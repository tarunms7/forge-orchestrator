# TUI Layout Redesign — Design Spec

**Date:** 2026-03-12
**Status:** Draft
**Goal:** Bigger stage labels, remove sidebar except during execution, unified agent+review log stream.

## Problem

1. Stage labels like "PLANNING" are small (single line, normal text) — not visually prominent enough.
2. The TaskList sidebar appears on all pipeline phases including planning, where there are no tasks to switch between. Wastes ~25% of screen width.
3. Agent output and review output live in separate view panels (toggle with `o`/`v`) — the user must manually switch. Review results are invisible unless you press `v`.

## Solution

Three targeted changes to the PipelineScreen and ReviewScreen:

### 1. Bigger Phase Banner

**Current:** PhaseBanner is 3 lines tall, renders `[bold color]◌ Planning[/]` in normal text.

**New:** PhaseBanner is 5 lines tall, renders with wide character spacing for visual weight.

```

               ◌  P L A N N I N G

```

**Implementation in `PhaseBanner` widget:**

```python
# In PhaseBanner.render():
label, colour = _PHASE_BANNER.get(self._phase, ("Unknown", "#8b949e"))
# Extract the text portion after any icon prefix
icon, _, text = label.partition(" ")
if not text:
    text, icon = icon, ""
# Wide-space the text: preserve word boundaries with triple-space
words = text.upper().split()
spaced_words = ["  ".join(w) for w in words]
spaced = "   ".join(spaced_words)  # triple-space between words
icon_prefix = f"{icon}  " if icon else ""
banner = f"[bold {colour}]{icon_prefix}{spaced}[/]"
```

**CSS changes — both `PhaseBanner.DEFAULT_CSS` AND `PipelineScreen.DEFAULT_CSS` override:**

The PipelineScreen has a CSS specificity override at `PipelineScreen > PhaseBanner { height: 3; }`. Both must be updated:

```css
/* PhaseBanner.DEFAULT_CSS */
PhaseBanner {
    width: 1fr;
    height: 5;
    content-align: center middle;
    text-align: center;
    background: #0d1117;
    border-bottom: tall #30363d;
}

/* PipelineScreen.DEFAULT_CSS — update the PhaseBanner override */
PipelineScreen > PhaseBanner {
    width: 100%;
    height: 5;
    content-align: center middle;
    text-align: center;
    background: #0d1117;
    border-bottom: tall #30363d;
}
```

The height goes from 3 → 5. The `content-align: center middle` vertically centers within the 5-line box, giving 1 line padding above and below.

**Phase labels — all 14 phases (wide-spaced render):**

Multi-word labels use triple-space between words to preserve word boundaries:

| Phase | Icon | Label | Wide-Spaced Render |
|-------|------|-------|--------------------|
| idle | | Idle | `I D L E` |
| planning | ◌ | Planning | `◌  P L A N N I N G` |
| planned | ◉ | Plan Approval | `◉  P L A N   A P P R O V A L` |
| contracts | ⚙ | Contracts | `⚙  C O N T R A C T S` |
| executing | ⚡ | Execution | `⚡  E X E C U T I O N` |
| in_progress | ⚡ | Execution | `⚡  E X E C U T I O N` |
| review | 🔍 | Review | `🔍  R E V I E W` |
| in_review | 🔍 | Review | `🔍  R E V I E W` |
| final_approval | ◎ | Final Approval | `◎  F I N A L   A P P R O V A L` |
| pr_creating | ⚙ | Creating PR | `⚙  C R E A T I N G   P R` |
| pr_created | ✔ | PR Created | `✔  P R   C R E A T E D` |
| complete | ✔ | Complete | `✔  C O M P L E T E` |
| error | ✖ | Error | `✖  E R R O R` |
| cancelled | ✘ | Cancelled | `✘  C A N C E L L E D` |
| paused | ⏸ | Paused | `⏸  P A U S E D` |

The read-only banner (pipeline replay) continues to render below in `[dim]` — the 5-line height accommodates it.

### 2. Dynamic Sidebar — Phase-Aware Layout

**Principle:** The `#left-panel` (TaskList + DecisionBadge) is only visible during execution-like phases. During planning, contracts, and terminal phases, the content area uses the full terminal width.

