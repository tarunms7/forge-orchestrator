# Resilient Pipeline Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline lifecycle bulletproof — partial completion visibility, quit/resume, dependency cascade, complexity-scaled timeouts, follow-up/re-run fixes, and a universal shortcut bar.

**Architecture:** Extend the existing pipeline state machine with 3 new states (partial_success, retrying, interrupted) and 1 new task state (BLOCKED). The TUI's FinalApprovalScreen gains a partial mode. Graceful quit writes interrupted status to DB; resume reconstructs state from events. A ShortcutBar widget is added to every screen.

**Tech Stack:** Python 3.12+, asyncio, aiosqlite/SQLAlchemy, Textual TUI, Rich markup

**Spec:** `docs/superpowers/specs/2026-03-15-resilient-pipeline-lifecycle-design.md` (on branch `feat/resilient-pipeline-lifecycle`)

**Test runner:** `uv run --extra dev pytest <path> -v`

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `forge/core/models.py` | Data models & enums | Add BLOCKED to TaskState |
| `forge/core/daemon.py` | Execution loop & lifecycle | Add BLOCKED to parked_states, differentiate complete/partial_success/error, add executor_pid/token, emit pipeline:interrupted |
| `forge/core/daemon_merge.py` | Retry & merge logic | Add _cascade_blocked() after ERROR in both _handle_retry and _handle_merge_retry |
| `forge/core/daemon_executor.py` | Agent execution | Complexity-scaled timeout multiplier |
| `forge/storage/db.py` | Database ORM & queries | Add executor_pid/executor_token columns, add BLOCKED to schema |
| `forge/tui/state.py` | TUI state machine | Differentiate partial_success vs final_approval in _on_all_tasks_done, add pipeline:interrupted handler |
| `forge/tui/app.py` | TUI application | Graceful quit, resume flow, follow-up handler, re-run handler, partial_success phase handling |
| `forge/tui/screens/final_approval.py` | Final approval screen | Partial mode rendering, dynamic bindings, error/blocked display |
| `forge/tui/widgets/followup_input.py` | Follow-up text input | Add escape binding to FollowUpTextArea |
| `forge/tui/widgets/shortcut_bar.py` | **(NEW)** Shortcut bar widget | Reusable bottom bar for all screens |
| `forge/tui/pr_creator.py` | PR creation utilities | Accept failed_tasks param, render partial PR body |
| `forge/tui/bus.py` | Event type registry | Add pipeline:interrupted |
| `forge/tui/screens/pipeline.py` | Pipeline screen | Add partial_success/retrying/interrupted phase banners |

---

## Chunk 1: Foundation — Models, DB, and Core Logic

### Task 1: Add BLOCKED task state and executor tracking columns

**Files:**
- Modify: `forge/core/models.py:14-23`
- Modify: `forge/storage/db.py:72-100` (TaskRow), `db.py` schema migration
- Test: `forge/core/models_test.py` (if exists), `forge/storage/db_test.py`

- [ ] **Step 1: Write failing test for BLOCKED state**

```python
# In forge/core/models_test.py (create if needed)
from forge.core.models import TaskState

def test_blocked_state_exists():
    assert TaskState.BLOCKED == "blocked"
    assert TaskState.BLOCKED.value == "blocked"

def test_blocked_is_distinct_from_error():
    assert TaskState.BLOCKED != TaskState.ERROR
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/core/models_test.py -v -k "blocked"`
Expected: FAIL — AttributeError: type object 'TaskState' has no attribute 'BLOCKED'

- [ ] **Step 3: Add BLOCKED to TaskState enum**

In `forge/core/models.py`, add after line 22 (`ERROR = "error"`):
```python
    BLOCKED = "blocked"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/core/models_test.py -v -k "blocked"`
Expected: PASS

- [ ] **Step 5: Write failing test for executor tracking columns**

```python
# In forge/storage/db_test.py (append to existing)
import pytest
from forge.storage.db import Database

@pytest.mark.asyncio
async def test_executor_tracking_columns(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    # Create a pipeline
    pid = await db.create_pipeline(
        description="test", project_dir="/tmp", status="executing",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    # Set executor info
    await db.set_executor_info(pid, pid=12345, token="abc-123")
    p = await db.get_pipeline(pid)
    assert p.executor_pid == 12345
    assert p.executor_token == "abc-123"

    # Clear executor info
    await db.clear_executor_info(pid)
    p = await db.get_pipeline(pid)
    assert p.executor_pid is None
    assert p.executor_token is None
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/storage/db_test.py -v -k "executor_tracking"`
Expected: FAIL — AttributeError: no method 'set_executor_info'

- [ ] **Step 7: Add executor_pid and executor_token columns + methods to db.py**

In `forge/storage/db.py`, add to PipelineRow class (after existing columns):
```python
    executor_pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    executor_token: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
```

Add the `_ensure_columns` migration pattern (same as used for `paused_at`, `contracts_json` etc.) to add these columns to existing DBs.

