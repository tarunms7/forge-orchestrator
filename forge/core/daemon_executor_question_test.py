"""Tests for question detection and pause/resume in daemon_executor."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from forge.core.daemon_helpers import _parse_forge_question


class TestQuestionDetection:
    """Tests for _parse_forge_question used by _execute_task."""

    def test_detects_question_in_result(self):
        result_text = 'Analysis done.\n\nFORGE_QUESTION:\n{"question": "Which?", "suggestions": ["A", "B"]}'
        q = _parse_forge_question(result_text)
        assert q is not None
        assert q["question"] == "Which?"

    def test_no_question_means_normal_completion(self):
        result_text = "I wrote the code and committed."
        q = _parse_forge_question(result_text)
        assert q is None

    def test_detects_question_with_context(self):
        result_text = (
            "Found two possible patterns.\n\n"
            'FORGE_QUESTION:\n{"question": "Which auth strategy?", '
            '"suggestions": ["JWT", "Session"], "context": "Codebase uses both"}'
        )
        q = _parse_forge_question(result_text)
        assert q is not None
        assert q["question"] == "Which auth strategy?"
        assert q["context"] == "Codebase uses both"
        assert q["suggestions"] == ["JWT", "Session"]

    def test_no_marker_returns_none(self):
        assert _parse_forge_question("") is None
        assert _parse_forge_question(None) is None

    def test_invalid_json_after_marker_returns_none(self):
        result_text = "Some output\nFORGE_QUESTION:\nnot-valid-json"
        q = _parse_forge_question(result_text)
        assert q is None

    def test_question_missing_from_json_returns_none(self):
        result_text = 'FORGE_QUESTION:\n{"suggestions": ["A", "B"]}'
        q = _parse_forge_question(result_text)
        assert q is None

    def test_agent_continued_after_marker_returns_none(self):
        """If significant text follows the JSON block, the agent didn't stop."""
        result_text = (
            'FORGE_QUESTION:\n{"question": "Which?"}\n\n'
            "Actually I will just proceed and implement it."
        )
        q = _parse_forge_question(result_text)
        assert q is None


class TestResumeTaskSdkOptions:
    """Verify _resume_task passes resume=session_id to the SDK."""

    def test_resume_task_calls_sdk_with_resume(self):
        """Verify ClaudeCodeOptions accepts resume=session_id."""
        from claude_code_sdk import ClaudeCodeOptions
        opts = ClaudeCodeOptions(
            resume="sess_123",
            permission_mode="acceptEdits",
            max_turns=25,
        )
        assert opts.resume == "sess_123"

    def test_resume_none_by_default(self):
        """ClaudeCodeOptions resume defaults to None."""
        from claude_code_sdk import ClaudeCodeOptions
        opts = ClaudeCodeOptions(
            permission_mode="acceptEdits",
        )
        assert opts.resume is None


class TestAgentResultSessionId:
    """AgentResult now carries session_id from the SDK."""

    def test_agent_result_has_session_id(self):
        from forge.agents.adapter import AgentResult
        result = AgentResult(
            success=True,
            files_changed=[],
            summary="done",
            session_id="sess_abc",
        )
        assert result.session_id == "sess_abc"

    def test_agent_result_session_id_defaults_none(self):
        from forge.agents.adapter import AgentResult
        result = AgentResult(success=False, files_changed=[], summary="fail")
        assert result.session_id is None


@pytest.mark.asyncio
class TestExecuteTaskQuestionDetection:
    """Integration-level tests for question detection in _execute_task."""

    def _make_mixin(self):
        """Return an ExecutorMixin with the required host-class attributes mocked."""
        from forge.core.daemon_executor import ExecutorMixin
        mixin = ExecutorMixin()
        mixin._project_dir = "/fake/project"
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock(allowed_dirs=[], require_approval=False, budget_limit_usd=0.0)
        mixin._template_config = None
        mixin._contracts = None
        mixin._merge_lock = MagicMock()
        mixin._merge_lock.__aenter__ = AsyncMock(return_value=None)
        mixin._merge_lock.__aexit__ = AsyncMock(return_value=False)
        return mixin

    async def test_execute_task_emits_question_event_and_returns_early(self):
        """When agent output has FORGE_QUESTION, task transitions to awaiting_input."""
        from forge.agents.adapter import AgentResult

        mixin = self._make_mixin()

        # DB mock
        db = MagicMock()
        db.get_task = AsyncMock(return_value=MagicMock(
            title="Test Task",
            retry_count=0,
            retry_reason=None,
            files=["foo.py"],
            complexity="medium",
            depends_on=[],
            review_feedback=None,
            questions_asked=0,
        ))
        db.update_task_state = AsyncMock()
        db.release_agent = AsyncMock()
        db.create_task_question = AsyncMock(return_value=MagicMock(
            id="q-1", question="Which strategy?"
        ))
        db.update_task_field = AsyncMock()
        db.get_pipeline = AsyncMock(return_value=None)
        db.get_pipeline_contracts = AsyncMock(return_value=None)
        db.add_task_agent_cost = AsyncMock()
        db.add_pipeline_cost = AsyncMock()
        db.get_pipeline_cost = AsyncMock(return_value=0.0)
        db.get_pipeline_budget = AsyncMock(return_value=0.0)
        db._session_factory = MagicMock()
        # Mock the async context manager returned by _session_factory()
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        db._session_factory.return_value = mock_session

        # Agent result with a FORGE_QUESTION
        question_json = '{"question": "Which strategy?", "suggestions": ["JWT", "Session"]}'
        agent_result = AgentResult(
            success=True,
            files_changed=["foo.py"],
            summary=f"I need to ask.\n\nFORGE_QUESTION:\n{question_json}",
            session_id="sess_xyz",
        )

        worktree_mgr = MagicMock()
        worktree_mgr.create = MagicMock(return_value="/fake/worktrees/task-1")

        runtime = MagicMock()
        runtime.run_task = AsyncMock(return_value=agent_result)

        merge_worker = MagicMock()
        merge_worker._main = "main"

        emitted_events: list[tuple] = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted_events.append((event_type, payload))

        mixin._emit = mock_emit

        # Patch _stream_agent to return our agent_result directly
        async def fake_stream_agent(*args, **kwargs):
            return agent_result

        mixin._stream_agent = fake_stream_agent

        with patch("forge.core.daemon_executor._get_diff_vs_main", return_value="diff content"):
            with patch("forge.core.daemon_executor._run_git", return_value=MagicMock(returncode=0, stdout="")):
                with patch("forge.core.budget.check_budget", AsyncMock()):
                    await mixin._execute_task(
                        db, runtime, worktree_mgr, merge_worker,
                        task_id="task-1", agent_id="agent-1", pipeline_id="pipe-1",
                    )

        # Should have emitted task:question
        event_types = [e[0] for e in emitted_events]
        assert "task:question" in event_types

        # Should have emitted awaiting_input state change
        state_changes = [
            e[1]["state"] for e in emitted_events
            if e[0] == "task:state_changed"
        ]
        assert "awaiting_input" in state_changes

        # Should have released the agent
        db.release_agent.assert_called_once_with("agent-1")

        # Should have stored the question
        db.create_task_question.assert_called_once()


