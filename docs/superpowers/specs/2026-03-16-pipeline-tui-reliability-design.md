# Pipeline & TUI Reliability Fixes

**Date:** 2026-03-16
**Status:** Approved
**Scope:** 8 fixes across review pipeline, TUI, and planning UX

## Context

After testing the multi-pass planning pipeline on a real project (review-like-him Phase 3), several reliability and UX issues surfaced:

- Agent stuck in impossible retry loops due to out-of-scope test failures
- TUI shortcuts broken by textarea focus stealing
- Diff viewer not scrollable
- No visibility into which planning stage is running
- Inconsistent shortcut bars across screens
- Misleading keybinding labels

## Fix 1: Test Gate Scoping — Prevent Impossible Retry Loops

### Problem

`_find_related_test_files()` discovers tests by naming convention (e.g., `foo.py` → `test_foo.py`) but doesn't filter against the task's `allowed_files` scope. When the task spec says "change behavior X" but `test_X.py` (out of scope) asserts old behavior, the agent is trapped in an impossible loop:

```
Agent implements spec → test_X.py fails (can't modify) → reverts → reviewer says "spec not implemented" → retry → loop
```

### Design

Modify `_gate_test()` to accept an `allowed_files: list[str] | None` parameter. After `_find_related_test_files()` discovers test files, partition them:

- **In-scope tests:** Listed in `allowed_files` OR newly created by the agent (exist in worktree but not on base branch)
- **Out-of-scope tests:** Everything else

Only in-scope tests are run as blocking. Out-of-scope test names are logged at INFO level with a message like: `"Skipping out-of-scope test: tests/test_orchestrator.py (not in task files)"`.

To detect newly created files, use `git diff --name-only --diff-filter=A {base_ref}...HEAD` in the worktree.

### Files Changed

| File | Change |
|------|--------|
| `forge/core/daemon_review.py` | `_gate_test()` (line 359): add `allowed_files` param; filter discovered tests; add `pipeline_branch` param for new-file detection. Caller at line 608: pass `task.files` |
| `forge/core/daemon_helpers.py` | `_find_related_test_files()`: add `allowed_files` + `worktree_path` + `base_ref` params; return `(in_scope, out_of_scope)` tuple |

### Test Plan

- Unit test: `_find_related_test_files()` with `allowed_files` set correctly partitions tests
- Unit test: newly created test file (not on base) is treated as in-scope
- Integration: mock test gate with out-of-scope test → verify it passes (not blocked)

---

## Fix 2: Priority Bindings on Final Screen

### Problem

When `FollowUpTextArea` has focus, single-character keybindings (`r`, `d`, `s`, `n`) are consumed as text input instead of reaching screen-level handlers. User presses `r` to rerun but nothing happens.

### Design

Add `priority=True` to the critical single-char bindings in `FinalApprovalScreen.BINDINGS`. Priority bindings are handled at the screen level before being dispatched to focused widgets.

```python
Binding("d", "view_diff", "View Diff", show=True, priority=True),
Binding("r", "rerun", "Re-run Failed", show=True, priority=True),
Binding("s", "skip_failed", "Skip & Finish", show=True, priority=True),
Binding("n", "new_task", "New Task", show=True, priority=True),
```

The `f` binding intentionally stays non-priority (it focuses the textarea — if textarea already has focus, typing `f` as text is fine). `ctrl+s` for submit is already modifier-based and won't conflict.

### Files Changed

| File | Change |
|------|--------|
| `forge/tui/screens/final_approval.py` | Add `priority=True` to `d`, `r`, `s`, `n` bindings |

### Test Plan

- Manual: press `r` on partial-success final screen → rerun triggers
- Manual: press `d` → diff viewer opens
- Manual: press `f` → textarea gets focus; press `d` → diff viewer still opens (priority)

---

## Fix 3: Scrollable Diff Viewer

### Problem

`DiffViewer` extends `Widget` and uses `render()` to return a string. In Textual, a plain Widget with `overflow-y: auto` CSS clips content rather than providing a scrollbar. Large diffs are unreadable.

### Design

Refactor `DiffViewer` to use Textual's `ScrollableContainer`:

1. Change `DiffViewer` to extend `ScrollableContainer`
2. Move the diff rendering into a child `Static` widget that is yielded in `compose()`
3. On `update_diff()`, update the child Static's content
4. The ScrollableContainer provides native scrollbar + mouse wheel + page up/down support

