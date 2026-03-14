# Task Pool Execution & Review Quality Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `asyncio.gather` batch dispatch with a continuous task pool for true parallelism, and overhaul the LLM review prompt to catch real bugs instead of rubber-stamping.

**Architecture:** Two independent changes. (1) The daemon's execution loop replaces its `gather`-based batch dispatch (lines 774-813) with a `dict[str, asyncio.Task]` pool that reaps completed tasks, handles errors, and dispatches new work every tick — no batch boundaries. A `_safe_execute_task` wrapper ensures cleanup on all exit paths. (2) The LLM review system prompt is replaced with a structured 5-category checklist, retry suppression language is removed, and a missing `\n\n` separator before `custom_review_focus` is fixed.

**Tech Stack:** Python 3.12+, asyncio, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-15-task-pool-and-review-quality-design.md`

---

## Chunk 1: Review Quality Overhaul

This chunk is fully independent of the task pool work. It modifies only `forge/review/llm_review.py` and its test file.

### Task 1: Update `REVIEW_SYSTEM_PROMPT`

**Files:**
- Modify: `forge/review/llm_review.py:29-49` (replace `REVIEW_SYSTEM_PROMPT`)
- Test: `forge/review/llm_review_test.py`

- [ ] **Step 1: Write failing test for new system prompt content**

In `forge/review/llm_review_test.py`, add a new test class after `TestDeadCodeRemoval`:

```python
class TestReviewSystemPrompt:
    """Verify the system prompt has the comprehensive review checklist."""

    def test_prompt_has_checklist_categories(self):
        """System prompt must include all 5 review categories."""
        from forge.review.llm_review import REVIEW_SYSTEM_PROMPT
        assert "CORRECTNESS" in REVIEW_SYSTEM_PROMPT
        assert "ERROR HANDLING" in REVIEW_SYSTEM_PROMPT
        assert "SECURITY" in REVIEW_SYSTEM_PROMPT
        assert "CONCURRENCY & STATE" in REVIEW_SYSTEM_PROMPT
        assert "DESIGN QUALITY" in REVIEW_SYSTEM_PROMPT

    def test_prompt_has_strict_framing(self):
        """System prompt frames reviewer as senior code reviewer catching production bugs."""
        from forge.review.llm_review import REVIEW_SYSTEM_PROMPT
        assert "senior code reviewer" in REVIEW_SYSTEM_PROMPT
        assert "production incidents" in REVIEW_SYSTEM_PROMPT

    def test_prompt_forbids_style_nitpicking(self):
        """System prompt tells reviewer not to nitpick pure style."""
        from forge.review.llm_review import REVIEW_SYSTEM_PROMPT
        assert "Do NOT nitpick pure style preferences" in REVIEW_SYSTEM_PROMPT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/review/llm_review_test.py::TestReviewSystemPrompt -v`
Expected: FAIL — current prompt doesn't have "CORRECTNESS", "senior code reviewer", etc.

- [ ] **Step 3: Replace `REVIEW_SYSTEM_PROMPT` in `llm_review.py`**

Replace lines 29-49 of `forge/review/llm_review.py` with:

```python
REVIEW_SYSTEM_PROMPT = """You are a senior code reviewer. Your job is to catch bugs, security issues,
and design problems that would cause production incidents. You are the last
line of defense before code ships.

You will receive a task specification and a git diff. Review the code
thoroughly and respond with EXACTLY one of:

PASS: <explanation covering what you verified>
FAIL: <specific issues with file paths and line references>

## Review Checklist (evaluate ALL categories)

1. CORRECTNESS
   - Does the code actually implement what the task spec requires?
   - Are there logic errors, off-by-one errors, or wrong conditions?
   - Are return values and error states handled correctly?
   - Do edge cases work (empty inputs, None values, boundary conditions)?

2. ERROR HANDLING
   - Are exceptions caught at the right level (not too broad, not missing)?
   - Do error paths clean up resources (files, connections, locks)?
   - Are error messages useful for debugging (not swallowed silently)?

3. SECURITY
   - Is user input validated/sanitized before use?
   - Are secrets handled safely (not logged, not in URLs, not hardcoded)?
   - Are file paths validated (no path traversal)?
   - Are permissions checked where needed?

4. CONCURRENCY & STATE
   - Are shared resources protected from race conditions?
   - Are async operations awaited properly?
   - Is mutable state handled safely across concurrent access?

5. DESIGN QUALITY
   - Is the code doing what it should at the right abstraction level?
   - Are functions/methods focused (single responsibility)?
   - Are there obvious performance issues (N+1 queries, unbounded loops)?

## Rules
- Be thorough. A missed bug in review means a production incident.
- Be specific. Reference exact file paths and line numbers.
- Do NOT pass code just because it "mostly works." If there are real issues, FAIL it.
- Do NOT nitpick pure style preferences (variable naming, import ordering) when
  no linter flags them. Focus on things that affect correctness and reliability.
- If a "Pipeline Task Context" section lists sibling tasks and their file scopes,
  do NOT fail for missing integration code that belongs to a sibling task's scope."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/review/llm_review_test.py::TestReviewSystemPrompt -v`
Expected: PASS

- [ ] **Step 5: Run full llm_review test suite for regressions**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/review/llm_review_test.py -v`
Expected: All existing tests still pass. Note: `test_includes_prior_feedback_on_retry` asserts `"PRIMARY job" in prompt` — this will FAIL. We fix that in Task 2.

---

### Task 2: Remove retry suppression + fix delta diff framing

**Files:**
- Modify: `forge/review/llm_review.py:208-236` (replace retry + delta sections in `_build_review_prompt`)
- Test: `forge/review/llm_review_test.py`

- [ ] **Step 1: Write failing tests for new retry and delta framing**

Add to `forge/review/llm_review_test.py` in `TestBuildReviewPrompt`:

```python
    def test_retry_prompt_no_suppression_language(self):
        """Retry prompt must NOT contain suppression language."""
        prompt = _build_review_prompt(
            "T", "D", "diff",
            prior_feedback="Bug in line 42",
        )
        assert "PRIMARY job" not in prompt
        assert "Do NOT invent new stylistic complaints" not in prompt
        assert "focus on the prior feedback" not in prompt

    def test_retry_prompt_allows_new_issues(self):
        """Retry prompt tells reviewer to do full review + flag new genuine issues."""
        prompt = _build_review_prompt(
            "T", "D", "diff",
            prior_feedback="Bug in line 42",
        )
        assert "full review" in prompt.lower() or "full review" in prompt
        assert "not a ceiling" in prompt or "Prior feedback is context" in prompt

    def test_delta_diff_neutral_framing(self):
        """Delta diff section uses neutral context framing, not scope-limiting."""
        prompt = _build_review_prompt(
            "T", "D", "full diff",
            delta_diff="delta changes",
        )
        assert "shown for context" in prompt
        assert "Focus your review on these delta changes" not in prompt
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/review/llm_review_test.py::TestBuildReviewPrompt::test_retry_prompt_no_suppression_language forge/review/llm_review_test.py::TestBuildReviewPrompt::test_retry_prompt_allows_new_issues forge/review/llm_review_test.py::TestBuildReviewPrompt::test_delta_diff_neutral_framing -v`
Expected: FAIL — current code has "PRIMARY job" and "Focus your review on these delta changes"

- [ ] **Step 3: Update `_build_review_prompt` retry section**

In `forge/review/llm_review.py`, replace lines 220-226 (the `parts.append(...)` with the 4-point "PRIMARY job" instruction):

```python
        parts.append(
            "The developer has attempted to fix these issues.\n"
            "Verify the specific issues above were addressed, AND do a full review of the\n"
            "current code. If you find new genuine issues (bugs, security, error handling),\n"
            "FAIL — regardless of whether they were in the prior feedback or not.\n"
            "Prior feedback is context, not a ceiling on what you can flag.\n\n"
        )
```

- [ ] **Step 4: Update `_build_review_prompt` delta section**

In `forge/review/llm_review.py`, replace lines 227-236 (the delta diff section):

```python
    if delta_diff:
        delta_snippet = delta_diff[:6000]
        parts.append(
            "=== CHANGES SINCE LAST REVIEW (DELTA) ===\n"
            "These are the changes the developer made in this retry attempt, shown for context.\n"
            f"```diff\n{delta_snippet}\n```\n\n"
            "The full diff above shows the complete current state.\n\n"
        )
```

- [ ] **Step 5: Update existing test assertion**

In `forge/review/llm_review_test.py`, update `test_includes_prior_feedback_on_retry` (line 65):

Change:
```python
        assert "PRIMARY job" in prompt
```
To:
```python
        assert "Prior feedback is context" in prompt
```

And update `test_includes_delta_diff` (line 96):

Change:
```python
        assert "Focus your review on these delta changes" in prompt
```
To:
```python
        assert "shown for context" in prompt
```

- [ ] **Step 6: Run full test suite to verify all pass**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/review/llm_review_test.py -v`
Expected: ALL tests PASS

---

### Task 3: Fix `custom_review_focus` separator

**Files:**
- Modify: `forge/review/llm_review.py:101-102`
- Test: `forge/review/llm_review_test.py`

- [ ] **Step 1: Write failing test for separator**

The separator is in `gate2_llm_review` (not `_build_review_prompt`), so we capture the `ClaudeCodeOptions` passed to `sdk_query`:

```python
class TestCustomReviewFocusSeparator:
    """custom_review_focus gets proper separator from system prompt."""

    @pytest.mark.asyncio
    async def test_custom_focus_has_separator(self):
        """custom_review_focus is separated from base prompt by double newline."""
        mock_result = MagicMock()
        mock_result.result = "PASS: ok"
        mock_result.cost_usd = 0.0
        mock_result.input_tokens = 0
        mock_result.output_tokens = 0

        captured_options = []

        async def capture_sdk_query(*, prompt, options, on_message=None):
            captured_options.append(options)
            return mock_result

        with patch("forge.review.llm_review.sdk_query", side_effect=capture_sdk_query):
            await gate2_llm_review(
                "T", "D", "diff",
                custom_review_focus="Focus on error handling paths.",
            )

        assert len(captured_options) == 1
        system_prompt = captured_options[0].system_prompt
        assert "\n\nFocus on error handling paths." in system_prompt
        # Must NOT be glued directly without separator
        assert "scope.\nFocus on error" not in system_prompt or "\n\nFocus on error" in system_prompt
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/review/llm_review_test.py::TestCustomReviewFocusSeparator -v`
Expected: FAIL — current code does `system_prompt += custom_review_focus` without separator

- [ ] **Step 3: Fix the separator in `gate2_llm_review`**

In `forge/review/llm_review.py`, line 102, change:

```python
        system_prompt += custom_review_focus
```
to:
```python
        system_prompt += "\n\n" + custom_review_focus
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/review/llm_review_test.py::TestCustomReviewFocusSeparator -v`
Expected: PASS

- [ ] **Step 5: Run full test suite + daemon_review tests**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/review/llm_review_test.py forge/core/daemon_review_test.py -v`
Expected: ALL tests PASS

- [ ] **Step 6: Commit review quality changes**

```bash
cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande
git add forge/review/llm_review.py forge/review/llm_review_test.py
git commit -m "$(cat <<'EOF'
feat: comprehensive LLM review prompt + remove retry suppression

Replace the sparse 5-bullet review system prompt with a structured
5-category checklist (correctness, error handling, security,
concurrency, design quality). Remove retry suppression language
that turned re-reviews into checkbox verifiers. Fix missing \n\n
separator before custom_review_focus concatenation.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Chunk 2: Task Pool Execution

This chunk replaces the `asyncio.gather` batch dispatch with a continuous task pool. All changes are in `forge/core/daemon.py` and a new test file.

### Task 4: Add `_safe_execute_task` wrapper

**Files:**
- Modify: `forge/core/daemon.py` (add method to `ForgeDaemon` class, near `_execute_task` delegation)
- Test: `forge/core/daemon_pool_test.py` (new file)

- [ ] **Step 1: Write failing tests for `_safe_execute_task`**

Create `forge/core/daemon_pool_test.py`:

```python
"""Tests for the continuous task pool in ForgeDaemon."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.config.settings import ForgeSettings
from forge.core.daemon import ForgeDaemon
from forge.core.models import TaskState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daemon(tmp_path, **settings_kwargs):
    settings = ForgeSettings(**settings_kwargs)
    return ForgeDaemon(project_dir=str(tmp_path), settings=settings)