Add methods:
```python
async def set_executor_info(self, pipeline_id: str, pid: int, token: str) -> None:
    async with self._session_factory() as session:
        pipeline = await session.get(PipelineRow, pipeline_id)
        if pipeline:
            pipeline.executor_pid = pid
            pipeline.executor_token = token
            await session.commit()

async def clear_executor_info(self, pipeline_id: str) -> None:
    async with self._session_factory() as session:
        pipeline = await session.get(PipelineRow, pipeline_id)
        if pipeline:
            pipeline.executor_pid = None
            pipeline.executor_token = None
            await session.commit()
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/storage/db_test.py -v -k "executor_tracking"`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add forge/core/models.py forge/storage/db.py forge/core/models_test.py forge/storage/db_test.py
git commit -m "feat: add BLOCKED task state and executor tracking columns"
```

---

### Task 2: Dependency cascade — _cascade_blocked()

**Files:**
- Modify: `forge/core/daemon_merge.py:100-163` (_handle_retry), `daemon_merge.py:169-230` (_handle_merge_retry)
- Test: `forge/core/daemon_merge_test.py`

- [ ] **Step 1: Write failing test for cascade_blocked**

```python
# In forge/core/daemon_merge_test.py (append to existing)
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_cascade_blocked_marks_dependents():
    """When task-1 fails, task-3 (depends on task-1) should be marked BLOCKED."""
    from forge.core.daemon_merge import MergeMixin

    mixin = MergeMixin.__new__(MergeMixin)
    mixin._settings = MagicMock()
    mixin._settings.max_retries = 3
    mixin._events = AsyncMock()
    mixin._emit = AsyncMock()

    db = AsyncMock()
    # task-1 is ERROR, task-2 is DONE, task-3 depends on task-1
    task1 = MagicMock(id="t1", state="error", depends_on=[])
    task2 = MagicMock(id="t2", state="done", depends_on=[])
    task3 = MagicMock(id="t3", state="todo", depends_on=["t1"])
    task4 = MagicMock(id="t4", state="todo", depends_on=["t3"])  # transitive
    db.list_tasks_by_pipeline = AsyncMock(return_value=[task1, task2, task3, task4])

    await mixin._cascade_blocked(db, "t1", "pipe-1")

    # task3 and task4 should be marked blocked
    calls = db.update_task_state.call_args_list
    assert any(c.args == ("t3", "blocked") for c in calls)
    assert any(c.args == ("t4", "blocked") for c in calls)
    # task2 should NOT be touched
    assert not any(c.args[0] == "t2" for c in calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/core/daemon_merge_test.py -v -k "cascade_blocked"`
Expected: FAIL — AttributeError: 'MergeMixin' has no attribute '_cascade_blocked'

- [ ] **Step 3: Implement _cascade_blocked in daemon_merge.py**

Add to `MergeMixin` class in `daemon_merge.py`:

```python
async def _cascade_blocked(
    self, db: Database, failed_task_id: str, pipeline_id: str,
) -> None:
    """Mark all transitive dependents of a failed task as BLOCKED."""
    from collections import deque
    all_tasks = await db.list_tasks_by_pipeline(pipeline_id)
    newly_blocked: set[str] = set()
    queue: deque[str] = deque([failed_task_id])

    while queue:
        current_id = queue.popleft()
        for task in all_tasks:
            if task.id in newly_blocked:
                continue
            if task.state not in ("todo", "blocked"):
                continue
            if current_id in (task.depends_on or []):
                await db.update_task_state(task.id, "blocked")
                await self._emit("task:state_changed", {
                    "task_id": task.id,
                    "state": "blocked",
                    "error": f"Blocked: dependency {current_id} failed",
                }, db=db, pipeline_id=pipeline_id)
                newly_blocked.add(task.id)
                queue.append(task.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/core/daemon_merge_test.py -v -k "cascade_blocked"`
Expected: PASS

- [ ] **Step 5: Write test verifying cascade is called from _handle_retry on max retries**

```python
@pytest.mark.asyncio
async def test_handle_retry_cascades_on_max_retries():
    """When max retries exceeded, _cascade_blocked should be called."""
    from forge.core.daemon_merge import MergeMixin

    mixin = MergeMixin.__new__(MergeMixin)
    mixin._settings = MagicMock()
    mixin._settings.max_retries = 2
    mixin._events = AsyncMock()
    mixin._emit = AsyncMock()
    mixin._cascade_blocked = AsyncMock()

    db = AsyncMock()
    task = MagicMock(id="t1", retry_count=2)  # At max
    db.get_task = AsyncMock(return_value=task)

    worktree_mgr = MagicMock()
    worktree_mgr.remove = MagicMock()

    await mixin._handle_retry(db, "t1", worktree_mgr, pipeline_id="pipe-1")

    db.update_task_state.assert_called_once_with("t1", "error")
    mixin._cascade_blocked.assert_called_once_with(db, "t1", "pipe-1")
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/core/daemon_merge_test.py -v -k "handle_retry_cascades"`
Expected: FAIL — _cascade_blocked not called (it doesn't exist in the code path yet)

- [ ] **Step 7: Wire _cascade_blocked into _handle_retry and _handle_merge_retry**

In `forge/core/daemon_merge.py`, in the `_handle_retry` method, after line 142 (`await db.update_task_state(task_id, TaskState.ERROR.value)`), add:

```python
        if pipeline_id:
            await self._cascade_blocked(db, task_id, pipeline_id)
```

Do the same in `_handle_merge_retry`, after the ERROR state update (line ~210).

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/core/daemon_merge_test.py -v -k "cascade"`
Expected: PASS (both tests)

- [ ] **Step 9: Write test for _handle_merge_retry cascade too**

```python
@pytest.mark.asyncio
async def test_handle_merge_retry_cascades_on_max_retries():
    """_handle_merge_retry should also cascade on max retries."""
    from forge.core.daemon_merge import MergeMixin

    mixin = MergeMixin.__new__(MergeMixin)
    mixin._settings = MagicMock()
    mixin._settings.max_retries = 2
    mixin._events = AsyncMock()
    mixin._emit = AsyncMock()
    mixin._cascade_blocked = AsyncMock()

    db = AsyncMock()
    task = MagicMock(id="t1", retry_count=2)
    db.get_task = AsyncMock(return_value=task)

    worktree_mgr = MagicMock()
    worktree_mgr.remove = MagicMock()

    await mixin._handle_merge_retry(db, "t1", worktree_mgr, pipeline_id="pipe-1")

    db.update_task_state.assert_called_once_with("t1", "error")
    mixin._cascade_blocked.assert_called_once_with(db, "t1", "pipe-1")
```

- [ ] **Step 10: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/core/daemon_merge_test.py -v -k "merge_retry_cascades"`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add forge/core/daemon_merge.py forge/core/daemon_merge_test.py
git commit -m "feat: add dependency cascade — mark transitive dependents as BLOCKED"
```

---

### Task 3: Complexity-scaled timeouts

**Files:**
- Modify: `forge/core/daemon_executor.py:474` (AgentRuntime creation)
- Test: `forge/core/daemon_executor_test.py`

- [ ] **Step 1: Write failing test for complexity multiplier**

```python
# In forge/core/daemon_executor_test.py (append)
from forge.core.daemon_executor import _complexity_timeout

def test_complexity_timeout_low():
    assert _complexity_timeout(600, "low") == 600

def test_complexity_timeout_medium():
    assert _complexity_timeout(600, "medium") == 900

def test_complexity_timeout_high():
    assert _complexity_timeout(600, "high") == 1200

def test_complexity_timeout_unknown_defaults_to_medium():
    assert _complexity_timeout(600, "unknown") == 900

def test_complexity_timeout_none_defaults_to_medium():
    assert _complexity_timeout(600, None) == 900

def test_complexity_timeout_respects_base():
    """User override of base timeout should scale proportionally."""
    assert _complexity_timeout(300, "high") == 600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/core/daemon_executor_test.py -v -k "complexity_timeout"`
Expected: FAIL — ImportError: cannot import name '_complexity_timeout'

- [ ] **Step 3: Implement _complexity_timeout and wire it in**

Add at module level in `forge/core/daemon_executor.py`:

```python
_COMPLEXITY_MULTIPLIERS: dict[str, float] = {
    "low": 1.0,
    "medium": 1.5,
    "high": 2.0,
}

def _complexity_timeout(base_seconds: int, complexity: str | None) -> int:
    """Scale agent timeout by task complexity."""
    multiplier = _COMPLEXITY_MULTIPLIERS.get(complexity or "medium", 1.5)
    return int(base_seconds * multiplier)
```

Then at line 474 where `AgentRuntime` is created, change:
```python
# Before:
runtime = AgentRuntime(adapter, self._settings.agent_timeout_seconds)
# After:
timeout = _complexity_timeout(self._settings.agent_timeout_seconds, task.complexity)
runtime = AgentRuntime(adapter, timeout)
```

Note: `task` is available in scope — it's fetched earlier in the method. Check that `task.complexity` is accessible (it is — see line 104 where `task.complexity or "medium"` is used for model selection).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/core/daemon_executor_test.py -v -k "complexity_timeout"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add forge/core/daemon_executor.py forge/core/daemon_executor_test.py
git commit -m "feat: complexity-scaled agent timeouts — low=1x, medium=1.5x, high=2x"
```

---

### Task 4: Execution loop — differentiate complete/partial_success/error + BLOCKED in parked_states

**Files:**
- Modify: `forge/core/daemon.py:756-787`
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing test for partial_success detection**

```python
# In forge/core/daemon_test.py (append)
import pytest
from forge.core.daemon import _classify_pipeline_result

def test_classify_all_done():
    states = ["done", "done", "done"]
    assert _classify_pipeline_result(states) == "complete"

def test_classify_all_error():
    states = ["error", "error"]
    assert _classify_pipeline_result(states) == "error"

def test_classify_mixed():
    states = ["done", "done", "error", "blocked"]
    assert _classify_pipeline_result(states) == "partial_success"

def test_classify_with_cancelled_excluded():
    """Cancelled tasks don't count — if remaining are all done, it's complete."""
    states = ["done", "done", "cancelled"]
    assert _classify_pipeline_result(states) == "complete"

def test_classify_done_and_blocked():
    states = ["done", "blocked"]
    assert _classify_pipeline_result(states) == "partial_success"

def test_classify_all_cancelled():
    """All cancelled = complete (nothing failed)."""
    states = ["cancelled", "cancelled"]
    assert _classify_pipeline_result(states) == "complete"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/core/daemon_test.py -v -k "classify_pipeline"`
Expected: FAIL — ImportError: cannot import name '_classify_pipeline_result'

- [ ] **Step 3: Implement _classify_pipeline_result**

Add at module level in `forge/core/daemon.py`:

```python
def _classify_pipeline_result(task_states: list[str]) -> str:
    """Classify pipeline outcome from terminal task states.

    Returns: "complete", "partial_success", or "error"
    """
    # Exclude cancelled from consideration
    active_states = [s for s in task_states if s != "cancelled"]
    if not active_states:
        return "complete"  # All cancelled = nothing failed
    done_count = sum(1 for s in active_states if s == "done")
    if done_count == len(active_states):
        return "complete"
    if done_count == 0:
        return "error"
    return "partial_success"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/core/daemon_test.py -v -k "classify_pipeline"`
Expected: PASS

- [ ] **Step 5: Update the execution loop exit condition**

In `forge/core/daemon.py`, at line 757, add `"blocked"` to parked_states:

```python
# Before:
parked_states = (TaskState.DONE.value, TaskState.ERROR.value, TaskState.AWAITING_APPROVAL.value, TaskState.CANCELLED.value)
# After:
parked_states = (TaskState.DONE.value, TaskState.ERROR.value, TaskState.AWAITING_APPROVAL.value, TaskState.CANCELLED.value, TaskState.BLOCKED.value)
```

Then replace the `pipeline:all_tasks_done` emission block (lines 767-786) to include the classification and blocked count:

```python
    done_count = sum(1 for t in tasks if t.state == TaskState.DONE.value)
    error_count = sum(1 for t in tasks if t.state == TaskState.ERROR.value)
    blocked_count = sum(1 for t in tasks if t.state == TaskState.BLOCKED.value)
    cancelled_count = sum(1 for t in tasks if t.state == TaskState.CANCELLED.value)
    total_count = len(tasks)

    result = _classify_pipeline_result([t.state for t in tasks])
    if result == "complete":
        console.print(f"\n[bold green]Complete: {done_count}/{total_count} done[/bold green]")
    elif result == "partial_success":
        console.print(
            f"\n[bold yellow]Partial: {done_count} done, {error_count} errors, "
            f"{blocked_count} blocked[/bold yellow]"
        )
    else:
        console.print(f"\n[bold red]Failed: all {error_count} tasks errored[/bold red]")

    if pipeline_id:
        if _all_paused_since is not None:
            paused_elapsed = asyncio.get_event_loop().time() - _all_paused_since
            _all_paused_since = None
            await db.add_pipeline_paused_duration(pipeline_id, paused_elapsed)
            await db.set_pipeline_paused_at(pipeline_id, None)
        await db.update_pipeline_status(pipeline_id, result)
        await self._emit("pipeline:all_tasks_done", {
            "summary": {
                "done": done_count,
                "error": error_count,
                "blocked": blocked_count,
                "cancelled": cancelled_count,
                "total": total_count,
                "result": result,
            },
        }, db=db, pipeline_id=pipeline_id)
    break
```

- [ ] **Step 6: Run all existing daemon tests to verify no regressions**

Run: `uv run --extra dev pytest forge/core/daemon_test.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add forge/core/daemon.py forge/core/daemon_test.py
git commit -m "feat: differentiate complete/partial_success/error on pipeline exit"
```

---

## Chunk 2: TUI — State Machine, Screens, and Shortcuts

### Task 5: TUI state machine — handle partial_success, retrying, interrupted phases

**Files:**
- Modify: `forge/tui/state.py:383-385` (_on_all_tasks_done), `state.py:444-489` (EVENT_MAP)
- Modify: `forge/tui/bus.py:22-67` (TUI_EVENT_TYPES)
- Modify: `forge/tui/screens/pipeline.py:27-43` (_PHASE_BANNER)
- Test: `forge/tui/state_test.py` or `forge/tui/state_question_test.py`

- [ ] **Step 1: Write failing tests for new phase handling**

```python
# In forge/tui/state_test.py (append or create)
from forge.tui.state import TuiState

def test_all_tasks_done_partial_success():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {
        "summary": {"done": 3, "error": 1, "blocked": 1, "cancelled": 0, "total": 5, "result": "partial_success"}
    })
    assert state.phase == "partial_success"

def test_all_tasks_done_complete():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {
        "summary": {"done": 5, "error": 0, "blocked": 0, "cancelled": 0, "total": 5, "result": "complete"}
    })
    assert state.phase == "final_approval"

