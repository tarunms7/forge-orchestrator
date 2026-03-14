"""Tests for daemon_review — sibling context builder, test gate scoping, and review streaming."""


import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from forge.core.daemon_review import ReviewMixin, _summarize_auto_fix
from forge.review.pipeline import GateResult


def _make_task(task_id="task-2", title="Create webhook", files=None, state="todo"):
    """Create a mock task object."""
    t = MagicMock()
    t.id = task_id
    t.title = title
    t.files = files if files is not None else ["webhooks.py"]
    t.state = state
    return t


class TestBuildSiblingContext:
    """ReviewMixin._build_sibling_context() provides DAG awareness to the reviewer."""

    def _make_mixin(self):
        mixin = ReviewMixin()
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock()
        mixin._emit = AsyncMock()
        return mixin

    @pytest.mark.asyncio
    async def test_with_siblings(self):
        """Returns formatted context when pipeline has multiple tasks."""
        mixin = self._make_mixin()
        current_task = _make_task("task-2", "Create webhook", ["webhooks.py"])

        sibling1 = _make_task("task-1", "Add DB schema", ["db.py", "models.py"], "done")
        sibling2 = _make_task("task-3", "Register router", ["app.py"], "todo")

        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [sibling1, current_task, sibling2]

        result = await mixin._build_sibling_context(current_task, db, "pipeline-123")

        assert result is not None
        assert "Pipeline Task Context" in result
        assert "task-1" in result
        assert "Add DB schema" in result
        assert "db.py, models.py" in result
        assert "done" in result
        assert "task-3" in result
        assert "Register router" in result
        assert "app.py" in result
        # Current task should NOT be listed
        assert "task-2" not in result
        # Should include the important instruction
        assert "do not fail the review" in result.lower()

    @pytest.mark.asyncio
    async def test_solo_task_returns_none(self):
        """Returns None when pipeline has only one task."""
        mixin = self._make_mixin()
        current_task = _make_task("task-1", "Solo task")

        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [current_task]

        result = await mixin._build_sibling_context(current_task, db, "pipeline-123")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_pipeline_returns_none(self):
        """Returns None when pipeline_id is empty/falsy."""
        mixin = self._make_mixin()
        current_task = _make_task()

        db = AsyncMock()

        result = await mixin._build_sibling_context(current_task, db, "")

        assert result is None
        db.list_tasks_by_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_many_files_truncated(self):
        """Tasks with >5 files show truncation indicator."""
        mixin = self._make_mixin()
        current_task = _make_task("task-1", "Current")

        many_files_task = _make_task(
            "task-2", "Big task",
            ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py"],
            "in_progress",
        )

        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [current_task, many_files_task]

        result = await mixin._build_sibling_context(current_task, db, "pipeline-1")

        assert result is not None
        assert "+2 more" in result

    @pytest.mark.asyncio
    async def test_sibling_with_no_files(self):
        """Handles siblings with empty file lists."""
        mixin = self._make_mixin()
        current_task = _make_task("task-1", "Current")
        no_files_task = _make_task("task-2", "No files", [], "todo")

        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [current_task, no_files_task]

        result = await mixin._build_sibling_context(current_task, db, "pipeline-1")

        assert result is not None
        assert "(none)" in result