Add `j`/`k` bindings for vim-style scrolling, plus `g`/`G` for jump-to-top/bottom:

```python
Binding("j", "scroll_down", "Scroll Down", show=False),
Binding("k", "scroll_up", "Scroll Up", show=False),
Binding("g", "scroll_home", "Top", show=False),
Binding("shift+g", "scroll_end", "Bottom", show=False),
```

### Files Changed

| File | Change |
|------|--------|
| `forge/tui/widgets/diff_viewer.py` | Extend `ScrollableContainer`; child `Static` for content; add j/k/g/G bindings |
| `forge/tui/screens/final_approval.py` | `DiffScreen`: remove any manual scroll logic if present; rely on DiffViewer's built-in scrolling |

### Test Plan

- Manual: open diff with >100 lines → scrollbar visible, j/k/mouse wheel all work
- Manual: `g` jumps to top, `G` jumps to bottom

---

## Fix 4: Home Screen Keybinding Label

### Problem

Home screen shortcut bar shows `[↑↓] History` but arrow keys don't navigate history — `j`/`k` do. Misleading.

### Design

Change the shortcut label from `[↑↓]` to `[j/k]` in the home screen's ShortcutBar definition.

### Files Changed

| File | Change |
|------|--------|
| `forge/tui/screens/home.py` | Update ShortcutBar shortcut tuple from `("↑↓", "History")` to `("j/k", "History")` |

### Test Plan

- Visual: home screen bottom bar shows `[j/k] History`

---

## Fix 5: Consistent Shortcut Bars Across All Screens

### Problem

Not all screens have the bottom shortcut bar. Some screens show shortcuts that don't work. Inconsistent experience.

### Design

Audit every screen in `forge/tui/screens/`. Ensure:

1. Every `compose()` yields a `ShortcutBar` as the last widget (or second-to-last before `Footer` if Footer is used)
2. Each bar shows ONLY the shortcuts that actually work on that screen
3. Remove `Footer()` from screens that also have `ShortcutBar` — having both creates a confusing double bar. ShortcutBar is our canonical shortcut display.
4. `check_action` gated bindings (like `rerun` in non-partial mode) should have `show=False` set dynamically when disabled

Specific screens to check:
- `home.py` — has ShortcutBar ✓
- `pipeline.py` — has ShortcutBar, no Footer ✓
- `plan_approval.py` — has ShortcutBar ✓
- `review.py` — has ShortcutBar, no Footer ✓
- `settings.py` — has ShortcutBar ✓
- `final_approval.py` — has ShortcutBar + Footer (remove Footer, line 204)

### Files Changed

| File | Change |
|------|--------|
| `forge/tui/screens/final_approval.py` | Remove `Footer()` from `compose()` (line 204); dynamically set `show=False` on gated bindings |

### Test Plan

- Visual: every screen has exactly one bottom bar showing working shortcuts
- Manual: no screen shows shortcuts that don't work

---

## Fix 6: Planning Stage Indicator

### Problem

During deep planning, user can't tell if Scout, Architect, Detailer, or Validator is running. All output goes to the same panel with no stage labels.

### Design

The current architecture maps all 4 planning events (`planning:scout`, `planning:architect`, `planning:detailer`, `planning:validator`) to the same handler `_on_planning_stage_output()` via `_EVENT_MAP` (lines 476-479 in state.py). The handler only receives the `data` dict — not the event type name.

**Architecture change required:** Modify the event dispatch in `_handle_event()` (line ~86) to pass the event type string as a second argument to handlers, OR use separate handler functions per stage.

Preferred approach — separate handler functions (simpler, no dispatch change):

1. Add `planning_stage: str = ""` reactive property to `ForgeState`
2. Replace the single `_on_planning_stage_output()` with 4 thin wrappers that set `planning_stage` before calling the shared logic:
   ```python
   def _on_planning_scout(self, data: dict) -> None:
       self._handle_planning_output("Scout", data)

   def _on_planning_architect(self, data: dict) -> None:
       self._handle_planning_output("Architect", data)
   # etc.

   def _handle_planning_output(self, stage: str, data: dict) -> None:
       if self.planning_stage != stage:
           self.planning_stage = stage
           self.planner_output.append(f"─── {stage} ───")
       self.planner_output.append(data.get("line", ""))
   ```
3. Update `_EVENT_MAP` to point each event to its own handler
4. In `pipeline.py`, the `PhaseBanner` widget (line 57) reads `planning_stage` and appends it: `"◌ Planning (Scout)"`