**Implementation — CSS class toggling on `#split-pane`:**

Add a new CSS rule:

```css
#split-pane.full-width #left-panel {
    display: none;
}
#split-pane.full-width #right-panel {
    width: 100%;
}
```

In `_refresh_all()`, after updating `phase_banner`:

```python
_SIDEBAR_HIDDEN_PHASES = frozenset({
    "idle", "planning", "planned", "contracts",
    "final_approval", "complete", "pr_creating", "pr_created", "cancelled",
})

split_pane = self.query_one("#split-pane")
if state.phase in _SIDEBAR_HIDDEN_PHASES:
    split_pane.add_class("full-width")
else:
    split_pane.remove_class("full-width")
```

**Phases and sidebar visibility:**

| Phase | Sidebar | Rationale |
|-------|---------|-----------|
| idle | hidden | No tasks exist |
| planning | hidden | Planner output fills full width |
| planned | hidden | Plan approval screen handles this |
| contracts | hidden | Contracts output fills full width |
| executing / in_progress | **shown** | Task switching is needed |
| review / in_review | **shown** | Multiple tasks may be in review |
| final_approval | hidden | Final screen handles this |
| pr_creating | hidden | Background PR creation |
| pr_created | hidden | Done — PR URL shown |
| complete | hidden | Done |
| error | **shown** | User may need to select errored task |
| paused | **shown** | User may need to inspect tasks |
| cancelled | hidden | Terminal state, no interaction |

Note: `review` and `in_review` keep the sidebar because the user needs to switch between tasks being reviewed.

**ReviewScreen changes:**

ReviewScreen currently has `TaskList + DiffViewer` in a horizontal split. Per the user's request, remove the sidebar.

**Compose — before and after:**

```python
# ReviewScreen.compose() — BEFORE:
yield Static("[bold #a371f7]REVIEW[/]", id="review-header")
with Horizontal(id="review-pane"):
    yield TaskList()
    yield DiffViewer()
yield Static("[a] approve  [x] reject  [e] editor  [j/k] navigate", id="review-status")

# ReviewScreen.compose() — AFTER:
yield Static("[bold #a371f7]REVIEW[/]", id="review-header")
yield DiffViewer()
yield Static("[a] approve  [x] reject  [e] editor  [j/k] scroll  [1-9] jump task", id="review-status")
```

**Remove TaskList import** and `Horizontal` import (no longer needed).

**Update 4 methods that reference TaskList:**

```python
# _refresh() — BEFORE calls query_one(TaskList).update_tasks()
# AFTER: remove TaskList update entirely, only update DiffViewer
def _refresh(self) -> None:
    state = self._state
    tid = state.selected_task_id
    if tid and tid in state.tasks:
        task = state.tasks[tid]
        diff = self._diff_cache.get(tid, "")
        if not diff and tid not in self._diff_loading:
            self._diff_loading.add(tid)
            asyncio.create_task(self._load_diff(tid))
            diff = "Loading diff..."
        self.query_one(DiffViewer).update_diff(tid, task.get("title", ""), diff)

# action_cursor_down/up — BEFORE calls query_one(TaskList)
# AFTER: scroll DiffViewer
def action_cursor_down(self) -> None:
    self.query_one(DiffViewer).scroll_down()

def action_cursor_up(self) -> None:
    self.query_one(DiffViewer).scroll_up()

# on_task_list_selected — REMOVE entirely (TaskList no longer composed)
```

Note: DiffViewer inherits from Widget, not VerticalScroll. If `scroll_down()`/`scroll_up()` are not available, wrap the DiffViewer content in a VerticalScroll in ReviewScreen's compose, or use `self.query_one(DiffViewer).scroll_relative(y=3)` / `scroll_relative(y=-3)`. The DiffViewer widget currently uses `overflow-y: auto` CSS which makes it scrollable.

**Update ReviewScreen CSS (remove `#review-pane` rule):**

```css
ReviewScreen {
    layout: vertical;
}
#review-header {
    height: 1;
    padding: 0 1;
    background: #161b22;
    color: #a371f7;
}
DiffViewer {
    height: 1fr;
}
#review-status {
    dock: bottom;
    height: 1;
    padding: 0 1;
    background: #161b22;
    color: #8b949e;
}
```