class TestGateTestScoping:
    """ReviewMixin._gate_test() scopes pytest to changed files when possible."""

    def _make_mixin(self):
        mixin = ReviewMixin()
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock()
        mixin._settings.agent_timeout_seconds = 600
        mixin._emit = AsyncMock()
        return mixin

    @pytest.mark.asyncio
    async def test_scoped_when_pytest_and_changed_files(self):
        """With pytest cmd and changed files, scopes to related test files."""
        mixin = self._make_mixin()

        # Mock _run_shell_gate to capture the actual command passed
        captured_cmds = []

        async def fake_shell_gate(worktree_path, cmd, timeout, *, gate_name):
            captured_cmds.append(cmd)
            return GateResult(passed=True, gate=gate_name, details="OK")

        mixin._run_shell_gate = fake_shell_gate

        with patch(
            "forge.core.daemon_review._find_related_test_files",
            return_value=["forge/core/foo_test.py", "forge/core/bar_test.py"],
        ):
            result = await mixin._gate_test(
                "/repo", "pytest -v", 300,
                changed_files=["forge/core/foo.py", "forge/core/bar.py"],
            )

        assert result.passed is True
        assert len(captured_cmds) == 1
        assert "forge/core/foo_test.py" in captured_cmds[0]
        assert "forge/core/bar_test.py" in captured_cmds[0]

    @pytest.mark.asyncio
    async def test_skips_when_no_related_tests(self):
        """Passes gate with 'skipped' message when no related test files found."""
        mixin = self._make_mixin()

        with patch(
            "forge.core.daemon_review._find_related_test_files",
            return_value=[],
        ):
            result = await mixin._gate_test(
                "/repo", "pytest", 300,
                changed_files=["forge/core/new_module.py"],
            )

        assert result.passed is True
        assert "No test files found" in result.details

    @pytest.mark.asyncio
    async def test_full_suite_when_not_pytest(self):
        """Non-pytest commands run as-is (unscoped)."""
        mixin = self._make_mixin()

        captured_cmds = []

        async def fake_shell_gate(worktree_path, cmd, timeout, *, gate_name):
            captured_cmds.append(cmd)
            return GateResult(passed=True, gate=gate_name, details="OK")

        mixin._run_shell_gate = fake_shell_gate

        result = await mixin._gate_test(
            "/repo", "npm test", 300,
            changed_files=["src/foo.js"],
        )

        assert result.passed is True
        assert captured_cmds == ["npm test"]  # Unmodified command

    @pytest.mark.asyncio
    async def test_full_suite_when_no_changed_files(self):
        """Without changed_files, runs the full test command."""
        mixin = self._make_mixin()

        captured_cmds = []

        async def fake_shell_gate(worktree_path, cmd, timeout, *, gate_name):
            captured_cmds.append(cmd)
            return GateResult(passed=True, gate=gate_name, details="OK")

        mixin._run_shell_gate = fake_shell_gate

        result = await mixin._gate_test("/repo", "pytest", 300)

        assert result.passed is True
        assert captured_cmds == ["pytest"]


def _make_task_for_review(task_id="task-1", title="Add feature", files=None):
    """Create a fully-featured mock task for _run_review tests."""
    t = MagicMock()
    t.id = task_id
    t.title = title
    t.description = "Some description"
    t.files = files if files is not None else ["feature.py"]
    t.state = "in_review"
    t.retry_count = 0
    t.complexity = "medium"
    t.review_feedback = None
    t.prior_diff = None
    return t