def test_all_tasks_done_all_error():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {
        "summary": {"done": 0, "error": 5, "blocked": 0, "cancelled": 0, "total": 5, "result": "error"}
    })
    assert state.phase == "error"

def test_pipeline_interrupted_event():
    state = TuiState()
    state.phase = "executing"
    state.apply_event("pipeline:interrupted", {"summary": {"done": 2, "todo": 3}})
    assert state.phase == "interrupted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --extra dev pytest forge/tui/state_test.py -v -k "all_tasks_done or interrupted"`
Expected: FAIL — partial_success/error phases not set

- [ ] **Step 3: Update _on_all_tasks_done in state.py**

Replace `_on_all_tasks_done` (lines 383-385):

```python
def _on_all_tasks_done(self, data: dict) -> None:
    summary = data.get("summary", {})
    result = summary.get("result", "complete")
    if result == "partial_success":
        self.phase = "partial_success"
    elif result == "error":
        self.phase = "error"
    else:
        self.phase = "final_approval"
    self._notify("phase")
```

Add new handler for `pipeline:interrupted`:

```python
def _on_interrupted(self, data: dict) -> None:
    self.phase = "interrupted"
    self._notify("phase")
```

Add to `_EVENT_MAP`:
```python
"pipeline:interrupted": _on_interrupted,
```

- [ ] **Step 4: Add "pipeline:interrupted" to bus.py TUI_EVENT_TYPES**

In `forge/tui/bus.py`, add to the list after `"pipeline:all_tasks_done"`:
```python
"pipeline:interrupted",
```

- [ ] **Step 5: Add phase banners to pipeline.py**

In `forge/tui/screens/pipeline.py`, add to `_PHASE_BANNER` dict:
```python
"partial_success": ("⚠ Partial Success", "#d29922"),
"retrying":        ("⟳ Retrying Failed",  "#f0883e"),
"interrupted":     ("⏸ Interrupted",      "#d29922"),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run --extra dev pytest forge/tui/state_test.py -v -k "all_tasks_done or interrupted"`
Expected: PASS

- [ ] **Step 7: Run all TUI tests to verify no regressions**

Run: `uv run --extra dev pytest forge/tui/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add forge/tui/state.py forge/tui/bus.py forge/tui/screens/pipeline.py forge/tui/state_test.py
git commit -m "feat: TUI state machine handles partial_success, retrying, interrupted phases"
```

---

### Task 6: ShortcutBar widget + FollowUpTextArea escape fix

**Files:**
- Create: `forge/tui/widgets/shortcut_bar.py`
- Modify: `forge/tui/widgets/followup_input.py:39-44` (FollowUpTextArea)
- Test: `forge/tui/widgets/shortcut_bar_test.py`

- [ ] **Step 1: Write failing test for ShortcutBar**