### 3. Unified Log Stream

**Goal:** Agent output and review output flow together in one chronological stream, separated by colored section headers.

**Visual:**

```
───── AGENT ──────────────────────────────────────────────
  Setting up project structure...
  Creating authentication module...
  Writing user model...
───── REVIEW 1 ───────────────────────────────────────────
  🔨 Build: ✓ passed
  📏 Lint: ✓ passed (auto-fixed 2 issues)
  🧪 Tests: ✓ 12/12 passed
  🤖 LLM Review: analyzing code quality...
  PASS: Code follows existing patterns...
───── AGENT ──────────────────────────────────────────────
  Applying review feedback...
  Updating error handling...
```

Section header colors:
- `AGENT`: `#f0883e` (orange) — matches execution accent
- `REVIEW N`: `#a371f7` (purple) — matches review accent
- Gate results: `#79c0ff` (blue) — rendered inline under review section

#### 3a. State changes (`forge/tui/state.py`)

Add a new field to `TuiState.__init__`:

```python
# Unified chronological log per task: list of (source_type, line)
# source_type: "agent" | "review" | "gate" | "system"
self.unified_log: dict[str, list[tuple[str, str]]] = defaultdict(list)
```

Modify existing event handlers to also append to `unified_log`:

```python
def _on_agent_output(self, data: dict) -> None:
    tid = data.get("task_id", "")
    line = data.get("line", "")
    # Existing agent_output append (keep for backwards compat)
    lines = self.agent_output[tid]
    lines.append(line)
    if len(lines) > self._max_output_lines:
        del lines[: len(lines) - self._max_output_lines]
    # NEW: append to unified log
    ulog = self.unified_log[tid]
    ulog.append(("agent", line))
    if len(ulog) > self._max_output_lines:
        del ulog[: len(ulog) - self._max_output_lines]
    if tid:
        self.streaming_task_ids.add(tid)
    self._notify("agent_output")

def _on_review_llm_output(self, data: dict) -> None:
    task_id = data.get("task_id")
    line = data.get("line", "")
    if task_id:
        # Existing review_output append (keep for backwards compat)
        lines = self.review_output[task_id]
        lines.append(line)
        if len(lines) > self._max_output_lines:
            del lines[: len(lines) - self._max_output_lines]
        # NEW: append to unified log
        ulog = self.unified_log[task_id]
        ulog.append(("review", line))
        if len(ulog) > self._max_output_lines:
            del ulog[: len(ulog) - self._max_output_lines]
        self.streaming_task_ids.add(task_id)
        self._notify("review_output")
```

Add gate results to unified log:

```python
def _on_review_gate_passed(self, data: dict) -> None:
    task_id = data.get("task_id")
    gate = data.get("gate")
    if task_id and gate:
        self.review_gates.setdefault(task_id, {})[gate] = {
            "status": "passed", "details": data.get("details"),
        }
        # NEW: add to unified log
        gate_label = {"gate0_build": "🔨 Build", "gate1_lint": "📏 Lint",
                      "gate1_5_test": "🧪 Tests", "gate2_llm_review": "🤖 LLM Review"}.get(gate, gate)
        self.unified_log[task_id].append(("gate", f"{gate_label}: ✓ {data.get('details', 'passed')}"))
        self._notify("tasks")

def _on_review_gate_failed(self, data: dict) -> None:
    task_id = data.get("task_id")
    gate = data.get("gate")
    if task_id and gate:
        self.review_gates.setdefault(task_id, {})[gate] = {
            "status": "failed", "details": data.get("details"),
        }
        # NEW: add to unified log
        gate_label = {"gate0_build": "🔨 Build", "gate1_lint": "📏 Lint",
                      "gate1_5_test": "🧪 Tests", "gate2_llm_review": "🤖 LLM Review"}.get(gate, gate)
        self.unified_log[task_id].append(("gate", f"{gate_label}: ✗ {data.get('details', 'failed')}"))
        self._notify("tasks")
```

Add to `reset()` AND `_on_restarted()`:

```python
# In reset():
self.unified_log.clear()

# In _on_restarted():
self.unified_log.clear()  # alongside existing .clear() calls
```

#### 3b. Rendering changes (`forge/tui/widgets/agent_output.py`)

