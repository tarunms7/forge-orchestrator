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
# Wide-space the text
spaced = "  ".join(text.upper())
icon_prefix = f"{icon}  " if icon else ""
banner = f"[bold {colour}]{icon_prefix}{spaced}[/]"
```

**CSS change:**

```css
PhaseBanner {
    height: 5;
    content-align: center middle;
    text-align: center;
    background: #0d1117;
    border-bottom: tall #30363d;
}
```

The height goes from 3 → 5. The `content-align: center middle` vertically centers within the 5-line box, giving 1 line padding above and below. The wide spacing (`P L A N N I N G`) makes it roughly 2x wider visually.

**Phase labels (updated for wide spacing):**

| Phase | Icon | Label | Wide-Spaced Render |
|-------|------|-------|--------------------|
| planning | ◌ | Planning | `◌  P L A N N I N G` |
| planned | ◉ | Plan Approval | `◉  P L A N  A P P R O V A L` |
| contracts | ⚙ | Contracts | `⚙  C O N T R A C T S` |
| executing | ⚡ | Execution | `⚡  E X E C U T I O N` |
| review | 🔍 | Review | `🔍  R E V I E W` |
| complete | ✔ | Complete | `✔  C O M P L E T E` |
| error | ✖ | Error | `✖  E R R O R` |

The read-only banner (pipeline replay) continues to render below in `[dim]` — the 5-line height accommodates it.

### 2. Dynamic Sidebar — Phase-Aware Layout

**Principle:** The `#left-panel` (TaskList + DecisionBadge) is only visible during execution. During planning, contracts, and other non-execution phases, the content area uses the full terminal width.

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
split_pane = self.query_one("#split-pane")
if state.phase in ("planning", "planned", "contracts", "idle"):
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
| complete | hidden | Done |
| error | **shown** | User may need to select errored task |
| paused | **shown** | User may need to inspect tasks |

Note: `review` and `in_review` keep the sidebar because the user needs to switch between tasks being reviewed. The wireframe showed execution with sidebar, and review tasks need the same navigation.

**ReviewScreen changes:**

ReviewScreen currently has `TaskList + DiffViewer` in a horizontal split. Per the user's request, remove the sidebar:

```python
# ReviewScreen.compose() — BEFORE:
with Horizontal(id="review-pane"):
    yield TaskList()
    yield DiffViewer()

# ReviewScreen.compose() — AFTER:
yield DiffViewer()
```

Remove `TaskList` from ReviewScreen entirely. The DiffViewer takes full width. Task navigation in review uses the 1-9 number key bindings (already implemented with `action_jump_task`). The `j`/`k` bindings change to scroll the diff instead of navigating tasks.

Update ReviewScreen CSS:

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
- `GATE`: `#79c0ff` (blue) — for individual gate results if shown separately

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

Add to `reset()`:

```python
self.unified_log.clear()
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
    _SECTION_LABELS = {
        "agent": "AGENT",
        "review": "REVIEW",
        "gate": "REVIEW",   # gates render inline with review sections
        "system": "SYSTEM",
    }

    parts: list[str] = []
    current_section: str | None = None
    review_count = 0

    for source_type, line in entries:
        # Determine effective section (gate lines merge into review section)
        effective = "review" if source_type == "gate" else source_type

        if effective != current_section:
            current_section = effective
            color = _SECTION_COLORS.get(source_type, "#8b949e")
            if effective == "review":
                review_count += 1
                label = f"REVIEW {review_count}"
            else:
                label = _SECTION_LABELS.get(effective, effective.upper())
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

- Add `_unified_entries: list[tuple[str, str]]` field
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
    """Full refresh with unified log entries."""
    self._task_id = task_id
    self._title = title
    self._state = state
    self._unified_entries = list(entries)
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

#### 3c. PipelineScreen changes

**Remove ViewLabel and separate view panels:**

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

**Full refresh changes in `_refresh_all()`:**

When showing a task during execution, use `update_unified()` instead of `update_output()`:

```python
if tid and tid in state.tasks:
    task = state.tasks[tid]
    unified = state.unified_log.get(tid, [])
    if task.get("state") == "error":
        agent_output.render_error_detail(tid, task, state.agent_output.get(tid, []))
    elif tid in self._agent_streaming_tasks or tid in self._review_streaming_tasks:
        agent_output.update_header(tid, task.get("title"), task.get("state"))
    else:
        agent_output.clear_error_detail()
        agent_output.update_unified(tid, task.get("title"), task.get("state"), unified)
```

During planning phase (no unified log), keep using `update_output()` with `planner_output`.

## What Changes Per File

| File | Change |
|------|--------|
| `forge/tui/screens/pipeline.py` | PhaseBanner render + height, remove ViewLabel/ReviewGates from compose, dynamic sidebar CSS class, unified log in refresh/streaming |
| `forge/tui/widgets/agent_output.py` | Add `format_unified_output()`, `append_unified()`, `update_unified()`, `_unified_entries` field |
| `forge/tui/state.py` | Add `unified_log` field, append to it in `_on_agent_output`, `_on_review_llm_output`, `_on_review_gate_passed/failed`, clear in `reset()` |
| `forge/tui/screens/review.py` | Remove TaskList from compose, DiffViewer full-width, j/k scroll diff instead of navigate tasks |

## Files NOT Changed

| File | Reason |
|------|--------|
| `forge/tui/widgets/task_list.py` | No changes — still used in PipelineScreen during execution |
| `forge/tui/widgets/review_gates.py` | Kept for backwards compat — no longer composed in PipelineScreen but could be used elsewhere |
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

6. **ReviewScreen j/k keys:** Without TaskList, `j`/`k` should scroll the DiffViewer content instead. The DiffViewer already uses `VerticalScroll` internally (via its render), so we bind j/k to scroll actions.

## Testing Plan

1. **Visual verification:** Run `forge tui`, start a pipeline, verify:
   - PhaseBanner shows wide-spaced text, 5 lines tall
   - No sidebar during planning phase
   - Sidebar appears when execution starts
   - Unified log shows both agent and review sections with colored headers
2. **ReviewScreen:** Verify DiffViewer is full-width, no TaskList visible
3. **Key bindings:** Verify `j`/`k` scroll diff in ReviewScreen, `1-9` jump tasks, `d`/`t`/`c` switch views in PipelineScreen
4. **Streaming:** Verify agent lines appear under `AGENT` header, review lines under `REVIEW N` header, no flickering
5. **Error mode:** Verify error detail view still works (takes over unified log display)
6. **Pipeline replay:** Verify read-only mode respects dynamic sidebar (hidden during planning, shown during execution)

## Out of Scope

- Color theme customization (future enhancement)
- Configurable sidebar width
- Resizable split pane (Textual doesn't support mouse drag resize)
- Touch/mouse support for task switching (terminal only)