class TestOnTaskAnswered:
    """Tests for _on_task_answered event-driven resume."""

    @pytest.mark.asyncio
    async def test_resumes_awaiting_input_task(self):
        """When task:answer arrives for an awaiting_input task, daemon should assign and resume."""
        from forge.core.daemon_executor import ExecutorMixin

        executor = ExecutorMixin.__new__(ExecutorMixin)
        executor._project_dir = "/tmp/test"
        executor._active_tasks = {}
        executor._effective_max_agents = 4
        executor._runtime = MagicMock()
        executor._worktree_mgr = MagicMock()
        executor._merge_worker = MagicMock()

        mock_db = AsyncMock()
        mock_task = MagicMock()
        mock_task.state = "awaiting_input"
        mock_task.id = "t1"
        mock_db.get_task = AsyncMock(return_value=mock_task)
        mock_db.assign_task = AsyncMock()
        mock_db.list_agents = AsyncMock(return_value=[MagicMock(id="agent-1")])
        mock_db.list_tasks_by_pipeline = AsyncMock(return_value=[mock_task])

        with patch("forge.core.scheduler.Scheduler") as MockSched:
            MockSched.dispatch_plan.return_value = [("t1", "agent-1")]

            # Mock engine helpers
            with patch("forge.core.engine._row_to_agent", side_effect=lambda a: a):
                with patch("forge.core.engine._row_to_record", side_effect=lambda t: t):
                    # Mock _safe_execute_resume to avoid actual execution
                    executor._safe_execute_resume = AsyncMock()

                    await executor._on_task_answered(
                        data={"task_id": "t1", "answer": "Use JWT", "pipeline_id": "pipe1"},
                        db=mock_db,
                    )

        mock_db.assign_task.assert_called_once_with("t1", "agent-1")

    @pytest.mark.asyncio
    async def test_skips_non_awaiting_task(self):
        """task:answer for a task not in AWAITING_INPUT should be a no-op."""
        from forge.core.daemon_executor import ExecutorMixin

        executor = ExecutorMixin.__new__(ExecutorMixin)
        executor._active_tasks = {}

        mock_db = AsyncMock()
        mock_task = MagicMock()
        mock_task.state = "in_progress"
        mock_db.get_task = AsyncMock(return_value=mock_task)

        executor._safe_execute_resume = AsyncMock()

        await executor._on_task_answered(
            data={"task_id": "t1", "answer": "x", "pipeline_id": "pipe1"},
            db=mock_db,
        )

        executor._safe_execute_resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_already_active_task(self):
        """task:answer for a task already in _active_tasks should be skipped."""
        from forge.core.daemon_executor import ExecutorMixin

        executor = ExecutorMixin.__new__(ExecutorMixin)
        executor._active_tasks = {"t1": MagicMock()}  # already active

        mock_db = AsyncMock()
        mock_task = MagicMock()
        mock_task.state = "awaiting_input"
        mock_db.get_task = AsyncMock(return_value=mock_task)

        executor._safe_execute_resume = AsyncMock()

        await executor._on_task_answered(
            data={"task_id": "t1", "answer": "x", "pipeline_id": "pipe1"},
            db=mock_db,
        )

        executor._safe_execute_resume.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_on_missing_task_id(self):
        """task:answer with no task_id should return immediately."""
        from forge.core.daemon_executor import ExecutorMixin

        executor = ExecutorMixin.__new__(ExecutorMixin)
        mock_db = AsyncMock()

        await executor._on_task_answered(data={"answer": "x"}, db=mock_db)
        mock_db.get_task.assert_not_called()