Add a new rendering function:

```python
def format_unified_output(
    entries: list[tuple[str, str]],
    spinner_frame: int = 0,
    streaming: bool = False,
    typing_frame: int = 0,
) -> str:
    """Render unified log with section headers when source type changes."""
    if not entries:
        frame = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        return f"[#58a6ff]{frame}[/] [#8b949e]Waiting for output...[/]"

    _SECTION_COLORS = {
        "agent": "#f0883e",
        "review": "#a371f7",
        "gate": "#79c0ff",
        "system": "#8b949e",
    }

    parts: list[str] = []
    current_section: str | None = None
    review_count = 0

    for source_type, line in entries:
        # Determine effective section (gate lines merge into review section)
        effective = "review" if source_type == "gate" else source_type

        if effective != current_section:
            current_section = effective
            color = _SECTION_COLORS.get(effective, "#8b949e")
            if effective == "review":
                review_count += 1
                label = f"REVIEW {review_count}"
            else:
                label = "AGENT"
            header = f"[{color}]───── {label} " + "─" * max(1, 50 - len(label)) + "[/]"
            if parts:
                parts.append("")  # blank line before new section
            parts.append(header)

        # Render gate lines with their own formatting
        if source_type == "gate":
            parts.append(f"  [#79c0ff]{line}[/]")
        else:
            parts.append(line)

    if streaming:
        cursor = _TYPING_FRAMES[typing_frame % len(_TYPING_FRAMES)]
        parts.append(f"[#58a6ff]● Typing{cursor}[/]")

    return "\n".join(parts)
```

**AgentOutput widget changes:**

- Add `_unified_entries: list[tuple[str, str]] = []` to `__init__`
- Add `update_unified(entries)` method for full refresh
- Add `append_unified(source_type, line)` method for streaming
- The `format_output` function stays for backwards compat (planner output during planning phase uses it)
- During execution phase, rendering uses `format_unified_output`

```python
def append_unified(self, source_type: str, line: str) -> None:
    """Append a single unified log entry during streaming."""
    self._unified_entries.append((source_type, line))
    try:
        content = self.query_one("#agent-content", Static)
        content.update(
            format_unified_output(
                self._unified_entries,
                self._spinner_frame,
                streaming=self._streaming,
                typing_frame=self._typing_frame,
            )
        )
        if self._is_near_bottom():
            self.call_after_refresh(self._scroll_to_end)
    except Exception:
        pass

def update_unified(
    self,
    task_id: str | None,
    title: str | None,
    state: str | None,
    entries: list[tuple[str, str]],
) -> None:
    """Full refresh with unified log entries.

    Replaces _unified_entries with the authoritative state from TuiState.
    This is the reconciliation point: during streaming, the widget
    accumulates entries via append_unified(); on task switch or
    streaming-end, this method resets to the canonical state.unified_log.
    """
    self._task_id = task_id
    self._title = title
    self._state = state
    self._unified_entries = list(entries)  # Reset to authoritative state
    self.set_streaming(False)
    try:
        self.query_one("#agent-header", Static).update(
            format_header(task_id, title, state)
        )
        self.query_one("#agent-content", Static).update(
            format_unified_output(entries, self._spinner_frame)
        )
        if entries and self._is_near_bottom():
            self.call_after_refresh(self._scroll_to_end)
    except Exception:
        pass
```

**Streaming reconciliation pattern:**

The widget has a dual-write pattern for streaming performance:

1. **During streaming:** `append_unified()` adds entries to `_unified_entries` (widget-local). This is the fast path — no full re-render from state.
2. **On task switch or streaming end:** `update_unified()` replaces `_unified_entries` with `state.unified_log[tid]` — the authoritative source. This reconciles any drift.
3. **Guard:** `_refresh_all()` does NOT call `update_unified()` when streaming is active (guarded by `if tid in self._agent_streaming_tasks`). This prevents the authoritative-reset from fighting the fast-path appends.

This matches the existing pattern for `append_line()` / `update_output()` which has the same dual-write design.

#### 3c. PipelineScreen changes

**Remove ViewLabel and ReviewGates from compose:**

The right panel simplifies from 5 widgets (ViewLabel + AgentOutput + ChatThread + DiffViewer + ReviewGates) to 3 (AgentOutput + ChatThread + DiffViewer):