```python
# forge/tui/widgets/shortcut_bar_test.py (new file)
from forge.tui.widgets.shortcut_bar import ShortcutBar

def test_shortcut_bar_renders_keys():
    bar = ShortcutBar([("Enter", "Create PR"), ("r", "Retry Failed")])
    rendered = bar.render()
    text = str(rendered)
    assert "Enter" in text
    assert "Create PR" in text
    assert "r" in text
    assert "Retry Failed" in text

def test_shortcut_bar_empty():
    bar = ShortcutBar([])
    rendered = bar.render()
    assert str(rendered) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/tui/widgets/shortcut_bar_test.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Create ShortcutBar widget**

```python
# forge/tui/widgets/shortcut_bar.py
"""Universal shortcut bar — pinned to screen bottom, shows available keys."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from rich.text import Text


class ShortcutBar(Widget):
    """Persistent bottom bar showing available keyboard shortcuts.

    Usage:
        bar = ShortcutBar([("Enter", "Create PR"), ("r", "Retry")])
        bar.shortcuts = [("d", "View Diff")]  # Update dynamically
    """

    DEFAULT_CSS = """
    ShortcutBar {
        dock: bottom;
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    """

    shortcuts: reactive[list[tuple[str, str]]] = reactive(list, layout=True)

    def __init__(
        self,
        shortcuts: list[tuple[str, str]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.shortcuts = shortcuts or []

    def render(self) -> Text:
        if not self.shortcuts:
            return Text("")
        parts = Text()
        for i, (key, label) in enumerate(self.shortcuts):
            if i > 0:
                parts.append("  ")
            parts.append(f"[{key}]", style="bold bright_cyan")
            parts.append(f" {label}")
        return parts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/tui/widgets/shortcut_bar_test.py -v`
Expected: PASS

- [ ] **Step 5: Fix FollowUpTextArea escape binding**

In `forge/tui/widgets/followup_input.py`, find the `FollowUpTextArea` class (around line 39). Replace its BINDINGS:

```python
class FollowUpTextArea(TextArea):
    """TextArea subclass with escape-to-unfocus and clear-input bindings."""

    BINDINGS = [
        Binding("ctrl+u", "clear_input", "Clear", show=False, priority=True),
        Binding("escape", "unfocus", "Back", show=False, priority=True),
    ]

    def action_clear_input(self) -> None:
        """Clear the text area content and reset cursor."""
        self.text = ""
        self.move_cursor((0, 0))

    def action_unfocus(self) -> None:
        """Return focus to the parent screen so keybindings work again."""
        self.screen.focus()
```

- [ ] **Step 6: Run all widget tests**

Run: `uv run --extra dev pytest forge/tui/widgets/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add forge/tui/widgets/shortcut_bar.py forge/tui/widgets/shortcut_bar_test.py forge/tui/widgets/followup_input.py
git commit -m "feat: add ShortcutBar widget + fix FollowUpTextArea escape trap"
```

---

### Task 7: FinalApprovalScreen partial mode + PR body update

**Files:**
- Modify: `forge/tui/screens/final_approval.py` (entire screen)
- Modify: `forge/tui/pr_creator.py:28-44` (generate_pr_body)
- Test: `forge/tui/screens/final_approval_test.py`, `forge/tui/pr_creator_test.py`

- [ ] **Step 1: Write failing test for partial mode task rendering**

```python
# In forge/tui/screens/final_approval_test.py (append)
from forge.tui.screens.final_approval import format_task_table

def test_format_task_table_partial_mode():
    tasks = [
        {"title": "Auth", "state": "done", "added": 100, "removed": 10},
        {"title": "API", "state": "error", "error": "timed out (5 attempts)"},
        {"title": "Tests", "state": "blocked", "error": "blocked by API"},
    ]
    result = format_task_table(tasks)
    assert "✅" in result  # done task
    assert "❌" in result  # error task
    assert "⚠️" in result or "⚠" in result  # blocked task
    assert "Auth" in result
    assert "timed out" in result
    assert "blocked by API" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/tui/screens/final_approval_test.py -v -k "partial_mode"`
Expected: FAIL — format_task_table doesn't handle state/error fields

- [ ] **Step 3: Update format_task_table to handle all task states**

In `forge/tui/screens/final_approval.py`, rewrite `format_task_table` (lines 32-45):

```python
def format_task_table(tasks: list[dict]) -> str:
    """Format task table with status icons based on task state."""
    lines: list[str] = []
    for t in tasks:
        title = t.get("title", "?")
        state = t.get("state", t.get("review", "?"))

        if state == "done":
            added = t.get("added", 0)
            removed = t.get("removed", 0)
            files = t.get("files", 0)
            tp = t.get("tests_passed", 0)
            tt = t.get("tests_total", 0)
            stats = f"+{added}/-{removed}"
            if tt > 0:
                stats += f"  tests: {tp}/{tt}"
            if files > 0:
                stats += f"  {files} files"
            lines.append(f"  [#3fb950]\u2705[/] [bold]{title}[/]  [#8b949e]{stats}[/]")
        elif state == "error":
            error = t.get("error", "failed")
            lines.append(f"  [#f85149]\u274c[/] [bold]{title}[/]  [#f85149]{error}[/]")
        elif state == "blocked":
            error = t.get("error", "blocked by dependency")
            lines.append(f"  [#d29922]\u26a0\ufe0f[/] [bold]{title}[/]  [#d29922]{error}[/]")
        elif state == "cancelled":
            lines.append(f"  [#8b949e]\u2718[/] [bold]{title}[/]  [#8b949e]cancelled[/]")
        else:
            # Legacy: review-based display
            review = t.get("review", "?")
            icon = "[#3fb950]\u2713[/]" if review == "passed" else "[#f85149]\u2717[/]"
            added = t.get("added", 0)
            removed = t.get("removed", 0)
            lines.append(f"  {icon} [bold]{title}[/]  [#8b949e]+{added}/-{removed}[/]")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/tui/screens/final_approval_test.py -v -k "partial_mode"`
Expected: PASS

- [ ] **Step 5: Update FinalApprovalScreen to accept partial mode**

Modify `FinalApprovalScreen.__init__` to accept `partial: bool = False`:

```python
def __init__(
    self,
    stats: dict,
    tasks: list[dict],
    pipeline_branch: str = "",
    partial: bool = False,
    **kwargs,
) -> None:
    super().__init__(**kwargs)
    self._stats = stats
    self._tasks = tasks
    self._pipeline_branch = pipeline_branch
    self._partial = partial
```

Update the header in `compose()`:
```python
if self._partial:
    done = sum(1 for t in self._tasks if t.get("state") == "done")
    total = len(self._tasks)
    header = f"Pipeline Partial — {done}/{total} Tasks Completed"
else:
    header = "Pipeline Complete — Final Approval"
```

Update BINDINGS — keep all bindings defined, but use `check_action` to dynamically show/hide `r` and `s` based on partial mode:
```python
BINDINGS = [
    Binding("enter", "create_pr", "Create PR", show=True, priority=True),
    Binding("d", "view_diff", "View Diff", show=True),
    Binding("r", "rerun", "Re-run Failed", show=True),
    Binding("s", "skip_failed", "Skip & Finish", show=True),
    Binding("f", "focus_followup", "Follow Up", show=True),
    Binding("n", "new_task", "New Task", show=True),
    Binding("ctrl+s", "submit_followup", "Submit Follow-up", show=False),
    Binding("escape", "app.pop_screen", "Cancel", show=True),
]

def check_action(self, action: str, parameters: tuple) -> bool | None:
    """Dynamically enable/disable actions based on partial mode."""
    if action in ("rerun", "skip_failed"):
        return self._partial  # only available in partial mode
    if action == "new_task":
        return not self._partial  # only available in full mode
    return True
```

Note: Textual's `check_action` returning `False` hides the binding from the footer and disables it. This handles the dynamic visibility without needing to override `_get_bindings()`.

Add new action method:
```python
def action_skip_failed(self) -> None:
    """Skip all failed tasks and finish the pipeline."""
    self.post_message(self.SkipFailed())
```

Add the SkipFailed message class alongside existing ones:
```python
class SkipFailed(Message):
    pass
```

Add the ShortcutBar to `compose()`:
```python
from forge.tui.widgets.shortcut_bar import ShortcutBar

# At end of compose():
if self._partial:
    yield ShortcutBar([
        ("Enter", "Create PR (completed only)"),
        ("r", "Retry Failed"),
        ("s", "Skip & Finish"),
        ("d", "View Diff"),
        ("f", "Follow Up"),
        ("Esc", "Back"),
    ])
else:
    yield ShortcutBar([
        ("Enter", "Create PR"),
        ("d", "View Diff"),
        ("f", "Follow Up"),
        ("n", "New Task"),
        ("Esc", "Back"),
    ])
```

- [ ] **Step 6: Write failing test for PR body with failed tasks**

```python
# In forge/tui/pr_creator_test.py (create if needed)
from forge.tui.pr_creator import generate_pr_body

def test_pr_body_with_failed_tasks():
    body = generate_pr_body(
        tasks=[
            {"title": "Auth", "added": 100, "removed": 10, "files": 3},
            {"title": "Docs", "added": 50, "removed": 0, "files": 1},
        ],
        failed_tasks=[
            {"title": "API", "error": "timed out (5 attempts)"},
        ],
        time="12m 30s",
        cost=6.57,
        questions=[],
    )
    assert "Completed Tasks" in body
    assert "✅" in body
    assert "Failed Tasks" in body
    assert "❌" in body
    assert "API" in body
    assert "timed out" in body
```

- [ ] **Step 7: Run test to verify it fails**

Run: `uv run --extra dev pytest forge/tui/pr_creator_test.py -v -k "failed_tasks"`
Expected: FAIL — generate_pr_body doesn't accept failed_tasks

- [ ] **Step 8: Update generate_pr_body to handle failed tasks**

In `forge/tui/pr_creator.py`, update `generate_pr_body`:

```python
def generate_pr_body(
    *,
    tasks: list[dict],
    failed_tasks: list[dict] | None = None,
    time: str,
    cost: float,
    questions: list[dict],
) -> str:
    total = len(tasks) + (len(failed_tasks) if failed_tasks else 0)
    completed = len(tasks)

    if failed_tasks:
        lines = ["## Summary", f"Built by Forge pipeline \u2022 {total} tasks \u2022 {completed}/{total} completed \u2022 {time} \u2022 ${cost:.2f}", ""]
        lines.append("## Completed Tasks")
    else:
        lines = ["## Summary", f"Built by Forge pipeline \u2022 {total} tasks \u2022 {time} \u2022 ${cost:.2f}", ""]
        lines.append("## Tasks")

    for t in tasks:
        added = t.get("added", 0)
        removed = t.get("removed", 0)
        files = t.get("files", 0)
        lines.append(f"- \u2705 **{t['title']}** \u2014 +{added}/-{removed}, {files} files")

    if failed_tasks:
        lines.append("")
        lines.append("## Failed Tasks (not included in this PR)")
        for t in failed_tasks:
            error = t.get("error", "failed")
            lines.append(f"- \u274c **{t['title']}** \u2014 {error}")

    if questions:
        lines.append("")
        lines.append("## Human Decisions")
        for q in questions:
            lines.append(f"- **Q:** {q['question']} \u2192 **A:** {q['answer']}")

    lines.extend(["", "\U0001f916 Built with [Forge](https://github.com/tarunms7/forge-orchestrator)"])
    return "\n".join(lines)
```

- [ ] **Step 9: Run test to verify it passes**

Run: `uv run --extra dev pytest forge/tui/pr_creator_test.py -v`
Expected: PASS

- [ ] **Step 10: Run all screen tests**

Run: `uv run --extra dev pytest forge/tui/screens/ -v`
Expected: All PASS

- [ ] **Step 11: Commit**

```bash
git add forge/tui/screens/final_approval.py forge/tui/screens/final_approval_test.py forge/tui/pr_creator.py forge/tui/pr_creator_test.py
git commit -m "feat: FinalApprovalScreen partial mode with error/blocked display + PR body update"
```

---

### Task 8: App handlers — follow-up, re-run, skip, graceful quit, resume, partial_success phase

**Files:**
- Modify: `forge/tui/app.py` (multiple sections)
- Test: `forge/tui/app_db_test.py`

This is the largest task. It wires everything together.

- [ ] **Step 1: Add on_final_approval_screen_follow_up handler to app.py**

In `forge/tui/app.py`, after the existing `on_final_approval_screen_create_pr` method (~line 325), add:

```python
async def on_final_approval_screen_follow_up(self, event) -> None:
    """User submitted a follow-up prompt from FinalApprovalScreen."""
    if not self._db or not self._pipeline_id:
        self.notify("No pipeline context for follow-up.", severity="error")
        return

    prompt = event.prompt
    if not prompt.strip():
        return

    # Count existing follow-up tasks to generate unique ID
    tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
    followup_n = sum(1 for t in tasks if "-followup-" in t.id) + 1
    prefix = self._pipeline_id[:8]
    task_id = f"{prefix}-followup-{followup_n}"

    # Depend on all DONE tasks so agent sees completed work
    done_ids = [t.id for t in tasks if t.state == "done"]

    await self._db.create_task(
        id=task_id,
        title=prompt[:80],
        description=prompt,
        files=[],
        depends_on=done_ids,
        complexity="medium",
        pipeline_id=self._pipeline_id,
    )

    # Update state and re-enter execution
    self._state.phase = "executing"
    self._state._notify("phase")
    self._final_approval_pushed = False

    # Pop back to pipeline screen
    while len(self.screen_stack) > 2:
        self.pop_screen()

    # Resume execution
    await self._resume_execution()
```

- [ ] **Step 2: Add on_final_approval_screen_rerun handler**

```python
async def on_final_approval_screen_rerun(self, event) -> None:
    """User wants to retry failed tasks."""
    if not self._db or not self._pipeline_id:
        return

    tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
    reset_count = 0
    for t in tasks:
        if t.state in ("error", "blocked"):
            await self._db.update_task_state(t.id, "todo")
            # Reset retry_count for blocked tasks (they never ran)
            if t.state == "blocked":
                # blocked tasks have retry_count=0, nothing to reset
                pass
            reset_count += 1

    if reset_count == 0:
        self.notify("No failed tasks to retry.", severity="warning")
        return

    await self._db.update_pipeline_status(self._pipeline_id, "retrying")
    self._state.phase = "retrying"
    self._state._notify("phase")
    self._final_approval_pushed = False

    while len(self.screen_stack) > 2:
        self.pop_screen()

    await self._resume_execution()
```

- [ ] **Step 3: Add on_final_approval_screen_skip_failed handler**

```python
async def on_final_approval_screen_skip_failed(self, event) -> None:
    """User wants to skip failed tasks and finish."""
    if not self._db or not self._pipeline_id:
        return

    tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
    for t in tasks:
        if t.state in ("error", "blocked"):
            await self._db.update_task_state(t.id, "cancelled")

    await self._db.update_pipeline_status(self._pipeline_id, "complete")
    self._state.phase = "final_approval"
    self._state._notify("phase")

    # Rebuild final approval screen in full mode
    self._final_approval_pushed = False
    while len(self.screen_stack) > 2:
        self.pop_screen()
    self._push_final_approval()
```

- [ ] **Step 4: Add _resume_execution helper**

```python
async def _resume_execution(self) -> None:
    """Re-enter the daemon execution loop for remaining TODO tasks."""
    if not self._daemon or not self._graph or not self._db:
        self.notify("Cannot resume: missing context.", severity="error")
        return

    self._daemon_task = asyncio.create_task(
        self._daemon.execute(self._graph, self._db, pipeline_id=self._pipeline_id, resume=True)
    )
    self._daemon_task.add_done_callback(self._on_daemon_done)
```

- [ ] **Step 5: Update phase watcher for partial_success**

In `_on_state_change` (line ~144), add handling for `partial_success`:

```python
if phase == "final_approval" and not self._final_approval_pushed:
    self._final_approval_pushed = True
    self._push_final_approval()
elif phase == "partial_success" and not self._final_approval_pushed:
    self._final_approval_pushed = True
    self._push_final_approval(partial=True)
```

Update `_push_final_approval` to accept `partial` param:
```python
def _push_final_approval(self, partial: bool = False) -> None:
```

And pass it through to `FinalApprovalScreen`:
```python
self.push_screen(FinalApprovalScreen(
    stats=stats, tasks=task_summaries, pipeline_branch=pipeline_branch,
    partial=partial,
))
```

Also update `task_summaries` construction to include state and error info:
```python
task_summaries = [
    {
        "title": t.get("title", ""),
        "state": t.get("state", "done"),
        "added": t.get("merge_result", {}).get("linesAdded", 0),
        "removed": t.get("merge_result", {}).get("linesRemoved", 0),
        "files": t.get("merge_result", {}).get("filesChanged", 0),
        "tests_passed": t.get("tests_passed", 0),
        "tests_total": t.get("tests_total", 0),
        "review": "passed" if t.get("state") == "done" else "failed",
        "error": t.get("error", ""),
    }
    for t in tasks_list
]
```

- [ ] **Step 6: Update graceful quit**

Replace `action_quit_app` (lines 643-652):

```python
def action_quit_app(self) -> None:
    if self._daemon_task and not self._daemon_task.done():
        if getattr(self, "_force_quit", False):
            # Second press: graceful shutdown
            asyncio.create_task(self._graceful_quit())
        else:
            self.notify("Pipeline running. Press q again to quit (tasks will be saved).", severity="warning")
            self._force_quit = True
    else:
        self.exit()

async def _graceful_quit(self) -> None:
    """Gracefully shut down: cancel daemon, reset stuck tasks, mark interrupted."""
    # Cancel daemon task
    if self._daemon_task and not self._daemon_task.done():
        self._daemon_task.cancel()
        try:
            await self._daemon_task
        except (asyncio.CancelledError, Exception):
            pass

    # Reset stuck tasks and mark pipeline interrupted
    if self._db and self._pipeline_id:
        tasks = await self._db.list_tasks_by_pipeline(self._pipeline_id)
        non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
        for t in tasks:
            if t.state in non_terminal:
                await self._db.update_task_state(t.id, "todo")

        # Release all agents
        prefix = self._pipeline_id[:8]
        agents = await self._db.list_agents(prefix=prefix)
        for a in agents:
            if a.state != "idle":
                await self._db.release_agent(a.id)

        await self._db.update_pipeline_status(self._pipeline_id, "interrupted")
        await self._db.clear_executor_info(self._pipeline_id)

        try:
            await self._daemon._emit("pipeline:interrupted", {
                "summary": {t.id: t.state for t in tasks},
            }, db=self._db, pipeline_id=self._pipeline_id)
        except Exception:
            pass

    self.exit()
```

- [ ] **Step 7: Add executor tracking to daemon execution start**

In `forge/core/daemon.py`, at the start of `_execution_loop_inner` (after the `_active_tasks` initialization), add:

```python
import os
import uuid
self._executor_token = str(uuid.uuid4())
if pipeline_id:
    await db.set_executor_info(pipeline_id, pid=os.getpid(), token=self._executor_token)
```

And in `_execution_loop`'s finally block (after `_shutdown_active_tasks`):
```python
if pipeline_id:
    await db.clear_executor_info(pipeline_id)
```

Add the takeover check inside the dispatch cycle (before dispatching, after backpressure check):
```python
# Check for session takeover
if pipeline_id:
    pipeline_rec = await db.get_pipeline(pipeline_id)
    if pipeline_rec and pipeline_rec.executor_token and pipeline_rec.executor_token != self._executor_token:
        console.print("[yellow]Pipeline taken over by another session. Exiting.[/yellow]")
        break
```

- [ ] **Step 8: Update resume flow in app.py**

In `on_pipeline_list_selected` (~line 675), add handling for `interrupted` status:

```python
# After the existing "planned" resume check:
if pipeline.status in ("interrupted", "partial_success"):
    # Replay events to reconstruct state
    events = await self._db.list_events(pipeline_id)
    state = TuiState()
    for evt in events:
        state.apply_event(evt.event_type, evt.payload or {})

    self._state = state
    self._pipeline_id = pipeline_id
    self._pipeline_start_time = time.time()

    # Reconstruct daemon and graph
    graph_json = pipeline.task_graph_json
    if graph_json:
        import json
        from forge.core.models import TaskGraph
        self._graph = TaskGraph.model_validate_json(graph_json)

    # ForgeDaemon requires project_dir (first positional arg)
    self._daemon = ForgeDaemon(
        project_dir=pipeline.project_dir,
        settings=self._settings,
        event_emitter=self._source,
    )

    self.push_screen(PipelineScreen(state))

    if pipeline.status == "interrupted":
        # Reset any stuck tasks and resume execution
        tasks = await self._db.list_tasks_by_pipeline(pipeline_id)
        non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
        for t in tasks:
            if t.state in non_terminal:
                await self._db.update_task_state(t.id, "todo")
        # Re-fetch tasks AFTER reset so counts reflect current state
        tasks = await self._db.list_tasks_by_pipeline(pipeline_id)

        await self._db.update_pipeline_status(pipeline_id, "executing")
        await self._resume_execution()
        self.notify(f"Resumed pipeline — {sum(1 for t in tasks if t.state == 'done')}/{len(tasks)} tasks done", severity="information")

    elif pipeline.status == "partial_success":
        # Go directly to partial approval screen
        self._final_approval_pushed = True
        self._push_final_approval(partial=True)

    return
```

- [ ] **Step 9: Write tests for app handlers**

```python
# In forge/tui/app_handlers_test.py (create new)
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


@pytest.mark.asyncio
async def test_graceful_quit_resets_stuck_tasks():
    """Graceful quit should reset non-terminal tasks to TODO and mark pipeline interrupted."""
    from forge.storage.db import Database
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir="/tmp", status="executing",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    t1 = await db.create_task(id="t1", title="A", description="A", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    t2 = await db.create_task(id="t2", title="B", description="B", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.update_task_state("t1", "in_progress")
    await db.update_task_state("t2", "done")

    # Simulate quit: reset in_progress to todo, mark interrupted
    tasks = await db.list_tasks_by_pipeline(pid)
    non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
    for t in tasks:
        if t.state in non_terminal:
            await db.update_task_state(t.id, "todo")
    await db.update_pipeline_status(pid, "interrupted")

    p = await db.get_pipeline(pid)
    assert p.status == "interrupted"
    t1_row = await db.get_task("t1")
    assert t1_row.state == "todo"
    t2_row = await db.get_task("t2")
    assert t2_row.state == "done"


@pytest.mark.asyncio
async def test_rerun_resets_error_and_blocked_to_todo():
    """Re-run handler should reset error and blocked tasks to TODO."""
    from forge.storage.db import Database
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir="/tmp", status="partial_success",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    await db.create_task(id="t1", title="A", description="A", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t2", title="B", description="B", files=[], depends_on=["t1"], complexity="low", pipeline_id=pid)
    await db.update_task_state("t1", "error")
    await db.update_task_state("t2", "blocked")

    # Simulate re-run: reset error/blocked to todo
    tasks = await db.list_tasks_by_pipeline(pid)
    for t in tasks:
        if t.state in ("error", "blocked"):
            await db.update_task_state(t.id, "todo")
    await db.update_pipeline_status(pid, "retrying")

    t1_row = await db.get_task("t1")
    t2_row = await db.get_task("t2")
    assert t1_row.state == "todo"
    assert t2_row.state == "todo"
    p = await db.get_pipeline(pid)
    assert p.status == "retrying"


@pytest.mark.asyncio
async def test_skip_failed_cancels_error_blocked_tasks():
    """Skip handler should cancel error/blocked tasks and mark pipeline complete."""
    from forge.storage.db import Database
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir="/tmp", status="partial_success",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    await db.create_task(id="t1", title="A", description="A", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t2", title="B", description="B", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.update_task_state("t1", "error")
    await db.update_task_state("t2", "blocked")

    # Simulate skip: cancel error/blocked, mark complete
    tasks = await db.list_tasks_by_pipeline(pid)
    for t in tasks:
        if t.state in ("error", "blocked"):
            await db.update_task_state(t.id, "cancelled")
    await db.update_pipeline_status(pid, "complete")

    t1_row = await db.get_task("t1")
    t2_row = await db.get_task("t2")
    assert t1_row.state == "cancelled"
    assert t2_row.state == "cancelled"
    p = await db.get_pipeline(pid)
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_resume_refetches_tasks_after_reset():
    """Resume should re-fetch tasks after resetting stuck ones so counts are accurate."""
    from forge.storage.db import Database
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir="/tmp", status="interrupted",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    await db.create_task(id="t1", title="A", description="A", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t2", title="B", description="B", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.update_task_state("t1", "done")
    await db.update_task_state("t2", "in_progress")  # stuck

    # Simulate resume reset
    tasks = await db.list_tasks_by_pipeline(pid)
    non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
    for t in tasks:
        if t.state in non_terminal:
            await db.update_task_state(t.id, "todo")

    # Re-fetch to get accurate counts
    tasks = await db.list_tasks_by_pipeline(pid)
    done_count = sum(1 for t in tasks if t.state == "done")
    assert done_count == 1
    todo_count = sum(1 for t in tasks if t.state == "todo")
    assert todo_count == 1
```

- [ ] **Step 10: Run handler tests to verify they pass**

Run: `uv run --extra dev pytest forge/tui/app_handlers_test.py -v`
Expected: All 4 tests PASS (these test the DB-level logic; handler wiring is integration-tested in Task 10)

- [ ] **Step 11: Run all tests**

Run: `uv run --extra dev pytest forge/ -v --timeout=60`
Expected: All tests PASS

- [ ] **Step 12: Commit**

```bash
git add forge/tui/app.py forge/core/daemon.py forge/tui/app_handlers_test.py
git commit -m "feat: wire all handlers — follow-up, re-run, skip, graceful quit, resume, executor tracking"
```

---

## Chunk 3: Integration — ShortcutBar on All Screens

### Task 9: Add ShortcutBar to every screen

**Files:**
- Modify: `forge/tui/screens/final_approval.py:48` (DiffScreen — already has FinalApproval bar from Task 7)
- Modify: `forge/tui/screens/pipeline.py:123` (PipelineScreen)
- Modify: `forge/tui/screens/home.py:75` (HomeScreen)
- Modify: `forge/tui/screens/plan_approval.py:110` (PlanApprovalScreen)
- Modify: `forge/tui/screens/review.py:31` (ReviewScreen)
- Modify: `forge/tui/screens/settings.py:93` (SettingsScreen)
- Modify: `forge/tui/widgets/shortcut_bar.py` (make shortcuts reactive for dynamic updates)
- Test: `forge/tui/widgets/shortcut_bar_test.py` (append reactive test)

This task adds the ShortcutBar to ALL screens that don't have it yet. FinalApprovalScreen already got its bar in Task 7.

- [ ] **Step 1: Make ShortcutBar shortcuts reactive**

Update `forge/tui/widgets/shortcut_bar.py` to support dynamic updates:

```python
from textual.reactive import reactive

class ShortcutBar(Static):
    shortcuts: reactive[list[tuple[str, str]]] = reactive(list, layout=True)

    def __init__(self, shortcuts: list[tuple[str, str]] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        if shortcuts:
            self.shortcuts = shortcuts

    def watch_shortcuts(self, _old: list, _new: list) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        from rich.text import Text
        parts = Text()
        for i, (key, desc) in enumerate(self.shortcuts):
            if i > 0:
                parts.append("  \u2502  ", style="dim")
            parts.append(f" {key} ", style="bold bright_cyan on #1a1a2e")
            parts.append(f" {desc} ", style="bright_cyan")
        self.update(parts)
```

- [ ] **Step 2: Write test for reactive shortcuts**

```python
# Append to forge/tui/widgets/shortcut_bar_test.py
def test_shortcut_bar_reactive_update():
    bar = ShortcutBar([("a", "Action A")])
    # Update shortcuts reactively
    bar.shortcuts = [("b", "Action B"), ("c", "Action C")]
    # The watch_shortcuts trigger will call _rebuild
    assert len(bar.shortcuts) == 2
```

- [ ] **Step 3: Run shortcut bar tests**

Run: `uv run --extra dev pytest forge/tui/widgets/shortcut_bar_test.py -v`
Expected: PASS

- [ ] **Step 4: Add ShortcutBar to PipelineScreen**

In `forge/tui/screens/pipeline.py` (class at line 123), import ShortcutBar and add to end of `compose()`:

```python
from forge.tui.widgets.shortcut_bar import ShortcutBar

# At end of compose():
yield ShortcutBar([
    ("d", "View Diff"),
    ("\u2191\u2193", "Select Task"),
    ("q", "Quit (tasks saved)"),
])
```

Also add a phase watcher to update shortcuts dynamically:

```python
def _update_shortcut_bar(self, phase: str) -> None:
    """Update shortcut bar based on current pipeline phase."""
    try:
        bar = self.query_one(ShortcutBar)
    except Exception:
        return
    if phase == "awaiting_input":
        bar.shortcuts = [
            ("Enter", "Answer Question"),
            ("d", "View Diff"),
            ("\u2191\u2193", "Select Task"),
            ("q", "Quit (tasks saved)"),
        ]
    elif phase in ("partial_success", "retrying"):
        bar.shortcuts = [
            ("Enter", "View Results"),
            ("q", "Quit (tasks saved)"),
        ]
    else:
        bar.shortcuts = [
            ("d", "View Diff"),
            ("\u2191\u2193", "Select Task"),
            ("q", "Quit (tasks saved)"),
        ]
```

Wire this to the phase change handler (wherever PipelineScreen reacts to state updates).

- [ ] **Step 5: Add ShortcutBar to HomeScreen**

In `forge/tui/screens/home.py` (class at line 75), import and add to end of `compose()`:

```python
from forge.tui.widgets.shortcut_bar import ShortcutBar

# At end of compose():
yield ShortcutBar([
    ("Ctrl+S", "Submit Task"),
    ("\u2191\u2193", "History"),
    ("Enter", "Resume Selected"),
    ("q", "Quit"),
])
```

- [ ] **Step 6: Add ShortcutBar to DiffScreen**

In `forge/tui/screens/final_approval.py` (DiffScreen class at line 48), import and add to end of `compose()`:

```python
from forge.tui.widgets.shortcut_bar import ShortcutBar

# At end of DiffScreen.compose():
yield ShortcutBar([
    ("\u2191\u2193", "Scroll"),
    ("Esc", "Back"),
])
```

- [ ] **Step 7: Add ShortcutBar to PlanApprovalScreen**

In `forge/tui/screens/plan_approval.py` (class at line 110), import and add to end of `compose()`:

```python
from forge.tui.widgets.shortcut_bar import ShortcutBar

# At end of compose():
yield ShortcutBar([
    ("Enter", "Approve Plan"),
    ("\u2191\u2193", "Scroll"),
    ("Esc", "Cancel"),
])
```

- [ ] **Step 8: Add ShortcutBar to ReviewScreen**

In `forge/tui/screens/review.py` (class at line 31), import and add to end of `compose()`:

```python
from forge.tui.widgets.shortcut_bar import ShortcutBar

# At end of compose():
yield ShortcutBar([
    ("a", "Approve"),
    ("x", "Reject"),
    ("e", "Open in Editor"),
    ("\u2191\u2193", "Scroll"),
    ("Esc", "Back"),
])
```

- [ ] **Step 9: Add ShortcutBar to SettingsScreen**

In `forge/tui/screens/settings.py` (class at line 93), import and add to end of `compose()`:

```python
from forge.tui.widgets.shortcut_bar import ShortcutBar

# At end of compose():
yield ShortcutBar([
    ("Enter", "Save"),
    ("Tab", "Next Field"),
    ("Esc", "Back"),
])
```

- [ ] **Step 10: Run all screen tests to check for regressions**

Run: `uv run --extra dev pytest forge/tui/screens/ -v`
Expected: All PASS. If snapshot/render tests fail due to the new ShortcutBar widget in compose():
- Tests in `home_test.py`, `pipeline_test.py`, `plan_approval_test.py`, `review_test.py`, `settings_test.py` may need updated assertions or snapshots
- Tests that construct screens may need to account for ShortcutBar in widget queries
- Fix each failing test by updating the expected output

- [ ] **Step 11: Commit**

```bash
git add forge/tui/screens/ forge/tui/widgets/shortcut_bar.py forge/tui/widgets/shortcut_bar_test.py
git commit -m "feat: add ShortcutBar to all TUI screens with phase-based updates"
```

---

### Task 10: Integration tests + run full test suite

**Files:**
- Create: `forge/tests/__init__.py` (empty, create directory)
- Create: `forge/tests/integration/__init__.py` (empty, create directory)
- Create: `forge/tests/integration/test_pipeline_lifecycle.py`
- Test: Full test suite

Note: `forge/tests/integration/` does not exist yet. Create the directory and `__init__.py` files.

- [ ] **Step 1: Write integration tests for key lifecycle flows**

```python
# In forge/tests/integration/test_pipeline_lifecycle.py (create new)
"""Integration tests for resilient pipeline lifecycle flows.

Tests the key flows from the spec's Flow Matrix (Section 9):
- Flow A: Full success path
- Flow B: Partial success path (some tasks fail)
- Flow C: Retry path (error/blocked -> todo -> execute)
- Flow F: Skip & Finish path
- Flow G/H: Quit + resume path
"""
import pytest
from forge.storage.db import Database
from forge.core.models import TaskState


@pytest.mark.asyncio
async def test_flow_a_full_success(tmp_path):
    """All tasks complete -> pipeline status = complete."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir=str(tmp_path), status="executing",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    for i in range(3):
        await db.create_task(id=f"t{i}", title=f"Task {i}", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
        await db.update_task_state(f"t{i}", "done")

    # Classify result
    tasks = await db.list_tasks_by_pipeline(pid)
    states = [t.state for t in tasks]
    done_count = states.count("done")
    error_count = states.count("error") + states.count("blocked")
    if error_count == 0:
        result = "complete"
    elif done_count == 0:
        result = "error"
    else:
        result = "partial_success"

    await db.update_pipeline_status(pid, result)
    p = await db.get_pipeline(pid)
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_flow_b_partial_success(tmp_path):
    """Some tasks fail -> pipeline status = partial_success."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir=str(tmp_path), status="executing",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    await db.create_task(id="t0", title="A", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t1", title="B", description="", files=[], depends_on=["t0"], complexity="low", pipeline_id=pid)
    await db.update_task_state("t0", "done")
    await db.update_task_state("t1", "error")

    tasks = await db.list_tasks_by_pipeline(pid)
    states = [t.state for t in tasks]
    done_count = states.count("done")
    error_count = states.count("error") + states.count("blocked")
    result = "complete" if error_count == 0 else ("error" if done_count == 0 else "partial_success")

    await db.update_pipeline_status(pid, result)
    p = await db.get_pipeline(pid)
    assert p.status == "partial_success"


@pytest.mark.asyncio
async def test_flow_c_retry_resets_and_resumes(tmp_path):
    """Retry: error/blocked -> todo, pipeline -> retrying -> complete."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir=str(tmp_path), status="partial_success",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    await db.create_task(id="t0", title="A", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t1", title="B", description="", files=[], depends_on=["t0"], complexity="low", pipeline_id=pid)
    await db.update_task_state("t0", "error")
    await db.update_task_state("t1", "blocked")

    # Retry: reset error/blocked to todo
    for t in await db.list_tasks_by_pipeline(pid):
        if t.state in ("error", "blocked"):
            await db.update_task_state(t.id, "todo")
    await db.update_pipeline_status(pid, "retrying")
    p = await db.get_pipeline(pid)
    assert p.status == "retrying"

    # Simulate successful retry
    await db.update_task_state("t0", "done")
    await db.update_task_state("t1", "done")
    await db.update_pipeline_status(pid, "complete")
    p = await db.get_pipeline(pid)
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_flow_f_skip_and_finish(tmp_path):
    """Skip: error/blocked -> cancelled, pipeline -> complete."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir=str(tmp_path), status="partial_success",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    await db.create_task(id="t0", title="A", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t1", title="B", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.update_task_state("t0", "done")
    await db.update_task_state("t1", "error")

    # Skip: cancel failed tasks
    for t in await db.list_tasks_by_pipeline(pid):
        if t.state in ("error", "blocked"):
            await db.update_task_state(t.id, "cancelled")
    await db.update_pipeline_status(pid, "complete")

    tasks = await db.list_tasks_by_pipeline(pid)
    states = {t.id: t.state for t in tasks}
    assert states["t0"] == "done"
    assert states["t1"] == "cancelled"
    p = await db.get_pipeline(pid)
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_flow_gh_quit_and_resume(tmp_path):
    """Quit: non-terminal -> todo, pipeline -> interrupted. Resume: interrupted -> executing."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = await db.create_pipeline(
        description="test", project_dir=str(tmp_path), status="executing",
        model_strategy="balanced", planner_cost_usd=0, budget_limit_usd=10,
    )
    await db.create_task(id="t0", title="A", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t1", title="B", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.update_task_state("t0", "done")
    await db.update_task_state("t1", "in_progress")

    # Quit: reset stuck, mark interrupted
    non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
    for t in await db.list_tasks_by_pipeline(pid):
        if t.state in non_terminal:
            await db.update_task_state(t.id, "todo")
    await db.update_pipeline_status(pid, "interrupted")

    p = await db.get_pipeline(pid)
    assert p.status == "interrupted"
    t1_row = await db.get_task("t1")
    assert t1_row.state == "todo"

    # Resume: interrupted -> executing
    await db.update_pipeline_status(pid, "executing")
    p = await db.get_pipeline(pid)
    assert p.status == "executing"
```

- [ ] **Step 2: Run integration tests**

Run: `uv run --extra dev pytest forge/tests/integration/test_pipeline_lifecycle.py -v`
Expected: All 5 integration tests PASS

- [ ] **Step 3: Run the entire test suite**

Run: `uv run --extra dev pytest forge/ -v --timeout=120`
Expected: All tests PASS with no regressions

- [ ] **Step 4: Fix any failing tests**

If tests fail, fix them. Common issues:
- Existing tests may need `partial=False` added to FinalApprovalScreen constructors
- State tests may need updated assertions for the new phase handling
- PR body tests may need `failed_tasks=None` added to calls
- Screen tests (`home_test.py`, `pipeline_test.py`, `plan_approval_test.py`, `review_test.py`, `settings_test.py`) may need updated assertions or snapshots due to the new ShortcutBar widget in `compose()`
- Fix each by updating the expected output to include the bar

- [ ] **Step 5: Run ruff linter**

Run: `uv run --extra dev ruff check forge/ --fix`
Expected: Clean or auto-fixed

- [ ] **Step 6: Final commit**

```bash
git add forge/tests/integration/test_pipeline_lifecycle.py
# Add any test fixes
git add forge/tui/screens/*_test.py forge/tui/app_handlers_test.py
git commit -m "test: integration tests for pipeline lifecycle + test suite fixes"
```

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-03-15-resilient-pipeline-lifecycle.md`. Ready to execute?

Use **superpowers:subagent-driven-development** — fresh subagent per task + two-stage review.
