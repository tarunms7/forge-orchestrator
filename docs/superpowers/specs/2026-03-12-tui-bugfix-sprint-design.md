# TUI Bugfix Sprint — Design Spec

**Date:** 2026-03-12
**Status:** Approved
**Scope:** Fix 4 critical TUI bugs degrading the pipeline monitoring experience

---

## Overview

Four bugs make the Forge TUI difficult to use during active pipeline execution:

1. **Agent output flickering + scroll lock** — lines appear and vanish; user cannot scroll up
2. **Diff view nearly empty** — shows only a few lines or nothing during execution
3. **Review screen navigation stuck** — pressing task numbers from ReviewScreen pops all screens
4. **No "back to home" from final screen** — `q` quits the whole TUI; no way to start a new task

All four have identified root causes and targeted fixes. Total: 5 files modified.

---

## Bug 1: Agent Output Flickering + Scroll Lock

### Root Cause

Two independent code paths update the `AgentOutput` widget simultaneously:

- **Fast path** (`_handle_agent_output_fast` → `append_line`): Called on every `agent_output` state change. Appends the latest line and schedules `scroll_to_end`.
- **Full refresh path** (`_refresh_all` → `update_output`): Called on `tasks`, `cost`, `phase`, `elapsed`, `planner_output` state changes. Replaces the widget's entire `_lines` list and re-renders both header and content.

The `elapsed` field changes every 1 second (timer in `app.py` line 401), triggering `_refresh_all` every second. During streaming:

1. `append_line` adds a line and schedules a render
2. Within 1 second, `_refresh_all` fires and calls `update_output`, which replaces `self._lines` entirely and calls `set_streaming(False)`
3. The freshly appended line is overwritten. Streaming indicator disappears and restarts.
4. Multiple concurrent `scroll_to_end` calls from both paths prevent user from scrolling up.

### Fix: Streaming Guard + Smart Scroll

**File: `forge/tui/screens/pipeline.py`**

In `_refresh_all()`, when the selected task is actively streaming (`tid in self._agent_streaming_tasks`), skip the `update_output` call. Instead, call a new `update_header()` method that only refreshes the header (task title/state) without touching the content or scroll position.

```python
# In _refresh_all(), around line 349:
tid = state.selected_task_id
if tid and tid in state.tasks:
    task = state.tasks[tid]
    lines = state.agent_output.get(tid, [])
    if tid in self._agent_streaming_tasks:
        # Streaming active — only update header, not content
        agent_output.update_header(tid, task.get("title"), task.get("state"))
    else:
        agent_output.update_output(tid, task.get("title"), task.get("state"), lines)
```

In `_update_streaming_lifecycle()`, when streaming ends for a task, perform a final full sync so any lines missed during the guard are rendered:

```python
# In _update_streaming_lifecycle(), when removing from _agent_streaming_tasks:
if tid in self._agent_streaming_tasks:
    self._agent_streaming_tasks.discard(tid)
    try:
        ao = self.query_one(AgentOutput)
        ao.set_streaming(False)
        # Final sync: full refresh to pick up any state accumulated during guard
        lines = state.agent_output.get(tid, [])
        task = state.tasks.get(tid, {})
        ao.update_output(tid, task.get("title"), task.get("state"), lines)
    except Exception:
        pass
```

**File: `forge/tui/widgets/agent_output.py`**

Add `update_header()` method:

```python
def update_header(self, task_id: str | None, title: str | None, state: str | None) -> None:
    """Update only the header line. Use during streaming to avoid replacing content."""
    self._task_id = task_id
    self._title = title
    self._state = state
    try:
        self.query_one("#agent-header", Static).update(
            format_header(task_id, title, state)
        )
    except Exception:
        pass
```

Add `_is_near_bottom()` helper and guard auto-scroll in `append_line`:

```python
def _is_near_bottom(self) -> bool:
    """Check if the scroll position is near the bottom (within 3 lines)."""
    try:
        scroll = self.query_one("#agent-scroll", VerticalScroll)
        return scroll.scroll_y >= scroll.virtual_size.height - scroll.size.height - 3
    except Exception:
        return True  # Default to auto-scroll if widget not ready

def append_line(self, line: str) -> None:
    self._lines.append(line)
    try:
        content = self.query_one("#agent-content", Static)
        content.update(
            format_output(
                self._lines, self._spinner_frame,
                streaming=self._streaming, typing_frame=self._typing_frame,
            )
        )
        # Only auto-scroll if user is already near the bottom
        if self._is_near_bottom():
            self.call_after_refresh(self._scroll_to_end)
    except Exception:
        pass
```

Also apply the same `_is_near_bottom()` guard in `update_output()` (line 193-194):