### Files Changed

| File | Change |
|------|--------|
| `forge/tui/state.py` | Add `planning_stage` property; replace single handler with 4 stage-specific handlers + shared `_handle_planning_output()`; update `_EVENT_MAP` |
| `forge/tui/screens/pipeline.py` | `PhaseBanner.render()` (line 85): read `planning_stage` from state, append to `"◌ Planning"` label |

### Test Plan

- Manual: run deep planning → phase banner shows `Planning (Scout)`, then `Planning (Architect)`, etc.
- Manual: output panel shows `─── Scout ───` separator between stages

---

## Fix 7: Scout Session Reuse on Retry

### Problem

Each Scout retry creates a fresh SDK session that re-reads all files from disk. With `max_turns=30` and 3 retries, this means up to 90 tool-call turns of redundant file reading. Looks like the planner is stuck.

### Design

Two changes needed:

1. **Add `session_id` field to `ScoutResult`** — currently missing (only has `codebase_map`, `cost_usd`, `input_tokens`, `output_tokens`)
2. **Track and reuse `session_id` in `Scout.run()`** — capture from first SDK result, use `options.resume` on retries

```python
# In ScoutResult dataclass (line 21):
@dataclass
class ScoutResult:
    codebase_map: CodebaseMap | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    session_id: str | None = None  # ADD THIS

# In Scout.run():
session_id: str | None = None

for attempt in range(self._max_retries):
    if session_id:
        prompt = f"Previous attempt feedback: {feedback}\n\nProduce ONLY the CodebaseMap JSON."
        options.resume = session_id
    else:
        prompt = self._build_prompt(...)

    result = await sdk_query(...)
    if result:
        session_id = result.session_id  # Cache for retry
```

Same pattern the Architect already uses for question follow-ups (lines 97-100 in architect.py).

### Files Changed

| File | Change |
|------|--------|
| `forge/core/planning/scout.py` | Add `session_id` to `ScoutResult`; track `session_id` across attempts; use `options.resume` on retries |

### Test Plan

- Unit test: mock `sdk_query` to return session_id; verify second call uses `resume`
- Manual: run deep planning → Scout retries don't re-read files

---

## Fix 8: Behind-Main Warning on Final Screen

### Problem

PR is created against main but the pipeline branch may be behind. User gets merge conflicts on GitHub with no prior warning.

### Design

Before showing the final approval screen, check how many commits the pipeline branch is behind `origin/main`:

```bash
git fetch origin main --quiet
git rev-list --count HEAD..origin/main
```

If count > 0, display a warning banner at the top of the final screen:

```
⚠ Branch is 3 commits behind main. PR may have merge conflicts.
```

This is informational only — PR creation is not blocked. The user is aware and can rebase manually if desired.

### Files Changed

| File | Change |
|------|--------|
| `forge/tui/screens/final_approval.py` | Add behind-main check in `on_mount()` using existing `_get_project_dir()` (line 256); display warning Static if behind |

Note: `FinalApprovalScreen` already has `_get_project_dir()` which returns the project directory via `self.app._project_dir` or `os.getcwd()`. No changes needed in `app.py`.

### Test Plan

- Manual: create a pipeline while main has advanced → warning shows commit count
- Manual: pipeline on latest main → no warning shown

---

## Future Work (Not Implemented Now)

### PR Auto-Rebase with Conflict Resolution

Before creating the PR, automatically rebase the pipeline branch onto the latest `origin/main`. If conflicts arise:
1. Attempt agent-assisted resolution (Claude agent with task context)
2. Show resolved diff to user for confirmation
3. If agent can't resolve, block PR creation and list conflicting files

This requires careful design to avoid silent bad merges and is deferred to a future iteration.

---

## Summary

| # | Fix | Severity | Files |
|---|-----|----------|-------|
| 1 | Test gate scoping | Critical | daemon_review.py, daemon_helpers.py |
| 2 | Priority bindings | High | final_approval.py |
| 3 | Scrollable diff viewer | High | diff_viewer.py, final_approval.py |
| 4 | Home keybinding label | Low | home.py |
| 5 | Consistent shortcut bars | Medium | final_approval.py |
| 6 | Planning stage indicator | Medium | state.py, pipeline.py |
| 7 | Scout session reuse | Medium | scout.py |
| 8 | Behind-main warning | Low | final_approval.py |