```python
# PipelineScreen.compose() — AFTER:
yield DagOverlay()
yield PhaseBanner()
with Horizontal(id="split-pane"):
    with Vertical(id="left-panel"):
        yield TaskList()
        yield DecisionBadge()
    with Vertical(id="right-panel"):
        yield AgentOutput()
        yield ChatThread()
        yield DiffViewer()
yield PipelineProgress()
```

- `ReviewGates` widget removed from compose — its data now flows through unified log
- `ViewLabel` widget removed — no more visible tab bar
- View switching reduced to: output (default), chat, diff
- Keep key bindings: `o` (output), `t` (chat), `d` (diff), `c`/`C` (copy)
- Remove `v` (review) binding — review data is in the unified output stream

**Update `_VIEW_NAMES` and `_set_view()`:**

```python
# BEFORE:
_VIEW_NAMES = ("output", "chat", "diff", "review")

# AFTER:
_VIEW_NAMES = ("output", "chat", "diff")
```

```python
def _set_view(self, view: str) -> None:
    """Show one right-panel view widget and hide the others."""
    assert view in _VIEW_NAMES, f"Unknown view: {view!r}"
    self._active_view = view

    widget_map: dict[str, type[Widget]] = {
        "output": AgentOutput,
        "chat": ChatThread,
        "diff": DiffViewer,
        # ReviewGates REMOVED — no longer composed
    }

    for name, cls in widget_map.items():
        w = self.query_one(cls)
        if name == view:
            w.display = True
        else:
            w.display = False

    # ViewLabel REMOVED — no longer composed
```

**Remove `action_view_review()` method and `v` binding entirely.**

**Update `_update_streaming_lifecycle()`:**

Remove all ReviewGates references:

```python
def _update_streaming_lifecycle(self) -> None:
    """Stop streaming indicators for tasks that are done/error."""
    state = self._state
    tid = state.selected_task_id
    if not tid:
        return
    if tid not in state.streaming_task_ids:
        if tid in self._agent_streaming_tasks:
            self._agent_streaming_tasks.discard(tid)
            try:
                ao = self.query_one(AgentOutput)
                ao.set_streaming(False)
                # Final sync: full refresh from authoritative unified_log
                unified = state.unified_log.get(tid, [])
                task = state.tasks.get(tid, {})
                ao.update_unified(tid, task.get("title"), task.get("state"), unified)
            except Exception:
                pass
        if tid in self._review_streaming_tasks:
            self._review_streaming_tasks.discard(tid)
            try:
                ao = self.query_one(AgentOutput)
                ao.set_streaming(False)
                # Review streaming ended — reconcile from unified_log
                unified = state.unified_log.get(tid, [])
                task = state.tasks.get(tid, {})
                ao.update_unified(tid, task.get("title"), task.get("state"), unified)
            except Exception:
                pass
```

**Update `_refresh_all()` — remove all ReviewGates references:**

Remove these lines from `_refresh_all()`:
- `review_gates = self.query_one(ReviewGates)`
- `review_gates.update_gates(gates)`
- `review_gates.update_streaming_output(review_lines)`
- `review_gates.update_gates({})`
- `review_gates.update_streaming_output([])`

Replace the task output section with unified log rendering:

```python
if tid and tid in state.tasks:
    task = state.tasks[tid]
    unified = state.unified_log.get(tid, [])
    if task.get("state") == "error":
        agent_output.render_error_detail(tid, task, state.agent_output.get(tid, []))
    elif tid in self._agent_streaming_tasks or tid in self._review_streaming_tasks:
        # Streaming active — only update header, not content
        agent_output.update_header(tid, task.get("title"), task.get("state"))
    else:
        agent_output.clear_error_detail()
        agent_output.update_unified(tid, task.get("title"), task.get("state"), unified)

        # Auto-switch to chat view when the selected task is awaiting input
        if task.get("state") == "awaiting_input":
            self._auto_switch_chat(tid, task)
elif state.phase == "planning" and state.planner_output:
    agent_output.clear_error_detail()
    agent_output.update_output("planner", "Planning", "planning", state.planner_output)
elif state.phase == "contracts":
    agent_output.clear_error_detail()
    if state.contracts_output:
        agent_output.update_output(
            "contracts", "⚙ Contracts", "contracts", state.contracts_output,
        )
    else:
        agent_output.update_output(
            "contracts", "Generating Contracts", "contracts",
            ["⚙ Building API contracts...",
             "  This enables tasks to run in parallel instead of sequentially."],
        )
else:
    agent_output.clear_error_detail()
    agent_output.update_output(None, None, None, [])
```