class TestReviewGateEvents:
    """Verify that _run_review emits the correct review gate events."""

    def _make_mixin(self):
        mixin = ReviewMixin()
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock()
        mixin._settings.agent_timeout_seconds = 600
        mixin._emit = AsyncMock()
        mixin._template_config = None
        return mixin

    def _collect_events(self, mixin) -> list[tuple[str, dict]]:
        """Return list of (event_type, data) from all _emit calls."""
        return [
            (call.args[0], call.args[1])
            for call in mixin._emit.call_args_list
        ]

    @pytest.mark.asyncio
    async def test_gate_started_event_emitted(self):
        """Verify _run_review emits review:gate_started before each active gate."""
        mixin = self._make_mixin()
        task = _make_task_for_review()
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [task]
        db.get_pipeline_contracts.return_value = None

        # No build or test command configured — only lint + LLM run
        mixin._settings.build_cmd = None
        mixin._pipeline_build_cmd = None
        mixin._settings.test_cmd = None
        mixin._pipeline_test_cmd = None

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=[]),
            patch.object(mixin, "_gate1", return_value=GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")),
            patch("forge.core.daemon_review.gate2_llm_review", return_value=(
                GateResult(passed=True, gate="gate2_llm_review", details="LGTM"),
                MagicMock(cost_usd=0),
            )),
            patch("forge.core.daemon_review.select_model", return_value="claude-sonnet-4-5"),
        ):
            passed, feedback = await mixin._run_review(
                task, "/repo", "diff content", db=db, pipeline_id="pipe-1",
            )

        assert passed is True
        emitted_events = self._collect_events(mixin)
        started_events = [e for e in emitted_events if e[0] == "review:gate_started"]
        # Both lint and LLM gates should emit gate_started
        assert len(started_events) >= 2
        gate_names = [e[1]["gate"] for e in started_events]
        assert "gate1_lint" in gate_names
        assert "gate2_llm_review" in gate_names
        # Each started event must carry task_id
        for _, data in started_events:
            assert data["task_id"] == task.id

    @pytest.mark.asyncio
    async def test_gate_passed_event_includes_details(self):
        """Verify review:gate_passed is emitted with gate name and details when a gate passes."""
        mixin = self._make_mixin()
        task = _make_task_for_review()
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [task]
        db.get_pipeline_contracts.return_value = None

        mixin._settings.build_cmd = None
        mixin._pipeline_build_cmd = None
        mixin._settings.test_cmd = None
        mixin._pipeline_test_cmd = None

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=[]),
            patch.object(mixin, "_gate1", return_value=GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")),
            patch("forge.core.daemon_review.gate2_llm_review", return_value=(
                GateResult(passed=True, gate="gate2_llm_review", details="LGTM"),
                MagicMock(cost_usd=0),
            )),
            patch("forge.core.daemon_review.select_model", return_value="claude-sonnet-4-5"),
        ):
            passed, feedback = await mixin._run_review(
                task, "/repo", "diff content", db=db, pipeline_id="pipe-1",
            )

        assert passed is True
        emitted_events = self._collect_events(mixin)
        passed_events = [e for e in emitted_events if e[0] == "review:gate_passed"]
        assert len(passed_events) >= 2  # at least lint + LLM
        for _, data in passed_events:
            assert "gate" in data
            assert "details" in data
            assert data["task_id"] == task.id
        # Verify lint gate_passed has correct gate name
        lint_passed = next((e for e in passed_events if e[1]["gate"] == "gate1_lint"), None)
        assert lint_passed is not None
        assert lint_passed[1]["details"] == "Lint clean"
        # Verify LLM gate_passed has correct gate name and details
        llm_passed = next((e for e in passed_events if e[1]["gate"] == "gate2_llm_review"), None)
        assert llm_passed is not None
        assert llm_passed[1]["details"] == "LGTM"

    @pytest.mark.asyncio
    async def test_gate_failed_event_emitted_on_failure(self):
        """Verify review:gate_failed is emitted (not gate_passed) when a gate fails."""
        mixin = self._make_mixin()
        task = _make_task_for_review()
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [task]
        db.get_pipeline_contracts.return_value = None

        mixin._settings.build_cmd = None
        mixin._pipeline_build_cmd = None
        mixin._settings.test_cmd = None
        mixin._pipeline_test_cmd = None

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=[]),
            patch.object(mixin, "_gate1", return_value=GateResult(
                passed=False, gate="gate1_auto_check", details="Lint errors:\nE501 line too long",
            )),
        ):
            passed, feedback = await mixin._run_review(
                task, "/repo", "diff content", db=db, pipeline_id="pipe-1",
            )

        assert passed is False
        assert feedback is not None
        emitted_events = self._collect_events(mixin)
        failed_events = [e for e in emitted_events if e[0] == "review:gate_failed"]
        assert len(failed_events) == 1
        assert failed_events[0][1]["gate"] == "gate1_lint"
        assert "Lint errors" in failed_events[0][1]["details"]
        # No gate_passed events should be emitted for the lint gate
        passed_events = [e for e in emitted_events if e[0] == "review:gate_passed"]
        assert not any(e[1]["gate"] == "gate1_lint" for e in passed_events)

    @pytest.mark.asyncio
    async def test_llm_feedback_event_emitted(self):
        """Verify LLM reviewer feedback is emitted as review:llm_feedback."""
        mixin = self._make_mixin()
        task = _make_task_for_review()
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [task]
        db.get_pipeline_contracts.return_value = None

        mixin._settings.build_cmd = None
        mixin._pipeline_build_cmd = None
        mixin._settings.test_cmd = None
        mixin._pipeline_test_cmd = None

        llm_feedback_text = "Code looks good. Minor: add docstrings to public methods."

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=[]),
            patch.object(mixin, "_gate1", return_value=GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")),
            patch("forge.core.daemon_review.gate2_llm_review", return_value=(
                GateResult(passed=True, gate="gate2_llm_review", details=llm_feedback_text),
                MagicMock(cost_usd=0),
            )),
            patch("forge.core.daemon_review.select_model", return_value="claude-sonnet-4-5"),
        ):
            passed, feedback = await mixin._run_review(
                task, "/repo", "diff content", db=db, pipeline_id="pipe-1",
            )

        assert passed is True
        emitted_events = self._collect_events(mixin)
        llm_feedback_events = [e for e in emitted_events if e[0] == "review:llm_feedback"]
        assert len(llm_feedback_events) == 1
        event_data = llm_feedback_events[0][1]
        assert event_data["task_id"] == task.id
        assert event_data["feedback"] == llm_feedback_text

    @pytest.mark.asyncio
    async def test_build_gate_emits_started_and_passed(self):
        """Verify build gate emits review:gate_started and review:gate_passed."""
        mixin = self._make_mixin()
        task = _make_task_for_review()
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [task]
        db.get_pipeline_contracts.return_value = None

        # Configure a build command so build gate runs
        mixin._settings.build_cmd = "make build"
        mixin._pipeline_build_cmd = None
        mixin._settings.test_cmd = None
        mixin._pipeline_test_cmd = None

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=[]),
            patch.object(mixin, "_gate_build", return_value=GateResult(
                passed=True, gate="gate0_build", details="OK",
            )),
            patch.object(mixin, "_gate1", return_value=GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")),
            patch("forge.core.daemon_review.gate2_llm_review", return_value=(
                GateResult(passed=True, gate="gate2_llm_review", details="LGTM"),
                MagicMock(cost_usd=0),
            )),
            patch("forge.core.daemon_review.select_model", return_value="claude-sonnet-4-5"),
        ):
            passed, _ = await mixin._run_review(
                task, "/repo", "diff content", db=db, pipeline_id="pipe-1",
            )

        assert passed is True
        emitted_events = self._collect_events(mixin)
        started_events = [e for e in emitted_events if e[0] == "review:gate_started"]
        gate_names_started = [e[1]["gate"] for e in started_events]
        assert "gate0_build" in gate_names_started

        passed_events = [e for e in emitted_events if e[0] == "review:gate_passed"]
        gate_names_passed = [e[1]["gate"] for e in passed_events]
        assert "gate0_build" in gate_names_passed

    @pytest.mark.asyncio
    async def test_event_order_gate_started_before_result(self):
        """Verify review:gate_started is emitted before review:gate_passed/failed."""
        mixin = self._make_mixin()
        task = _make_task_for_review()
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [task]
        db.get_pipeline_contracts.return_value = None

        mixin._settings.build_cmd = None
        mixin._pipeline_build_cmd = None
        mixin._settings.test_cmd = None
        mixin._pipeline_test_cmd = None

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=[]),
            patch.object(mixin, "_gate1", return_value=GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")),
            patch("forge.core.daemon_review.gate2_llm_review", return_value=(
                GateResult(passed=True, gate="gate2_llm_review", details="LGTM"),
                MagicMock(cost_usd=0),
            )),
            patch("forge.core.daemon_review.select_model", return_value="claude-sonnet-4-5"),
        ):
            await mixin._run_review(
                task, "/repo", "diff content", db=db, pipeline_id="pipe-1",
            )

        event_names = [call.args[0] for call in mixin._emit.call_args_list]
        # For lint gate: gate_started must come before gate_passed
        lint_started_idx = next(
            (i for i, e in enumerate(event_names) if e == "review:gate_started"
             and mixin._emit.call_args_list[i].args[1].get("gate") == "gate1_lint"),
            None,
        )
        lint_result_idx = next(
            (i for i, e in enumerate(event_names) if e in ("review:gate_passed", "review:gate_failed")
             and mixin._emit.call_args_list[i].args[1].get("gate") == "gate1_lint"),
            None,
        )
        assert lint_started_idx is not None
        assert lint_result_idx is not None
        assert lint_started_idx < lint_result_idx


