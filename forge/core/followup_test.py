"""Tests for forge.core.followup — question classification and follow-up execution."""

from __future__ import annotations

import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

from forge.core.followup import (
    FollowUpExecution,
    FollowUpQuestion,
    FollowUpResult,
    FollowUpStatus,
    _build_followup_prompt,
    _execute_task_followup,
    _gather_task_context,
    classify_questions,
    execute_followups,
)

# ── classify_questions tests ──────────────────────────────────────────


class TestClassifyQuestions:
    """Tests for LLM-based question classification."""

    async def test_empty_questions_returns_empty(self):
        """No questions -> empty classification."""
        result = await classify_questions([], [{"id": "t1", "title": "Task 1"}])
        assert result == {}

    async def test_empty_tasks_returns_empty(self):
        """No tasks -> empty classification."""
        result = await classify_questions(
            [FollowUpQuestion(text="How?")],
            [],
        )
        assert result == {}

    async def test_single_task_maps_all_questions(self):
        """When there's only one task, all questions map to it."""
        questions = [
            FollowUpQuestion(text="Q1"),
            FollowUpQuestion(text="Q2"),
            FollowUpQuestion(text="Q3"),
        ]
        tasks = [{"id": "task-abc", "title": "Only Task", "description": "desc", "files": ["a.py"]}]

        result = await classify_questions(questions, tasks)

        assert result == {0: "task-abc", 1: "task-abc", 2: "task-abc"}

    @patch("forge.core.followup.sdk_query")
    async def test_llm_classification_parses_response(self, mock_sdk):
        """LLM returns valid JSON -> parsed correctly."""
        mock_result = MagicMock()
        mock_result.result = '{"0": "task-1", "1": "task-2"}'
        mock_sdk.return_value = mock_result

        questions = [
            FollowUpQuestion(text="Fix the auth bug"),
            FollowUpQuestion(text="Add more tests"),
        ]
        tasks = [
            {"id": "task-1", "title": "Auth Module", "description": "Auth", "files": ["auth.py"]},
            {"id": "task-2", "title": "Test Suite", "description": "Tests", "files": ["test.py"]},
        ]

        result = await classify_questions(questions, tasks)

        assert result == {0: "task-1", 1: "task-2"}
        mock_sdk.assert_called_once()

    @patch("forge.core.followup.sdk_query")
    async def test_llm_classification_handles_code_block(self, mock_sdk):
        """LLM wraps JSON in markdown code block -> still parsed."""
        mock_result = MagicMock()
        mock_result.result = '```json\n{"0": "task-1", "1": "task-1"}\n```'
        mock_sdk.return_value = mock_result

        questions = [FollowUpQuestion(text="Q1"), FollowUpQuestion(text="Q2")]
        tasks = [
            {"id": "task-1", "title": "T1", "description": "D1", "files": ["a.py"]},
            {"id": "task-2", "title": "T2", "description": "D2", "files": ["b.py"]},
        ]

        result = await classify_questions(questions, tasks)
        assert result == {0: "task-1", 1: "task-1"}

    @patch("forge.core.followup.sdk_query")
    async def test_llm_classification_invalid_task_id_falls_back(self, mock_sdk):
        """LLM returns invalid task ID -> falls back to first task."""
        mock_result = MagicMock()
        mock_result.result = '{"0": "task-1", "1": "nonexistent"}'
        mock_sdk.return_value = mock_result

        questions = [FollowUpQuestion(text="Q1"), FollowUpQuestion(text="Q2")]
        tasks = [
            {"id": "task-1", "title": "T1", "description": "D1", "files": ["a.py"]},
        ]

        result = await classify_questions(questions, tasks)
        # Index 0 maps to task-1 (valid), index 1 falls back to task-1 (first task)
        assert result[0] == "task-1"
        assert result[1] == "task-1"

    @patch("forge.core.followup.sdk_query")
    async def test_llm_failure_falls_back_to_first_task(self, mock_sdk):
        """When LLM call fails, all questions fall back to first task."""
        mock_sdk.side_effect = Exception("LLM unavailable")

        questions = [FollowUpQuestion(text="Q1"), FollowUpQuestion(text="Q2")]
        tasks = [
            {"id": "task-1", "title": "T1", "description": "D", "files": ["a.py"]},
            {"id": "task-2", "title": "T2", "description": "D", "files": ["b.py"]},
        ]

        result = await classify_questions(questions, tasks)
        assert result == {0: "task-1", 1: "task-1"}

    @patch("forge.core.followup.sdk_query")
    async def test_llm_returns_none_falls_back(self, mock_sdk):
        """When LLM returns None, fall back to first task."""
        mock_sdk.return_value = None

        questions = [FollowUpQuestion(text="Q1")]
        tasks = [
            {"id": "task-1", "title": "T1", "description": "D", "files": ["a.py"]},
            {"id": "task-2", "title": "T2", "description": "D", "files": ["b.py"]},
        ]

        result = await classify_questions(questions, tasks)
        assert result == {0: "task-1"}