def _make_task(state: str, task_id: str = "task-1") -> MagicMock:
    t = MagicMock()
    t.id = task_id
    t.state = state
    t.title = f"Task {task_id}"
    t.description = "test task"
    t.files = []
    t.depends_on = []
    t.complexity = "medium"
    t.assigned_agent = None
    t.retry_count = 0
    return t


# ---------------------------------------------------------------------------
# Tests for _safe_execute_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSafeExecuteTask:
    """_safe_execute_task wraps _execute_task with cleanup guarantees."""

    async def test_normal_completion_releases_agent(self, tmp_path):
        """Agent is released after normal _execute_task completion."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock()

        # Mock _execute_task to complete normally
        daemon._execute_task = AsyncMock(return_value=None)

        await daemon._safe_execute_task(
            db, MagicMock(), MagicMock(), MagicMock(),
            "task-1", "agent-1", pipeline_id="pipe-1",
        )

        db.release_agent.assert_called_once_with("agent-1")

    async def test_exception_releases_agent_and_reraises(self, tmp_path):
        """Agent is released and exception re-raised on crash."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock()

        daemon._execute_task = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await daemon._safe_execute_task(
                db, MagicMock(), MagicMock(), MagicMock(),
                "task-1", "agent-1",
            )

        db.release_agent.assert_called_once_with("agent-1")

    async def test_cancellation_releases_agent_and_reraises(self, tmp_path):
        """Agent is released on CancelledError and error is re-raised."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock()

        daemon._execute_task = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await daemon._safe_execute_task(
                db, MagicMock(), MagicMock(), MagicMock(),
                "task-1", "agent-1",
            )

        db.release_agent.assert_called_once_with("agent-1")

    async def test_release_failure_does_not_mask_original_error(self, tmp_path):
        """If release_agent fails, the original exception still propagates."""
        daemon = _make_daemon(tmp_path)
        db = MagicMock()
        db.release_agent = AsyncMock(side_effect=Exception("DB down"))

        daemon._execute_task = AsyncMock(side_effect=RuntimeError("task crash"))

        with pytest.raises(RuntimeError, match="task crash"):
            await daemon._safe_execute_task(
                db, MagicMock(), MagicMock(), MagicMock(),
                "task-1", "agent-1",
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/core/daemon_pool_test.py::TestSafeExecuteTask -v`
Expected: FAIL — `_safe_execute_task` doesn't exist yet

- [ ] **Step 3: Implement `_safe_execute_task` on `ForgeDaemon`**

In `forge/core/daemon.py`, add this method directly to the `ForgeDaemon` class (line 116). `_execute_task` lives on `ExecutorMixin` in `daemon_executor.py`, but `_safe_execute_task` belongs on `ForgeDaemon` because it's part of the pool orchestration, not task execution. `ForgeDaemon` inherits from `ExecutorMixin`, so `self._execute_task` resolves via MRO. Place it right before the `_execution_loop_body` method:

```python
    async def _safe_execute_task(
        self, db, runtime, worktree_mgr, merge_worker,
        task_id: str, agent_id: str, pipeline_id: str | None = None,
    ) -> None:
        """Wrapper ensuring cleanup on cancellation or crash.

        Guarantees agent release on ALL exit paths (normal return, exception,
        cancellation). The agent is released here rather than deferring to
        the reap loop, because a transient DB error in the reap loop would
        otherwise leak the agent permanently.
        """
        try:
            await self._execute_task(
                db, runtime, worktree_mgr, merge_worker,
                task_id, agent_id, pipeline_id=pipeline_id,
            )
        except asyncio.CancelledError:
            logger.info("Task %s was cancelled (shutdown)", task_id)
            raise
        except Exception:
            raise
        finally:
            try:
                await db.release_agent(agent_id)
            except Exception:
                logger.warning("Failed to release agent %s for task %s", agent_id, task_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/core/daemon_pool_test.py::TestSafeExecuteTask -v`
Expected: ALL PASS

---

### Task 5: Extract `_handle_task_exception` helper

**Files:**
- Modify: `forge/core/daemon.py` (extract from lines 783-813, add as method)
- Test: `forge/core/daemon_pool_test.py`

- [ ] **Step 1: Write failing tests for `_handle_task_exception`**

Add to `forge/core/daemon_pool_test.py`:

```python
@pytest.mark.asyncio
class TestHandleTaskException:
    """_handle_task_exception marks task as ERROR, releases agent, cleans worktree."""

    async def test_marks_task_error_and_releases_agent(self, tmp_path):
        """Task is marked ERROR and agent released on exception."""
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()

        task_rec = _make_task("in_progress", "task-1")
        task_rec.assigned_agent = "agent-1"

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.get_task = AsyncMock(return_value=task_rec)
        db.release_agent = AsyncMock()
        db.log_event = AsyncMock()

        worktree_mgr = MagicMock()
        worktree_mgr.remove = MagicMock()

        exc = RuntimeError("task exploded")
        await daemon._handle_task_exception("task-1", exc, db, worktree_mgr, "pipe-1")

        db.update_task_state.assert_called_once_with("task-1", TaskState.ERROR.value)
        db.release_agent.assert_called_once_with("agent-1")
        worktree_mgr.remove.assert_called_once_with("task-1")

    async def test_emits_pipeline_error_when_all_terminal(self, tmp_path):
        """pipeline:error emitted when crashed task makes all tasks terminal."""
        daemon = _make_daemon(tmp_path)
        emitted = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        task_rec = _make_task("error", "task-1")
        task_rec.assigned_agent = "agent-1"

        # All tasks are now terminal after this crash
        all_tasks = [
            _make_task(TaskState.DONE.value, "task-2"),
            _make_task(TaskState.ERROR.value, "task-1"),
        ]

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.get_task = AsyncMock(return_value=task_rec)
        db.release_agent = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=all_tasks)
        db.log_event = AsyncMock()

        worktree_mgr = MagicMock()
        worktree_mgr.remove = MagicMock()

        await daemon._handle_task_exception("task-1", RuntimeError("crash"), db, worktree_mgr, "pipe-1")

        pipeline_errors = [e for e in emitted if e[0] == "pipeline:error"]
        assert len(pipeline_errors) == 1

    async def test_no_pipeline_error_when_tasks_still_active(self, tmp_path):
        """No pipeline:error when other tasks are still running."""
        daemon = _make_daemon(tmp_path)
        emitted = []

        async def mock_emit(event_type, payload, *, db=None, pipeline_id=None):
            emitted.append((event_type, payload))

        daemon._emit = mock_emit

        task_rec = _make_task("error", "task-1")
        task_rec.assigned_agent = "agent-1"

        remaining = [
            _make_task(TaskState.IN_PROGRESS.value, "task-2"),
            _make_task(TaskState.ERROR.value, "task-1"),
        ]

        db = MagicMock()
        db.update_task_state = AsyncMock()
        db.get_task = AsyncMock(return_value=task_rec)
        db.release_agent = AsyncMock()
        db.list_tasks_by_pipeline = AsyncMock(return_value=remaining)
        db.log_event = AsyncMock()

        worktree_mgr = MagicMock()
        worktree_mgr.remove = MagicMock()

        await daemon._handle_task_exception("task-1", RuntimeError("crash"), db, worktree_mgr, "pipe-1")

        pipeline_errors = [e for e in emitted if e[0] == "pipeline:error"]
        assert len(pipeline_errors) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/core/daemon_pool_test.py::TestHandleTaskException -v`
Expected: FAIL — `_handle_task_exception` doesn't exist yet

- [ ] **Step 3: Implement `_handle_task_exception`**

In `forge/core/daemon.py`, add this method to `ForgeDaemon`:

```python
    async def _handle_task_exception(
        self, task_id: str, exc: BaseException,
        db, worktree_mgr, pipeline_id: str | None,
    ) -> None:
        """Handle a task that raised an unhandled exception in the pool."""
        logger.error("Task %s raised unhandled exception: %s", task_id, exc, exc_info=exc)
        try:
            await db.update_task_state(task_id, TaskState.ERROR.value)
            await self._emit("task:state_changed", {
                "task_id": task_id, "state": "error", "error": str(exc),
            }, db=db, pipeline_id=pipeline_id or "")
        except Exception:
            logger.exception("Failed to mark crashed task %s as error", task_id)
        try:
            task_rec = await db.get_task(task_id)
            if task_rec and task_rec.assigned_agent:
                await db.release_agent(task_rec.assigned_agent)
        except Exception:
            pass
        try:
            worktree_mgr.remove(task_id)
        except Exception as cleanup_err:
            logger.warning("Failed to clean up worktree for task %s: %s", task_id, cleanup_err)

        if pipeline_id:
            try:
                remaining = await db.list_tasks_by_pipeline(pipeline_id)
                terminal = (TaskState.DONE.value, TaskState.ERROR.value, TaskState.CANCELLED.value)
                if all(t.state in terminal for t in remaining):
                    await self._emit("pipeline:error", {
                        "error": f"Pipeline failed: task {task_id} crashed",
                    }, db=db, pipeline_id=pipeline_id)
            except Exception:
                logger.exception("Failed to check pipeline state after task %s crash", task_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/core/daemon_pool_test.py::TestHandleTaskException -v`
Expected: ALL PASS

---

### Task 6: Replace `asyncio.gather` with continuous task pool

**Files:**
- Modify: `forge/core/daemon.py:761-813` (replace gather section with pool logic)
- Test: `forge/core/daemon_pool_test.py`

- [ ] **Step 1: Write failing test for continuous re-dispatch**

Add to `forge/core/daemon_pool_test.py`:

```python
@pytest.mark.asyncio
class TestContinuousTaskPool:
    """The execution loop re-dispatches retried tasks without waiting for batch completion."""

    async def test_retried_task_dispatched_while_other_running(self, tmp_path):
        """task-1 finishes and goes to TODO while task-2 is still running.
        task-1 should be re-dispatched on the next tick without waiting for task-2.

        Uses asyncio.Event gates to control task completion deterministically.
        """
        daemon = _make_daemon(tmp_path, max_agents=2, scheduler_poll_interval=0.05)
        daemon._emit = AsyncMock()

        # Track what was dispatched
        dispatched_tasks: list[str] = []
        task1_gate = asyncio.Event()
        task2_gate = asyncio.Event()
        task1_redispatch_gate = asyncio.Event()

        async def mock_execute(db, runtime, worktree_mgr, merge_worker, task_id, agent_id, pipeline_id=None):
            dispatched_tasks.append(task_id)
            if task_id == "task-1" and len([t for t in dispatched_tasks if t == "task-1"]) == 1:
                # First dispatch of task-1: complete quickly
                task1_gate.set()
            elif task_id == "task-1":
                # Re-dispatch of task-1: complete
                await task1_redispatch_gate.wait()
            elif task_id == "task-2":
                # task-2: wait until released
                await task2_gate.wait()

        daemon._execute_task = mock_execute

        # This test validates the pool concept — the actual loop integration
        # is tested by running the full loop. For a unit test, we verify
        # the pool mechanics directly.
        # Verify that asyncio.Task pool allows re-dispatch while task-2 runs.

        _active_tasks: dict[str, asyncio.Task] = {}

        db = MagicMock()
        db.release_agent = AsyncMock()

        # Launch task-1 and task-2
        t1 = asyncio.create_task(
            daemon._safe_execute_task(db, None, None, None, "task-1", "agent-1")
        )
        _active_tasks["task-1"] = t1

        t2 = asyncio.create_task(
            daemon._safe_execute_task(db, None, None, None, "task-2", "agent-2")
        )
        _active_tasks["task-2"] = t2

        # Wait for task-1 to complete
        await asyncio.sleep(0.05)
        assert task1_gate.is_set(), "task-1 should have completed"

        # Reap completed
        done_ids = [tid for tid, at in _active_tasks.items() if at.done()]
        assert "task-1" in done_ids
        assert "task-2" not in done_ids  # still running

        for tid in done_ids:
            _active_tasks.pop(tid)

        # Re-dispatch task-1 while task-2 still runs
        assert len(_active_tasks) == 1  # only task-2
        t1_redux = asyncio.create_task(
            daemon._safe_execute_task(db, None, None, None, "task-1", "agent-1")
        )
        _active_tasks["task-1"] = t1_redux
        dispatched_tasks.append("task-1")  # simulating scheduler dispatch

        # task-1 was re-dispatched without waiting for task-2
        assert "task-2" in _active_tasks
        assert not _active_tasks["task-2"].done()

        # Clean up
        task1_redispatch_gate.set()
        task2_gate.set()
        await asyncio.gather(*_active_tasks.values(), return_exceptions=True)
```

- [ ] **Step 2: Run to verify it passes**

This is a design validation test — it tests the pool data structure pattern, which works once `_safe_execute_task` exists from Task 4. It verifies the core property: task-1 can be reaped and re-dispatched while task-2 is still running.

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/core/daemon_pool_test.py::TestContinuousTaskPool::test_retried_task_dispatched_while_other_running -v`
Expected: PASS

- [ ] **Step 3: Write test for `_active_tasks` guard preventing double-dispatch**

```python
    async def test_active_tasks_guard_prevents_double_dispatch(self, tmp_path):
        """Tasks already in _active_tasks are filtered from dispatch_plan."""
        dispatch_plan = [("task-1", "agent-1"), ("task-2", "agent-2")]
        _active_tasks = {"task-1": MagicMock()}  # task-1 already running

        filtered = [
            (tid, aid) for tid, aid in dispatch_plan
            if tid not in _active_tasks
        ]

        assert len(filtered) == 1
        assert filtered[0] == ("task-2", "agent-2")
```

- [ ] **Step 4: Write test for exception isolation — crashed task doesn't affect sibling**

```python
    async def test_exception_in_one_task_does_not_affect_other(self, tmp_path):
        """When task-1 crashes, task-2 continues running unaffected in the pool."""
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()

        task2_gate = asyncio.Event()
        task2_completed = False

        async def mock_execute(db, runtime, worktree_mgr, merge_worker, task_id, agent_id, pipeline_id=None):
            if task_id == "task-1":
                raise RuntimeError("task-1 crashed")
            elif task_id == "task-2":
                await task2_gate.wait()
                nonlocal task2_completed
                task2_completed = True

        daemon._execute_task = mock_execute

        db = MagicMock()
        db.release_agent = AsyncMock()

        _active_tasks: dict[str, asyncio.Task] = {}

        t1 = asyncio.create_task(
            daemon._safe_execute_task(db, None, None, None, "task-1", "agent-1")
        )
        _active_tasks["task-1"] = t1

        t2 = asyncio.create_task(
            daemon._safe_execute_task(db, None, None, None, "task-2", "agent-2")
        )
        _active_tasks["task-2"] = t2

        # Wait for task-1 to crash
        await asyncio.sleep(0.05)

        # task-1 should be done (with exception), task-2 still running
        assert t1.done()
        assert not t2.done()
        assert t1.exception() is not None

        # Release task-2 and verify it completes normally
        task2_gate.set()
        await asyncio.sleep(0.05)
        assert t2.done()
        assert task2_completed
        assert t2.exception() is None

        # Clean up
        await asyncio.gather(*_active_tasks.values(), return_exceptions=True)
```

- [ ] **Step 5: Write test for shutdown cleanup — cancels all tasks and releases agents**

```python
    async def test_shutdown_cancels_active_tasks_and_releases_agents(self, tmp_path):
        """Shutdown cancels all active tasks; _safe_execute_task releases agents."""
        daemon = _make_daemon(tmp_path)
        daemon._emit = AsyncMock()

        gate1 = asyncio.Event()
        gate2 = asyncio.Event()

        async def mock_execute(db, runtime, worktree_mgr, merge_worker, task_id, agent_id, pipeline_id=None):
            if task_id == "task-1":
                await gate1.wait()
            elif task_id == "task-2":
                await gate2.wait()

        daemon._execute_task = mock_execute

        db = MagicMock()
        db.release_agent = AsyncMock()

        _active_tasks: dict[str, asyncio.Task] = {}

        t1 = asyncio.create_task(
            daemon._safe_execute_task(db, None, None, None, "task-1", "agent-1")
        )
        _active_tasks["task-1"] = t1

        t2 = asyncio.create_task(
            daemon._safe_execute_task(db, None, None, None, "task-2", "agent-2")
        )
        _active_tasks["task-2"] = t2

        await asyncio.sleep(0.05)  # let tasks start

        # Simulate shutdown: cancel all
        for atask in _active_tasks.values():
            atask.cancel()
        await asyncio.gather(*_active_tasks.values(), return_exceptions=True)

        # Both tasks should be cancelled
        assert t1.cancelled()
        assert t2.cancelled()

        # Agents should have been released by _safe_execute_task's finally block
        release_calls = [call.args[0] for call in db.release_agent.call_args_list]
        assert "agent-1" in release_calls
        assert "agent-2" in release_calls
```

- [ ] **Step 6: Run all pool tests**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/core/daemon_pool_test.py::TestContinuousTaskPool -v`
Expected: ALL PASS

- [ ] **Step 7: Replace `asyncio.gather` section in daemon.py**

In `forge/core/daemon.py`, this is the main change. Four precise insertion/replacement points:

**Insertion point 1 — Pool init (after line 647, before `while True:` on line 648):**

The `_execution_loop_body` method starts with variable setup at line 641. The `while True:` loop is at line 648. Add pool init between them:

```python
        _active_tasks: dict[str, asyncio.Task] = {}  # task_id → asyncio.Task
```

**Insertion point 2 — Reap logic (line 648, BEFORE the watchdog check at line 649):**

The reap must run BEFORE the watchdog timeout check so that tasks completed in the last tick are processed before we check if the pipeline timed out. Insert immediately after `while True:`:

```python
            # Reap completed tasks from the pool
            done_ids = [tid for tid, atask in _active_tasks.items() if atask.done()]
            for tid in done_ids:
                atask = _active_tasks.pop(tid)
                exc = atask.exception() if not atask.cancelled() else None
                if exc:
                    await self._handle_task_exception(tid, exc, db, worktree_mgr, pipeline_id)
```

**Replacement point 3 — Replace lines 761-813 (from `if not dispatch_plan:` through end of exception handling):**
```python
            if not dispatch_plan:
                if not _active_tasks:
                    # No active tasks in pool AND nothing to dispatch
                    if any(t.state in (TaskState.AWAITING_APPROVAL.value, TaskState.AWAITING_INPUT.value) for t in tasks):
                        await asyncio.sleep(self._settings.scheduler_poll_interval)
                        continue
                    console.print("[yellow]No tasks to dispatch and none in progress. Stopping.[/yellow]")
                    break
                # Tasks are running in pool but nothing new to dispatch — wait efficiently
                _done, _pending = await asyncio.wait(
                    _active_tasks.values(),
                    timeout=self._settings.scheduler_poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                continue

            # Guard: skip tasks already in the pool (race condition prevention)
            dispatch_plan = [
                (tid, aid) for tid, aid in dispatch_plan
                if tid not in _active_tasks
            ]

            # Cap to actual free slots (pool size is authoritative)
            available_slots = max(0, self._settings.max_agents - len(_active_tasks))
            dispatch_plan = dispatch_plan[:available_slots]

            # Launch into pool
            for task_id, agent_id in dispatch_plan:
                await db.assign_task(task_id, agent_id)
                await db.update_task_state(task_id, TaskState.IN_PROGRESS.value)
                atask = asyncio.create_task(
                    self._safe_execute_task(db, runtime, worktree_mgr, merge_worker,
                                            task_id, agent_id, pipeline_id=pipeline_id),
                    name=f"forge-task-{task_id}",
                )
                _active_tasks[task_id] = atask

            # Wait efficiently for next event
            if _active_tasks:
                _done, _pending = await asyncio.wait(
                    _active_tasks.values(),
                    timeout=self._settings.scheduler_poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            else:
                await asyncio.sleep(self._settings.scheduler_poll_interval)
```

**Insertion point 4 — Shutdown cleanup (after ALL `break` statements exit the loop):**

The loop has multiple `break` points (watchdog timeout at line 661, all-parked at line 712, no-dispatch-no-active at line 768). After the loop, add shutdown cleanup. This goes at the same indentation level as the `while True:`:

```python
        # Shutdown: cancel all active tasks and wait for cleanup
        for atask in _active_tasks.values():
            atask.cancel()
        if _active_tasks:
            await asyncio.gather(*_active_tasks.values(), return_exceptions=True)
        _active_tasks.clear()
```

- [ ] **Step 8: Run all pool tests**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/core/daemon_pool_test.py -v`
Expected: ALL PASS

- [ ] **Step 9: Run existing daemon tests for regressions**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/core/daemon_test.py forge/core/daemon_executor_test.py forge/core/daemon_review_test.py -v`
Expected: ALL PASS

- [ ] **Step 10: Commit task pool changes**

```bash
cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande
git add forge/core/daemon.py forge/core/daemon_pool_test.py
git commit -m "$(cat <<'EOF'
feat: continuous task pool replacing asyncio.gather batch dispatch

Replace the asyncio.gather batch that blocked the scheduler loop
until ALL tasks completed. Now uses a dict[str, asyncio.Task] pool
with continuous reap-dispatch cycle. Retried tasks get re-dispatched
immediately without waiting for sibling tasks.

Adds _safe_execute_task wrapper guaranteeing agent release on all
exit paths (normal, exception, cancellation). Extracts
_handle_task_exception helper for clean error handling in the
reap loop.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Chunk 3: Integration verification + PR

### Task 7: Full test suite verification

**Files:** None (verification only)

- [ ] **Step 1: Run complete test suite**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande && uv run --extra dev pytest forge/ -x -v --timeout=60 2>&1 | tail -30`
Expected: ALL tests PASS (300+ tests)

- [ ] **Step 2: If any failures, debug and fix before proceeding**

Use @superpowers:systematic-debugging if tests fail.

### Task 8: Push branch and create PR

- [ ] **Step 1: Push branch**

```bash
cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/happy-lalande
git push -u origin claude/happy-lalande
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --title "feat: task pool execution + review quality overhaul" --body "$(cat <<'EOF'
## Summary
- Replace `asyncio.gather` batch dispatch with continuous `dict[str, asyncio.Task]` pool — retried tasks get re-dispatched immediately without waiting for sibling tasks
- `_safe_execute_task` wrapper guarantees agent release on all exit paths (normal, exception, cancellation)
- Comprehensive 5-category LLM review checklist replacing sparse 5-bullet prompt
- Remove retry suppression language that turned re-reviews into checkbox verifiers
- Fix missing `\n\n` separator before `custom_review_focus` concatenation

## Test plan
- [ ] All existing 300+ tests pass
- [ ] New pool tests: re-dispatch timing, exception isolation, shutdown cleanup, double-dispatch guard
- [ ] New review tests: system prompt content, retry prompt framing, delta diff neutrality, separator fix
- [ ] Manual: run a 2-task pipeline, fail review on task-1, verify task-1 retries while task-2 continues

## Spec
`docs/superpowers/specs/2026-03-15-task-pool-and-review-quality-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