```python
# In update_output, replace unconditional scroll:
if lines and self._is_near_bottom():
    self.call_after_refresh(self._scroll_to_end)
```

### Files Changed

| File | Changes |
|------|---------|
| `forge/tui/widgets/agent_output.py` | Add `update_header()`, `_is_near_bottom()`. Guard scroll in `append_line()` and `update_output()`. |
| `forge/tui/screens/pipeline.py` | Streaming guard in `_refresh_all()`. Final sync in `_update_streaming_lifecycle()`. |

---

## Bug 2: Diff View Nearly Empty

### Root Cause

The `task:merge_result` event (emitted by `daemon_executor.py`) only includes stats:

```python
stats = _get_diff_stats(worktree_path, pipeline_branch=pipeline_branch)
await self._emit("task:merge_result", {"task_id": task_id, "success": True, "error": None, **stats})
```

`_get_diff_stats()` returns `{"linesAdded": X, "linesRemoved": Y}` — no `"diff"` key.

In `pipeline.py` line 370: `diff_text = task.get("diff", "")` → always empty.
In `review.py` line 95: `task.get("merge_result", {}).get("diff", "")` → always empty.

The actual diff is only generated on-demand by `FinalApprovalScreen._load_and_show_diff()` using `git diff main...{branch}`.

### Fix: On-Demand Diff Generation with Cache

**File: `forge/tui/screens/pipeline.py`**

Add a diff cache dict and an async method to generate diffs per task:

```python
def __init__(self, state: TuiState) -> None:
    super().__init__()
    self._state = state
    self._active_view: str = "output"
    self._agent_streaming_tasks: set[str] = set()
    self._review_streaming_tasks: set[str] = set()
    self._diff_cache: dict[str, str] = {}  # NEW: task_id -> diff text
```

```python
async def _load_task_diff(self, tid: str) -> str:
    """Generate diff for a task's changed files from the pipeline branch."""
    if tid in self._diff_cache:
        return self._diff_cache[tid]
    task = self._state.tasks.get(tid, {})
    branch = getattr(self._state, "pipeline_branch", "") or ""
    if not branch:
        return "No pipeline branch available yet."
    # Use task files to scope the diff; fall back to full branch diff
    files = task.get("files", [])
    cmd = ["git", "diff", f"main...{branch}"]
    if files:
        cmd += ["--"] + files
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            diff = stdout.decode(errors="replace")
        else:
            diff = f"git diff failed: {stderr.decode(errors='replace')}"
    except Exception as e:
        diff = f"Error running git diff: {e}"
    self._diff_cache[tid] = diff
    return diff
```

In `_refresh_all()`, replace the synchronous diff fetch with an async task launch when the diff view is active:

```python
# Replace lines ~370-371:
diff_text = task.get("diff", "")
diff_viewer.update_diff(tid, task.get("title", ""), diff_text)

# With:
if self._active_view == "diff":
    if tid in self._diff_cache:
        diff_viewer.update_diff(tid, task.get("title", ""), self._diff_cache[tid])
    else:
        diff_viewer.update_diff(tid, task.get("title", ""), "Loading diff...")
        asyncio.create_task(self._refresh_diff_async(tid))
```

```python
async def _refresh_diff_async(self, tid: str) -> None:
    """Fetch diff async and update the viewer."""
    diff = await self._load_task_diff(tid)
    try:
        diff_viewer = self.query_one(DiffViewer)
        task = self._state.tasks.get(tid, {})
        diff_viewer.update_diff(tid, task.get("title", ""), diff)
    except Exception:
        pass
```

Clear the cache entry for a task when its state changes (new merge may produce different diff):

```python
# In _on_state_change, when field == "tasks":
# Invalidate diff cache for tasks whose state changed
for tid in list(self._diff_cache):
    if tid in self._state.tasks:
        task = self._state.tasks[tid]
        if task.get("state") in ("in_progress", "in_review", "merging"):
            del self._diff_cache[tid]
```

**File: `forge/tui/state.py`**

Add `pipeline_branch` field to `TuiState`:

```python
self.pipeline_branch: str = ""
```

Populate it from the `pipeline:phase_changed` event when phase is `executing`, or from the pipeline DB record. The branch name is available in the `pipeline:plan_ready` or `pipeline:phase_changed` event data if the daemon includes it. If not available from events, it can be inferred from the pipeline record in the DB.

**File: `forge/tui/screens/review.py`**

Apply the same on-demand diff pattern. Replace line 95:

```python
# Before:
diff = task.get("merge_result", {}).get("diff", "")

# After:
diff = self._diff_cache.get(tid, "")
if not diff and tid not in self._diff_loading:
    self._diff_loading.add(tid)
    asyncio.create_task(self._load_diff(tid))
```

### Files Changed