# ── _build_followup_prompt tests ─────────────────────────────────────


class TestBuildFollowupPrompt:
    """Tests for prompt construction."""

    def test_builds_prompt_with_all_fields(self):
        """Prompt includes task info, output, feedback, and questions."""
        prompt = _build_followup_prompt(
            task_title="Auth Module",
            task_description="Implement JWT auth",
            task_files=["auth.py", "jwt.py"],
            original_output="## Agent Output\nImplemented JWT login flow",
            review_feedback="Add more error handling",
            questions=[
                FollowUpQuestion(text="Add refresh token support"),
                FollowUpQuestion(text="Fix the logout bug", context="Users can't log out"),
            ],
        )

        assert "Auth Module" in prompt
        assert "Implement JWT auth" in prompt
        assert "auth.py" in prompt
        assert "jwt.py" in prompt
        assert "Implemented JWT login flow" in prompt
        assert "Add more error handling" in prompt
        assert "Add refresh token support" in prompt
        assert "Fix the logout bug" in prompt
        assert "Users can't log out" in prompt

    def test_builds_prompt_without_optional_fields(self):
        """Prompt works without review feedback or question context."""
        prompt = _build_followup_prompt(
            task_title="Simple Task",
            task_description="Do something",
            task_files=[],
            original_output="(No prior output recorded)",
            review_feedback=None,
            questions=[FollowUpQuestion(text="How does this work?")],
        )

        assert "Simple Task" in prompt
        assert "Do something" in prompt
        assert "How does this work?" in prompt
        assert "Review Feedback" not in prompt

    def test_includes_question_numbers(self):
        """Each question is numbered."""
        prompt = _build_followup_prompt(
            task_title="T",
            task_description="D",
            task_files=["f.py"],
            original_output="output",
            review_feedback=None,
            questions=[
                FollowUpQuestion(text="First"),
                FollowUpQuestion(text="Second"),
                FollowUpQuestion(text="Third"),
            ],
        )

        assert "1. First" in prompt
        assert "2. Second" in prompt
        assert "3. Third" in prompt


# ── _gather_task_context tests ────────────────────────────────────────


class TestGatherTaskContext:
    """Tests for gathering original task context from DB events."""

    async def test_gathers_agent_output_lines(self):
        """Should collect agent output lines from events."""
        mock_db = AsyncMock()
        mock_events = [
            MagicMock(event_type="task:agent_output", payload={"line": "Line 1"}),
            MagicMock(event_type="task:agent_output", payload={"line": "Line 2"}),
        ]
        mock_db.list_events.return_value = mock_events

        result = await _gather_task_context("pipeline-1", "task-1", mock_db)

        assert "Line 1" in result
        assert "Line 2" in result
        mock_db.list_events.assert_called_once_with("pipeline-1", task_id="task-1")

    async def test_gathers_review_results(self):
        """Should collect review gate results from events."""
        mock_db = AsyncMock()
        mock_events = [
            MagicMock(
                event_type="task:review_update",
                payload={"gate": "lint", "passed": True, "details": "No issues"},
            ),
            MagicMock(
                event_type="task:review_update",
                payload={"gate": "tests", "passed": False, "details": "2 failures"},
            ),
        ]
        mock_db.list_events.return_value = mock_events

        result = await _gather_task_context("pipeline-1", "task-1", mock_db)

        assert "[PASS] lint" in result
        assert "[FAIL] tests" in result

    async def test_no_events_returns_placeholder(self):
        """Empty event list returns placeholder text."""
        mock_db = AsyncMock()
        mock_db.list_events.return_value = []

        result = await _gather_task_context("pipeline-1", "task-1", mock_db)

        assert result == "(No prior output recorded)"

    async def test_limits_output_lines(self):
        """Should limit output to last 100 lines."""
        mock_db = AsyncMock()
        mock_events = [
            MagicMock(event_type="task:agent_output", payload={"line": f"Line {i}"})
            for i in range(150)
        ]
        mock_db.list_events.return_value = mock_events

        result = await _gather_task_context("pipeline-1", "task-1", mock_db)

        # Should include line 50-149 (last 100), not line 0-49
        assert "Line 149" in result
        assert "Line 50" in result