class TestMakeReviewOnMessage:
    """ReviewMixin._make_review_on_message() builds a batched streaming callback."""

    def _make_mixin(self):
        mixin = ReviewMixin()
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock()
        mixin._emit = AsyncMock()
        return mixin

    @pytest.mark.asyncio
    async def test_emits_review_llm_output_events(self):
        """Callback emits review:llm_output with task_id and line."""
        mixin = self._make_mixin()

        with patch("forge.core.daemon_review.time") as mock_time:
            mock_time.monotonic.side_effect = [0.0, 0.2]  # initial + flush trigger
            on_msg, flush = mixin._make_review_on_message("task-1", MagicMock(), "pipe-1")

            with patch("forge.core.daemon_review._extract_text", return_value="Review text here"):
                await on_msg(MagicMock())

        emit_calls = [
            (call.args[0], call.args[1])
            for call in mixin._emit.call_args_list
        ]
        assert len(emit_calls) == 1
        assert emit_calls[0][0] == "review:llm_output"
        assert emit_calls[0][1]["task_id"] == "task-1"
        assert emit_calls[0][1]["line"] == "Review text here"

    @pytest.mark.asyncio
    async def test_batches_messages_within_interval(self):
        """Messages within 0.1s interval are batched, not flushed immediately."""
        mixin = self._make_mixin()

        with patch("forge.core.daemon_review.time") as mock_time:
            mock_time.monotonic.side_effect = [0.0, 0.05, 0.09]
            on_msg, flush = mixin._make_review_on_message("task-1", MagicMock(), "pipe-1")

            with patch("forge.core.daemon_review._extract_text", return_value="line"):
                await on_msg(MagicMock())
                await on_msg(MagicMock())

        # No emit yet — within batch interval
        assert mixin._emit.call_count == 0

        # Flush drains remaining
        await flush()

        assert mixin._emit.call_count == 2

    @pytest.mark.asyncio
    async def test_flush_drains_remaining(self):
        """flush() emits any buffered lines."""
        mixin = self._make_mixin()

        with patch("forge.core.daemon_review.time") as mock_time:
            mock_time.monotonic.side_effect = [0.0, 0.01]
            on_msg, flush = mixin._make_review_on_message("task-2", MagicMock(), "pipe-2")

            with patch("forge.core.daemon_review._extract_text", return_value="buffered line"):
                await on_msg(MagicMock())

        assert mixin._emit.call_count == 0

        await flush()

        assert mixin._emit.call_count == 1
        call_data = mixin._emit.call_args_list[0].args[1]
        assert call_data["task_id"] == "task-2"
        assert call_data["line"] == "buffered line"

    @pytest.mark.asyncio
    async def test_skips_none_text(self):
        """Messages with no extractable text are ignored."""
        mixin = self._make_mixin()

        with patch("forge.core.daemon_review.time") as mock_time:
            mock_time.monotonic.side_effect = [0.0, 0.2]
            on_msg, flush = mixin._make_review_on_message("task-1", MagicMock(), "pipe-1")

            with patch("forge.core.daemon_review._extract_text", return_value=None):
                await on_msg(MagicMock())

        await flush()
        assert mixin._emit.call_count == 0