| File | Changes |
|------|---------|
| `forge/tui/screens/pipeline.py` | Add `_diff_cache`, `_load_task_diff()`, `_refresh_diff_async()`, cache invalidation. |
| `forge/tui/screens/review.py` | Same on-demand diff loading pattern. |
| `forge/tui/state.py` | Add `pipeline_branch` field. |

---

## Bug 3: Review Screen Navigation Stuck

### Root Cause

`ReviewScreen.BINDINGS` does not include number keys (1-9). When the user presses a number:

1. ReviewScreen has no handler → key bubbles up to `ForgeApp`
2. `ForgeApp.BINDINGS` maps `1` → `action_switch_home()` which runs `while len(self.screen_stack) > 1: self.pop_screen()`
3. All screens are popped, landing on HomeScreen
4. No binding exists to return directly to ReviewScreen from HomeScreen during an active pipeline

### Fix: Add Task-Jump Bindings with Priority

**File: `forge/tui/screens/review.py`**

Add number bindings with `priority=True` to prevent bubbling, and an `escape` binding to go back:

```python
BINDINGS = [
    Binding("a", "approve", "Approve"),
    Binding("x", "reject", "Reject"),
    Binding("e", "edit", "Open in $EDITOR"),
    Binding("j", "cursor_down", "Down", show=False),
    Binding("k", "cursor_up", "Up", show=False),
    Binding("escape", "app.pop_screen", "Back", show=True),
    # Task jump — priority=True prevents bubble to app-level screen switch
    Binding("1", "jump_task(1)", show=False, priority=True),
    Binding("2", "jump_task(2)", show=False, priority=True),
    Binding("3", "jump_task(3)", show=False, priority=True),
    Binding("4", "jump_task(4)", show=False, priority=True),
    Binding("5", "jump_task(5)", show=False, priority=True),
    Binding("6", "jump_task(6)", show=False, priority=True),
    Binding("7", "jump_task(7)", show=False, priority=True),
    Binding("8", "jump_task(8)", show=False, priority=True),
    Binding("9", "jump_task(9)", show=False, priority=True),
]

def action_jump_task(self, index: int) -> None:
    """Jump to the Nth reviewable task (1-based)."""
    reviewable = [
        tid for tid in self._state.task_order
        if tid in self._state.tasks
        and self._state.tasks[tid]["state"] in _REVIEWABLE_STATES
    ]
    if 0 < index <= len(reviewable):
        self._state.selected_task_id = reviewable[index - 1]
        self._refresh()
```

### Files Changed

| File | Changes |
|------|---------|
| `forge/tui/screens/review.py` | Add 1-9 bindings with `priority=True`, escape binding, `action_jump_task()`. |

---

## Bug 4: No "Back to Home" from Final Screen

### Root Cause

`FinalApprovalScreen.BINDINGS` only includes: Enter (create PR), d (view diff), r (re-run), Escape (pop one screen). The app-level `q` binding triggers `action_quit_app()` which exits the entire TUI. There is no way to return to HomeScreen to start a new task.

### Fix: Add "New Task" Binding

**File: `forge/tui/screens/final_approval.py`**

```python
BINDINGS = [
    Binding("enter", "create_pr", "Create PR", show=True, priority=True),
    Binding("d", "view_diff", "View Diff", show=True),
    Binding("r", "rerun", "Re-run Failed", show=True),
    Binding("n", "new_task", "New Task", show=True),  # NEW
    Binding("escape", "app.pop_screen", "Cancel", show=True),
]

def action_new_task(self) -> None:
    """Pop all screens back to HomeScreen to start a new task."""
    while len(self.app.screen_stack) > 1:
        self.app.pop_screen()
```

Update the hint text at line 109:

```python
# Before:
yield Static("\n[#8b949e]Press Enter to create PR, d for diff, r to re-run, Esc to cancel[/]")

# After:
yield Static("\n[#8b949e]Enter: create PR  d: diff  r: re-run  n: new task  Esc: cancel[/]")
```

### Files Changed

| File | Changes |
|------|---------|
| `forge/tui/screens/final_approval.py` | Add `n` binding, `action_new_task()`, update hint text. |

---

## Complete File Change Summary

| File | Bug(s) | Description |
|------|--------|-------------|
| `forge/tui/widgets/agent_output.py` | 1 | `update_header()`, `_is_near_bottom()`, guarded scroll |
| `forge/tui/screens/pipeline.py` | 1, 2 | Streaming guard in `_refresh_all`, final sync, on-demand diff with cache |
| `forge/tui/screens/review.py` | 2, 3 | On-demand diff, number bindings with priority, escape, `action_jump_task()` |
| `forge/tui/screens/final_approval.py` | 4 | `n` binding, `action_new_task()`, updated hint |
| `forge/tui/state.py` | 2 | `pipeline_branch` field |