Note: `update_output()` is still used for planner/contracts phases (not unified log). Only task execution uses `update_unified()`.

Diff and review sections also simplified — remove ReviewGates calls:

```python
# Update diff for selected task (keep existing logic)
diff_viewer = self.query_one(DiffViewer)
if tid and tid in state.tasks:
    task = state.tasks[tid]
    if self._active_view == "diff":
        if tid in self._diff_cache:
            diff_viewer.update_diff(tid, task.get("title", ""), self._diff_cache[tid])
        else:
            diff_viewer.update_diff(tid, task.get("title", ""), "Loading diff...")
            asyncio.create_task(self._refresh_diff_async(tid))
    else:
        diff_text = self._diff_cache.get(tid, "")
        diff_viewer.update_diff(tid, task.get("title", ""), diff_text)
# No more review_gates.update_gates() or review_gates.update_streaming_output()
```

**Fast path changes for streaming:**

```python
def _handle_agent_output_fast(self) -> None:
    """Fast path for agent_output: append to unified log in AgentOutput widget."""
    state = self._state
    tid = state.selected_task_id
    if not tid:
        return
    lines = state.agent_output.get(tid, [])
    if not lines:
        return
    agent_output = self.query_one(AgentOutput)
    if tid not in self._agent_streaming_tasks:
        self._agent_streaming_tasks.add(tid)
        agent_output.set_streaming(True)
    # Use unified append instead of plain append
    agent_output.append_unified("agent", lines[-1])

def _handle_review_output_fast(self) -> None:
    """Fast path for review_output: append to unified log in AgentOutput widget."""
    state = self._state
    tid = state.selected_task_id
    if not tid:
        return
    lines = state.review_output.get(tid, [])
    if not lines:
        return
    agent_output = self.query_one(AgentOutput)
    if tid not in self._review_streaming_tasks:
        self._review_streaming_tasks.add(tid)
        agent_output.set_streaming(True)
    agent_output.append_unified("review", lines[-1])
```

**Update PipelineScreen docstring (lines 149-164):**

```python
class PipelineScreen(Screen):
    """Main pipeline execution screen with full-width phase banner + dynamic layout.

    Phase banner (full width, centered):
      - PhaseBanner — 5-line wide-spaced label

    Left panel (hidden during planning, shown during execution):
      - TaskList
      - DecisionBadge

    Right panel (fills remaining or full width):
      - AgentOutput   (unified log stream — agent + review + gates)
      - ChatThread    (view=chat, auto-shown for questions)
      - DiffViewer    (view=diff, toggled with 'd')
    """
```

**Remove CSS for ReviewGates and ViewLabel from PipelineScreen.DEFAULT_CSS:**

```css
/* REMOVE these rules: */
#right-panel ReviewGates { ... }
#right-panel ViewLabel { ... }  /* (actually: ViewLabel { ... }) */
```

**Remove bindings:**

```python
# REMOVE:
Binding("v", "view_review", "Review", show=True),

# REMOVE method:
def action_view_review(self) -> None:
    self._set_view("review")
```

## What Changes Per File

| File | Change |
|------|--------|
| `forge/tui/screens/pipeline.py` | PhaseBanner render (wide spacing + height 5), CSS specificity fix, remove ViewLabel/ReviewGates from compose, remove `v` binding and `action_view_review`, update `_VIEW_NAMES`, `_set_view()`, `_update_streaming_lifecycle()`, `_refresh_all()`, `_handle_*_fast()` to remove all ReviewGates/ViewLabel references and use unified log, dynamic sidebar CSS class, update docstring |
| `forge/tui/widgets/agent_output.py` | Add `format_unified_output()`, `_unified_entries` field, `append_unified()`, `update_unified()` |
| `forge/tui/state.py` | Add `unified_log` field, append to it in `_on_agent_output`, `_on_review_llm_output`, `_on_review_gate_passed/failed`, clear in `reset()` AND `_on_restarted()` |
| `forge/tui/screens/review.py` | Remove TaskList from compose, remove `Horizontal` container, DiffViewer full-width, update `_refresh()` (remove TaskList update), `action_cursor_down/up` scroll diff instead of navigate tasks, remove `on_task_list_selected`, update status bar text |
| `forge/tui/screens/pipeline_test.py` | Remove `ViewLabel` import and tests, remove `test_v_key_opens_review_view`, update tests that reference `append_line` to use `append_unified`, update widget existence assertions |