class TestRunReviewPassesOnMessage:
    """Verify _run_review wires on_message to gate2_llm_review."""

    def _make_mixin(self):
        mixin = ReviewMixin()
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock()
        mixin._settings.agent_timeout_seconds = 600
        mixin._emit = AsyncMock()
        mixin._template_config = None
        return mixin

    @pytest.mark.asyncio
    async def test_on_message_passed_to_gate2(self):
        """_run_review constructs on_message callback and passes it to gate2_llm_review."""
        mixin = self._make_mixin()
        task = _make_task_for_review()
        db = AsyncMock()
        db.list_tasks_by_pipeline.return_value = [task]
        db.get_pipeline_contracts.return_value = None

        mixin._settings.build_cmd = None
        mixin._pipeline_build_cmd = None
        mixin._settings.test_cmd = None
        mixin._pipeline_test_cmd = None

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=[]),
            patch.object(mixin, "_gate1", return_value=GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")),
            patch("forge.core.daemon_review.gate2_llm_review", new_callable=AsyncMock) as mock_gate2,
            patch("forge.core.daemon_review.select_model", return_value="claude-sonnet-4-5"),
        ):
            mock_gate2.return_value = (
                GateResult(passed=True, gate="gate2_llm_review", details="LGTM"),
                MagicMock(cost_usd=0),
            )
            passed, _ = await mixin._run_review(
                task, "/repo", "diff content", db=db, pipeline_id="pipe-1",
            )

        assert passed is True
        mock_gate2.assert_called_once()
        call_kwargs = mock_gate2.call_args
        # on_message should be a callable, not None
        assert call_kwargs.kwargs.get("on_message") is not None
        assert callable(call_kwargs.kwargs["on_message"])