# ── execute_followups tests ───────────────────────────────────────────


class TestExecuteFollowups:
    """Tests for the follow-up execution orchestrator."""

    @patch("forge.core.followup._execute_task_followup")
    async def test_executes_grouped_by_task(self, mock_execute):
        """Questions mapped to the same task should be grouped."""
        mock_execute.return_value = FollowUpResult(
            task_id="task-1",
            task_title="Auth",
            questions=[FollowUpQuestion(text="Q1"), FollowUpQuestion(text="Q2")],
            success=True,
            summary="Done",
            files_changed=["auth.py"],
        )

        followup = FollowUpExecution(
            id="fu-1",
            pipeline_id="pipe-1",
            status=FollowUpStatus.PENDING,
            questions=[
                FollowUpQuestion(text="Q1"),
                FollowUpQuestion(text="Q2"),
            ],
            classification={0: "task-1", 1: "task-1"},
        )

        pipeline_tasks = [
            {"id": "task-1", "title": "Auth", "description": "D", "files": ["auth.py"]}
        ]
        mock_pipeline = MagicMock(project_dir="/proj", id="pipe-1")
        mock_db = AsyncMock()

        result = await execute_followups(
            followup=followup,
            pipeline_tasks=pipeline_tasks,
            pipeline_db_tasks=[],
            pipeline=mock_pipeline,
            db=mock_db,
        )

        # Should call _execute_task_followup once (both questions grouped to task-1)
        mock_execute.assert_called_once()
        assert result.status == FollowUpStatus.COMPLETE
        assert len(result.results) == 1
        assert result.results[0].success is True

    @patch("forge.core.followup._execute_task_followup")
    async def test_executes_per_unique_task(self, mock_execute):
        """Questions mapped to different tasks should trigger separate executions."""

        async def fake_execute(**kwargs):
            return FollowUpResult(
                task_id=kwargs["task_id"],
                task_title=kwargs["task_info"].get("title", ""),
                questions=kwargs["questions"],
                success=True,
                summary="Done",
            )

        mock_execute.side_effect = fake_execute

        followup = FollowUpExecution(
            id="fu-2",
            pipeline_id="pipe-1",
            status=FollowUpStatus.PENDING,
            questions=[
                FollowUpQuestion(text="Q1"),
                FollowUpQuestion(text="Q2"),
            ],
            classification={0: "task-1", 1: "task-2"},
        )

        pipeline_tasks = [
            {"id": "task-1", "title": "Auth", "description": "D", "files": ["auth.py"]},
            {"id": "task-2", "title": "Tests", "description": "D", "files": ["test.py"]},
        ]
        mock_pipeline = MagicMock(project_dir="/proj", id="pipe-1")
        mock_db = AsyncMock()

        result = await execute_followups(
            followup=followup,
            pipeline_tasks=pipeline_tasks,
            pipeline_db_tasks=[],
            pipeline=mock_pipeline,
            db=mock_db,
        )

        assert mock_execute.call_count == 2
        assert len(result.results) == 2
        assert result.status == FollowUpStatus.COMPLETE

    @patch("forge.core.followup._execute_task_followup")
    async def test_handles_execution_failure(self, mock_execute):
        """If _execute_task_followup raises, the error is captured gracefully."""
        mock_execute.side_effect = RuntimeError("Agent crashed")

        followup = FollowUpExecution(
            id="fu-3",
            pipeline_id="pipe-1",
            status=FollowUpStatus.PENDING,
            questions=[FollowUpQuestion(text="Q1")],
            classification={0: "task-1"},
        )

        pipeline_tasks = [
            {"id": "task-1", "title": "Auth", "description": "D", "files": ["auth.py"]}
        ]
        mock_pipeline = MagicMock(project_dir="/proj", id="pipe-1")
        mock_db = AsyncMock()

        result = await execute_followups(
            followup=followup,
            pipeline_tasks=pipeline_tasks,
            pipeline_db_tasks=[],
            pipeline=mock_pipeline,
            db=mock_db,
        )

        assert result.status == FollowUpStatus.ERROR
        assert len(result.results) == 1
        assert result.results[0].success is False
        assert "Agent crashed" in result.results[0].error

    @patch("forge.core.followup._execute_task_followup")
    async def test_emits_events_during_execution(self, mock_execute):
        """Should emit events for task start, complete, and error."""
        mock_execute.return_value = FollowUpResult(
            task_id="task-1",
            task_title="Auth",
            questions=[FollowUpQuestion(text="Q1")],
            success=True,
            summary="Done",
        )

        emitter = MagicMock()
        emitter.emit = AsyncMock()

        followup = FollowUpExecution(
            id="fu-4",
            pipeline_id="pipe-1",
            status=FollowUpStatus.PENDING,
            questions=[FollowUpQuestion(text="Q1")],
            classification={0: "task-1"},
        )

        pipeline_tasks = [
            {"id": "task-1", "title": "Auth", "description": "D", "files": ["auth.py"]}
        ]
        mock_pipeline = MagicMock(project_dir="/proj", id="pipe-1")
        mock_db = AsyncMock()

        await execute_followups(
            followup=followup,
            pipeline_tasks=pipeline_tasks,
            pipeline_db_tasks=[],
            pipeline=mock_pipeline,
            db=mock_db,
            emitter=emitter,
        )

        # Should have emitted task_started and task_completed
        event_names = [call[0][0] for call in emitter.emit.call_args_list]
        assert "followup:task_started" in event_names
        assert "followup:task_completed" in event_names

    @patch("forge.core.followup._execute_task_followup")
    async def test_partial_success_still_completes(self, mock_execute):
        """If some tasks succeed and some fail, status is still COMPLETE."""
        call_count = 0

        async def alternating(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return FollowUpResult(
                    task_id=kwargs["task_id"],
                    task_title="T",
                    questions=[],
                    success=True,
                    summary="OK",
                )
            return FollowUpResult(
                task_id=kwargs["task_id"],
                task_title="T",
                questions=[],
                success=False,
                summary="Failed",
                error="Something broke",
            )

        mock_execute.side_effect = alternating

        followup = FollowUpExecution(
            id="fu-5",
            pipeline_id="pipe-1",
            status=FollowUpStatus.PENDING,
            questions=[FollowUpQuestion(text="Q1"), FollowUpQuestion(text="Q2")],
            classification={0: "task-1", 1: "task-2"},
        )

        pipeline_tasks = [
            {"id": "task-1", "title": "T1", "description": "D", "files": ["a.py"]},
            {"id": "task-2", "title": "T2", "description": "D", "files": ["b.py"]},
        ]
        mock_pipeline = MagicMock(project_dir="/proj", id="pipe-1")
        mock_db = AsyncMock()

        result = await execute_followups(
            followup=followup,
            pipeline_tasks=pipeline_tasks,
            pipeline_db_tasks=[],
            pipeline=mock_pipeline,
            db=mock_db,
        )

        assert result.status == FollowUpStatus.COMPLETE  # partial success


# ── FollowUpExecution dataclass tests ─────────────────────────────────


class TestFollowUpExecution:
    """Tests for the FollowUpExecution data structure."""

    def test_default_values(self):
        """Defaults are correct."""
        fu = FollowUpExecution(
            id="test",
            pipeline_id="pipe",
            status=FollowUpStatus.PENDING,
            questions=[],
        )
        assert fu.classification == {}
        assert fu.results == []
        assert fu.error is None
        assert fu.created_at is not None

    def test_status_enum_values(self):
        """Status enum has expected values."""
        assert FollowUpStatus.PENDING.value == "pending"
        assert FollowUpStatus.CLASSIFYING.value == "classifying"
        assert FollowUpStatus.EXECUTING.value == "executing"
        assert FollowUpStatus.COMPLETE.value == "complete"
        assert FollowUpStatus.ERROR.value == "error"


# ── Multi-repo follow-up tests ──────────────────────────────────────


class TestFollowupWorktreeMultiRepo:
    """Tests for multi-repo worktree path resolution."""

    @patch("forge.core.followup._cleanup_worktree")
    @patch("forge.core.followup.AgentRuntime")
    @patch("forge.core.followup.ClaudeAdapter")
    @patch("forge.core.followup._setup_worktree")
    @patch("forge.core.followup._gather_task_context", new_callable=AsyncMock)
    async def test_followup_worktree_multi_repo(
        self, mock_gather, mock_setup, mock_adapter, mock_runtime_cls, mock_cleanup
    ):
        """Multi-repo pipeline uses {project_dir}/.forge/worktrees/{repo_id}/{worktree_id}."""
        mock_gather.return_value = "(No prior output recorded)"

        mock_agent_result = MagicMock(
            success=True,
            summary="Done",
            files_changed=[],
            error=None,
            cost_usd=0.0,
        )
        mock_runtime = AsyncMock()
        mock_runtime.run_task.return_value = mock_agent_result
        mock_runtime_cls.return_value = mock_runtime

        # Pipeline with repos_json set (multi-repo)
        repos = [
            {
                "id": "backend",
                "path": "/workspace/backend",
                "base_branch": "main",
                "branch_name": "forge/pipe-123",
            },
            {
                "id": "frontend",
                "path": "/workspace/frontend",
                "base_branch": "main",
                "branch_name": "forge/pipe-123",
            },
        ]
        mock_pipeline = MagicMock()
        mock_pipeline.repos_json = json.dumps(repos)
        mock_pipeline.get_repos.return_value = repos

        mock_db = AsyncMock()
        mock_db.get_pipeline.return_value = mock_pipeline

        # Task in the "backend" repo
        task_info = {
            "id": "task-1",
            "title": "Auth",
            "description": "D",
            "files": ["auth.py"],
            "repo_id": "backend",
        }
        db_task = MagicMock(repo_id="backend")

        result = await _execute_task_followup(
            task_id="task-1",
            task_info=task_info,
            db_task=db_task,
            questions=[FollowUpQuestion(text="Fix it")],
            project_dir="/workspace",
            branch_name="forge/pipe-123",
            pipeline_id="pipe-123",
            db=mock_db,
            emitter=None,
            followup_id="fu-12345678",
        )

        assert result.success is True
        # Verify worktree path includes repo_id segment
        setup_call = mock_setup.call_args
        worktree_dir = setup_call[0][1]  # second positional arg
        assert "/backend/" in worktree_dir
        assert worktree_dir == os.path.join(
            "/workspace", ".forge", "worktrees", "backend", "followup-fu-12345-task-1"
        )
        # repo_dir should be the backend repo path
        repo_dir = setup_call[0][0]  # first positional arg
        assert repo_dir == "/workspace/backend"

    @patch("forge.core.followup._cleanup_worktree")
    @patch("forge.core.followup.AgentRuntime")
    @patch("forge.core.followup.ClaudeAdapter")
    @patch("forge.core.followup._setup_worktree")
    @patch("forge.core.followup._gather_task_context", new_callable=AsyncMock)
    async def test_followup_worktree_single_repo(
        self, mock_gather, mock_setup, mock_adapter, mock_runtime_cls, mock_cleanup
    ):
        """Single-repo pipeline uses {project_dir}/.forge/worktrees/{worktree_id} (no repo_id segment)."""
        mock_gather.return_value = "(No prior output recorded)"

        mock_agent_result = MagicMock(
            success=True,
            summary="Done",
            files_changed=[],
            error=None,
            cost_usd=0.0,
        )
        mock_runtime = AsyncMock()
        mock_runtime.run_task.return_value = mock_agent_result
        mock_runtime_cls.return_value = mock_runtime

        # Pipeline with repos_json=None (single-repo)
        mock_pipeline = MagicMock()
        mock_pipeline.repos_json = None
        mock_pipeline.project_dir = "/myproject"
        mock_pipeline.base_branch = "main"
        mock_pipeline.branch_name = "forge/pipe-abc"
        mock_pipeline.get_repos.return_value = [
            {
                "id": "default",
                "path": "/myproject",
                "base_branch": "main",
                "branch_name": "forge/pipe-abc",
            },
        ]

        mock_db = AsyncMock()
        mock_db.get_pipeline.return_value = mock_pipeline

        task_info = {"id": "task-1", "title": "Fix", "description": "D", "files": ["app.py"]}
        db_task = MagicMock(repo_id="default")

        result = await _execute_task_followup(
            task_id="task-1",
            task_info=task_info,
            db_task=db_task,
            questions=[FollowUpQuestion(text="Fix it")],
            project_dir="/myproject",
            branch_name="forge/pipe-abc",
            pipeline_id="pipe-abc",
            db=mock_db,
            emitter=None,
            followup_id="fu-abcdefgh",
        )

        assert result.success is True
        setup_call = mock_setup.call_args
        worktree_dir = setup_call[0][1]
        # Single-repo: no repo_id segment
        assert worktree_dir == os.path.join(
            "/myproject", ".forge", "worktrees", "followup-fu-abcde-task-1"
        )
        # repo_dir should be project_dir
        repo_dir = setup_call[0][0]
        assert repo_dir == "/myproject"


class TestFollowupPromptRepoContext:
    """Tests for repo context in follow-up prompts."""

    def test_followup_prompt_includes_repo_context(self):
        """Prompt should include repo name when it's not 'default'."""
        prompt = _build_followup_prompt(
            task_title="Auth Module",
            task_description="Implement auth",
            task_files=["auth.py"],
            original_output="Done",
            review_feedback=None,
            questions=[FollowUpQuestion(text="Fix it")],
            repo_name="backend",
        )

        assert "**Repository:** This task is in the **backend** repo." in prompt

    def test_followup_prompt_no_repo_context_for_default(self):
        """Prompt should NOT include repo section when repo_name is 'default'."""
        prompt = _build_followup_prompt(
            task_title="Auth Module",
            task_description="Implement auth",
            task_files=["auth.py"],
            original_output="Done",
            review_feedback=None,
            questions=[FollowUpQuestion(text="Fix it")],
            repo_name="default",
        )

        assert "Repository:" not in prompt

    def test_followup_prompt_no_repo_context_when_none(self):
        """Prompt should NOT include repo section when repo_name is None."""
        prompt = _build_followup_prompt(
            task_title="Auth Module",
            task_description="Implement auth",
            task_files=["auth.py"],
            original_output="Done",
            review_feedback=None,
            questions=[FollowUpQuestion(text="Fix it")],
            repo_name=None,
        )

        assert "Repository:" not in prompt


class TestFollowupCleanupMultiRepo:
    """Tests for cleanup with multi-repo repo_dir."""

    @patch("forge.core.followup._cleanup_worktree")
    @patch("forge.core.followup.AgentRuntime")
    @patch("forge.core.followup.ClaudeAdapter")
    @patch("forge.core.followup._setup_worktree")
    @patch("forge.core.followup._gather_task_context", new_callable=AsyncMock)
    async def test_followup_cleanup_multi_repo(
        self, mock_gather, mock_setup, mock_adapter, mock_runtime_cls, mock_cleanup
    ):
        """Cleanup should use repo_dir (not project_dir) for multi-repo."""
        mock_gather.return_value = "(No prior output recorded)"

        mock_agent_result = MagicMock(
            success=True,
            summary="Done",
            files_changed=[],
            error=None,
            cost_usd=0.0,
        )
        mock_runtime = AsyncMock()
        mock_runtime.run_task.return_value = mock_agent_result
        mock_runtime_cls.return_value = mock_runtime

        repos = [
            {
                "id": "backend",
                "path": "/ws/backend",
                "base_branch": "main",
                "branch_name": "forge/p1",
            },
        ]
        mock_pipeline = MagicMock()
        mock_pipeline.repos_json = json.dumps(repos)
        mock_pipeline.get_repos.return_value = repos

        mock_db = AsyncMock()
        mock_db.get_pipeline.return_value = mock_pipeline

        task_info = {
            "id": "task-1",
            "title": "T",
            "description": "D",
            "files": [],
            "repo_id": "backend",
        }

        await _execute_task_followup(
            task_id="task-1",
            task_info=task_info,
            db_task=MagicMock(repo_id="backend"),
            questions=[FollowUpQuestion(text="Q")],
            project_dir="/ws",
            branch_name="forge/p1",
            pipeline_id="p1",
            db=mock_db,
            emitter=None,
            followup_id="fu-12345678",
        )

        # Verify cleanup was called with repo_dir, not project_dir
        cleanup_call = mock_cleanup.call_args
        cleanup_repo_dir = cleanup_call[0][0]
        assert cleanup_repo_dir == "/ws/backend"


class TestFollowupPushCorrectRepo:
    """Tests for commit_and_push using the correct repo_dir."""

    @patch("forge.core.followup._cleanup_worktree")
    @patch("forge.core.followup._commit_and_push")
    @patch("forge.core.followup.AgentRuntime")
    @patch("forge.core.followup.ClaudeAdapter")
    @patch("forge.core.followup._setup_worktree")
    @patch("forge.core.followup._gather_task_context", new_callable=AsyncMock)
    async def test_followup_push_correct_repo(
        self, mock_gather, mock_setup, mock_adapter, mock_runtime_cls, mock_push, mock_cleanup
    ):
        """_commit_and_push should receive repo_dir (not project_dir) for multi-repo."""
        mock_gather.return_value = "(No prior output recorded)"

        mock_agent_result = MagicMock(
            success=True,
            summary="Done",
            files_changed=["auth.py"],
            error=None,
            cost_usd=0.0,
        )
        mock_runtime = AsyncMock()
        mock_runtime.run_task.return_value = mock_agent_result
        mock_runtime_cls.return_value = mock_runtime

        repos = [
            {
                "id": "backend",
                "path": "/ws/backend",
                "base_branch": "main",
                "branch_name": "forge/p1",
            },
        ]
        mock_pipeline = MagicMock()
        mock_pipeline.repos_json = json.dumps(repos)
        mock_pipeline.get_repos.return_value = repos

        mock_db = AsyncMock()
        mock_db.get_pipeline.return_value = mock_pipeline

        task_info = {
            "id": "task-1",
            "title": "Auth",
            "description": "D",
            "files": ["auth.py"],
            "repo_id": "backend",
        }

        await _execute_task_followup(
            task_id="task-1",
            task_info=task_info,
            db_task=MagicMock(repo_id="backend"),
            questions=[FollowUpQuestion(text="Q")],
            project_dir="/ws",
            branch_name="forge/p1",
            pipeline_id="p1",
            db=mock_db,
            emitter=None,
            followup_id="fu-12345678",
        )

        # Verify _commit_and_push was called with repo_dir=/ws/backend
        mock_push.assert_called_once()
        push_call = mock_push.call_args
        push_repo_dir = push_call[0][1]  # second positional arg
        assert push_repo_dir == "/ws/backend"


class TestFollowupMissingRepoIdFallback:
    """Tests for missing repo_id fallback behavior."""

    @patch("forge.core.followup._cleanup_worktree")
    @patch("forge.core.followup.AgentRuntime")
    @patch("forge.core.followup.ClaudeAdapter")
    @patch("forge.core.followup._setup_worktree")
    @patch("forge.core.followup._gather_task_context", new_callable=AsyncMock)
    async def test_followup_missing_repo_id_fallback(
        self, mock_gather, mock_setup, mock_adapter, mock_runtime_cls, mock_cleanup
    ):
        """When repo_id is missing from task_info and db_task, defaults to 'default'."""
        mock_gather.return_value = "(No prior output recorded)"

        mock_agent_result = MagicMock(
            success=True,
            summary="Done",
            files_changed=[],
            error=None,
            cost_usd=0.0,
        )
        mock_runtime = AsyncMock()
        mock_runtime.run_task.return_value = mock_agent_result
        mock_runtime_cls.return_value = mock_runtime

        # Single-repo pipeline
        mock_pipeline = MagicMock()
        mock_pipeline.repos_json = None
        mock_pipeline.project_dir = "/proj"
        mock_pipeline.base_branch = "main"
        mock_pipeline.branch_name = "forge/pipe-1"
        mock_pipeline.get_repos.return_value = [
            {
                "id": "default",
                "path": "/proj",
                "base_branch": "main",
                "branch_name": "forge/pipe-1",
            },
        ]

        mock_db = AsyncMock()
        mock_db.get_pipeline.return_value = mock_pipeline

        # task_info has NO repo_id, db_task has no repo_id attr
        task_info = {"id": "task-1", "title": "Fix", "description": "D", "files": ["app.py"]}
        db_task = MagicMock(spec=[])  # spec=[] means no attributes

        result = await _execute_task_followup(
            task_id="task-1",
            task_info=task_info,
            db_task=db_task,
            questions=[FollowUpQuestion(text="Fix")],
            project_dir="/proj",
            branch_name="forge/pipe-1",
            pipeline_id="pipe-1",
            db=mock_db,
            emitter=None,
            followup_id="fu-abcdefgh",
        )

        assert result.success is True
        # Should use single-repo worktree path (no repo_id segment)
        setup_call = mock_setup.call_args
        worktree_dir = setup_call[0][1]
        assert worktree_dir == os.path.join(
            "/proj", ".forge", "worktrees", "followup-fu-abcde-task-1"
        )


class TestFollowupUnknownRepoId:
    """Tests for unknown repo_id warning."""

    @patch("forge.core.followup._cleanup_worktree")
    @patch("forge.core.followup.AgentRuntime")
    @patch("forge.core.followup.ClaudeAdapter")
    @patch("forge.core.followup._setup_worktree")
    @patch("forge.core.followup._gather_task_context", new_callable=AsyncMock)
    async def test_followup_unknown_repo_id_logs_warning(
        self, mock_gather, mock_setup, mock_adapter, mock_runtime_cls, mock_cleanup, caplog
    ):
        """Unknown repo_id should log a warning and fall back to project_dir."""
        mock_gather.return_value = "(No prior output recorded)"

        mock_agent_result = MagicMock(
            success=True,
            summary="Done",
            files_changed=[],
            error=None,
            cost_usd=0.0,
        )
        mock_runtime = AsyncMock()
        mock_runtime.run_task.return_value = mock_agent_result
        mock_runtime_cls.return_value = mock_runtime

        # Multi-repo pipeline but task references unknown repo_id
        repos = [
            {
                "id": "backend",
                "path": "/ws/backend",
                "base_branch": "main",
                "branch_name": "forge/p1",
            },
        ]
        mock_pipeline = MagicMock()
        mock_pipeline.repos_json = json.dumps(repos)
        mock_pipeline.get_repos.return_value = repos

        mock_db = AsyncMock()
        mock_db.get_pipeline.return_value = mock_pipeline

        # Task references "unknown-repo" which doesn't exist in repos
        task_info = {
            "id": "task-1",
            "title": "T",
            "description": "D",
            "files": [],
            "repo_id": "unknown-repo",
        }

        with caplog.at_level(logging.WARNING, logger="forge.followup"):
            result = await _execute_task_followup(
                task_id="task-1",
                task_info=task_info,
                db_task=MagicMock(repo_id="unknown-repo"),
                questions=[FollowUpQuestion(text="Q")],
                project_dir="/ws",
                branch_name="forge/p1",
                pipeline_id="p1",
                db=mock_db,
                emitter=None,
                followup_id="fu-12345678",
            )

        assert result.success is True
        # Should have logged a warning about unknown repo_id
        assert any(
            "unknown-repo" in record.message and "not found" in record.message
            for record in caplog.records
        )
        # Should fall back to project_dir
        setup_call = mock_setup.call_args
        repo_dir = setup_call[0][0]
        assert repo_dir == "/ws"