## Files NOT Changed

| File | Reason |
|------|--------|
| `forge/tui/widgets/task_list.py` | No changes — still used in PipelineScreen during execution |
| `forge/tui/widgets/review_gates.py` | Class kept (not deleted) — just no longer composed in PipelineScreen. Could be used elsewhere or in future. |
| `forge/tui/widgets/chat_thread.py` | No changes — still composed in right panel |
| `forge/tui/widgets/diff_viewer.py` | No changes — still composed in right panel and ReviewScreen |
| `forge/tui/widgets/progress_bar.py` | No changes |
| `forge/tui/screens/home.py` | No changes — already has no sidebar |
| `forge/tui/screens/final_approval.py` | No changes — already centered single column |
| `forge/tui/screens/plan_approval.py` | No changes — already full width |
| `forge/tui/screens/settings.py` | No changes — already full width |

## Edge Cases

1. **Phase transition animation:** When transitioning from planning→execution, the sidebar appears. This happens in `_refresh_all()` via `add_class`/`remove_class` which Textual handles as an instant CSS reflow.

2. **Empty unified log on task switch:** When user switches to a task that hasn't started yet, `unified_log[tid]` is empty → spinner is shown (same as current behavior).

3. **Review auto-transition:** Currently, PipelineScreen auto-pushes ReviewScreen when a task enters `in_review`. This behavior stays — user sees the full-width diff view for approval. But now the unified log also shows review progress inline, so the user can see it without switching.

4. **Chat view during execution:** When a question arrives (`awaiting_input`), the chat view auto-activates replacing the unified log. When answered, it switches back. No change to this behavior.

5. **Read-only mode (pipeline replay):** Uses the same layout. Sidebar hidden if replaying a planning phase, shown if replaying execution.

6. **ReviewScreen j/k keys:** Without TaskList, `j`/`k` scroll the DiffViewer content. Use `scroll_down()`/`scroll_up()` if available, or `scroll_relative(y=3)`.

7. **Streaming reconciliation:** During streaming, `append_unified()` writes to widget-local `_unified_entries`. On streaming end or task switch, `update_unified()` resets from `state.unified_log[tid]` (authoritative). The guard in `_refresh_all()` prevents calling `update_unified()` during active streaming, avoiding conflicts.

8. **Pipeline restart:** `_on_restarted()` clears `unified_log` alongside all other state, preventing stale entries from the previous run.

## Testing Plan

1. **Visual verification:** Run `forge tui`, start a pipeline, verify:
   - PhaseBanner shows wide-spaced text, 5 lines tall
   - No sidebar during planning phase
   - Sidebar appears when execution starts
   - Unified log shows both agent and review sections with colored headers
2. **ReviewScreen:** Verify DiffViewer is full-width, no TaskList visible
3. **Key bindings:** Verify `j`/`k` scroll diff in ReviewScreen, `1-9` jump tasks, `d`/`t`/`c` switch views in PipelineScreen, `v` key does nothing
4. **Streaming:** Verify agent lines appear under `AGENT` header, review lines under `REVIEW N` header, no flickering
5. **Error mode:** Verify error detail view still works (takes over unified log display)
6. **Pipeline replay:** Verify read-only mode respects dynamic sidebar (hidden during planning, shown during execution)
7. **Unit tests:** Update `pipeline_test.py` — all tests pass after removing ViewLabel/ReviewGates references

## Out of Scope

- Color theme customization (future enhancement)
- Configurable sidebar width
- Resizable split pane (Textual doesn't support mouse drag resize)
- Touch/mouse support for task switching (terminal only)