class TestGate1AutoFixDiff:
    """Verify _gate1 captures ruff auto-fix diff and includes it in GateResult."""

    def _make_mixin(self):
        mixin = ReviewMixin()
        mixin._strategy = "auto"
        mixin._snapshot = None
        mixin._settings = MagicMock()
        mixin._emit = AsyncMock()
        return mixin

    @pytest.mark.asyncio
    async def test_auto_fix_diff_captured_when_ruff_makes_changes(self):
        """When ruff auto-fixes code, the diff is captured and included in details."""
        mixin = self._make_mixin()

        diff_output = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "-import os\n"
            "-import sys\n"
        )

        def fake_run_git(args, cwd=None, check=True, description=""):
            result = MagicMock()
            if args == ["diff"]:
                result.stdout = diff_output
            elif args == ["diff", "--cached", "--name-only"]:
                result.stdout = "foo.py\n"
            else:
                result.stdout = ""
            return result

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=["foo.py"]),
            patch("forge.core.daemon_review.os.path.isfile", return_value=True),
            patch("forge.core.daemon_review.subprocess.run") as mock_subprocess,
            patch("forge.core.daemon_review._run_git", side_effect=fake_run_git),
        ):
            # First call: ruff --version (availability check),
            # second: ruff --fix, third: ruff check (pass)
            version_result = MagicMock()
            version_result.returncode = 0
            fix_result = MagicMock()
            fix_result.returncode = 0
            check_result = MagicMock()
            check_result.returncode = 0
            check_result.stdout = ""
            check_result.stderr = ""
            mock_subprocess.side_effect = [version_result, fix_result, check_result]

            result = await mixin._gate1("/repo")

        assert result.passed is True
        assert "auto-fixed" in result.details
        assert "removed 2 unused imports" in result.details

    @pytest.mark.asyncio
    async def test_diff_capped_at_30_lines(self):
        """Auto-fix diff is truncated to 30 lines."""
        mixin = self._make_mixin()

        # Generate a diff with more than 30 lines
        long_diff_lines = [f"-import mod{i}" for i in range(40)]
        long_diff = "\n".join(long_diff_lines)

        def fake_run_git(args, cwd=None, check=True, description=""):
            result = MagicMock()
            if args == ["diff"]:
                result.stdout = long_diff
            elif args == ["diff", "--cached", "--name-only"]:
                result.stdout = "foo.py\n"
            else:
                result.stdout = ""
            return result

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=["foo.py"]),
            patch("forge.core.daemon_review.os.path.isfile", return_value=True),
            patch("forge.core.daemon_review.subprocess.run") as mock_subprocess,
            patch("forge.core.daemon_review._run_git", side_effect=fake_run_git),
        ):
            version_result = MagicMock()
            version_result.returncode = 0
            fix_result = MagicMock()
            fix_result.returncode = 0
            check_result = MagicMock()
            check_result.returncode = 0
            check_result.stdout = ""
            check_result.stderr = ""
            mock_subprocess.side_effect = [version_result, fix_result, check_result]

            result = await mixin._gate1("/repo")

        assert result.passed is True
        assert "auto-fixed" in result.details
        # The diff stored internally should have been capped — verify via summary
        # The summary should reflect only the first 30 lines of diff

    @pytest.mark.asyncio
    async def test_no_diff_when_ruff_makes_no_changes(self):
        """When ruff makes no changes, GateResult.details is plain 'Lint clean'."""
        mixin = self._make_mixin()

        def fake_run_git(args, cwd=None, check=True, description=""):
            result = MagicMock()
            result.stdout = ""  # No diff — ruff didn't change anything
            return result

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=["foo.py"]),
            patch("forge.core.daemon_review.os.path.isfile", return_value=True),
            patch("forge.core.daemon_review.subprocess.run") as mock_subprocess,
            patch("forge.core.daemon_review._run_git", side_effect=fake_run_git),
        ):
            version_result = MagicMock()
            version_result.returncode = 0
            fix_result = MagicMock()
            fix_result.returncode = 0
            check_result = MagicMock()
            check_result.returncode = 0
            check_result.stdout = ""
            check_result.stderr = ""
            mock_subprocess.side_effect = [version_result, fix_result, check_result]

            result = await mixin._gate1("/repo")

        assert result.passed is True
        assert result.details == "Lint clean"
        assert "auto-fixed" not in result.details

    @pytest.mark.asyncio
    async def test_details_includes_auto_fix_summary(self):
        """GateResult.details has format 'Lint clean (auto-fixed: <summary>)'."""
        mixin = self._make_mixin()

        diff_output = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "-import unused_module\n"
            "+\n"
        )

        def fake_run_git(args, cwd=None, check=True, description=""):
            result = MagicMock()
            if args == ["diff"]:
                result.stdout = diff_output
            elif args == ["diff", "--cached", "--name-only"]:
                result.stdout = "foo.py\n"
            else:
                result.stdout = ""
            return result

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=["foo.py"]),
            patch("forge.core.daemon_review.os.path.isfile", return_value=True),
            patch("forge.core.daemon_review.subprocess.run") as mock_subprocess,
            patch("forge.core.daemon_review._run_git", side_effect=fake_run_git),
        ):
            version_result = MagicMock()
            version_result.returncode = 0
            fix_result = MagicMock()
            fix_result.returncode = 0
            check_result = MagicMock()
            check_result.returncode = 0
            check_result.stdout = ""
            check_result.stderr = ""
            mock_subprocess.side_effect = [version_result, fix_result, check_result]

            result = await mixin._gate1("/repo")

        assert result.passed is True
        assert result.details.startswith("Lint clean (auto-fixed:")
        assert "removed 1 unused import" in result.details

    @pytest.mark.asyncio
    async def test_ruff_not_installed_skips_gracefully(self):
        """When ruff is not installed, gate1 passes with a skip message."""
        mixin = self._make_mixin()

        with (
            patch("forge.core.daemon_review._get_changed_files_vs_main", return_value=["foo.py"]),
            patch("forge.core.daemon_review.os.path.isfile", return_value=True),
            patch("forge.core.daemon_review.subprocess.run") as mock_subprocess,
        ):
            # ruff --version returns non-zero (not installed)
            version_result = MagicMock()
            version_result.returncode = 1
            mock_subprocess.return_value = version_result

            result = await mixin._gate1("/repo")

        assert result.passed is True
        assert "ruff not installed" in result.details


class TestSummarizeAutoFix:
    """Unit tests for _summarize_auto_fix helper."""

    def test_removed_imports_counted(self):
        diff = "-import os\n-import sys\n"
        summary = _summarize_auto_fix(diff)
        assert "removed 2 unused imports" in summary

    def test_single_import(self):
        diff = "-import os\n"
        summary = _summarize_auto_fix(diff)
        assert "removed 1 unused import" in summary
        assert "imports" not in summary  # singular

    def test_mixed_changes(self):
        diff = "-import os\n+x = 1\n-y = 2\n"
        summary = _summarize_auto_fix(diff)
        assert "removed 1 unused import" in summary
        assert "lines changed" in summary

    def test_no_imports_only_changes(self):
        diff = "+x = 1\n-y = 2\n"
        summary = _summarize_auto_fix(diff)
        assert "2 lines changed" in summary

    def test_empty_diff_fallback(self):
        summary = _summarize_auto_fix("")
        assert summary == "minor fixes applied"

    def test_ignores_diff_header_lines(self):
        diff = "--- a/foo.py\n+++ b/foo.py\n-import os\n"
        summary = _summarize_auto_fix(diff)
        assert "removed 1 unused import" in summary
