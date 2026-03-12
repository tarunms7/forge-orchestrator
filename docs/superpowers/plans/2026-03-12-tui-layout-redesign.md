# TUI Layout Redesign — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make stage labels bigger, hide sidebar except during execution, and merge agent+review logs into one unified chronological stream.

**Architecture:** Surgical changes across 4 files. State layer adds `unified_log` dict. AgentOutput widget gets `format_unified_output()` renderer + `append_unified()`/`update_unified()` methods. PipelineScreen gets dynamic sidebar via CSS class toggling, bigger PhaseBanner, and removes ViewLabel/ReviewGates. ReviewScreen drops TaskList for full-width diff.

**Tech Stack:** Python 3.12, Textual TUI framework, Rich markup

**Spec:** `docs/superpowers/specs/2026-03-12-tui-layout-redesign.md`

**Test convention:** Co-located `<module>_test.py` files, pytest, `pytest-asyncio` for async widget tests.

---

## Chunk 1: State + Widget Foundations

Leaf changes — no screen modifications yet. Safe to test independently.

### Task 1: Add `unified_log` to TuiState

**Files:**
- Modify: `forge/tui/state.py`
- Modify: `forge/tui/state_test.py`

- [ ] **Step 1: Write tests for unified_log**

Add to `forge/tui/state_test.py`:

```python
# --- unified_log ---

def test_initial_state_has_unified_log():
    state = TuiState()
    assert state.unified_log == {}


def test_agent_output_appends_to_unified_log():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "Creating file..."})
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "Done."})
    assert state.unified_log["t1"] == [("agent", "Creating file..."), ("agent", "Done.")]


def test_review_llm_output_appends_to_unified_log():
    state = TuiState()
    state.apply_event("review:llm_output", {"task_id": "t1", "line": "Checking scope..."})
    assert state.unified_log["t1"] == [("review", "Checking scope...")]


def test_unified_log_interleaves_agent_and_review():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "agent line"})
    state.apply_event("review:llm_output", {"task_id": "t1", "line": "review line"})
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "agent line 2"})
    assert state.unified_log["t1"] == [
        ("agent", "agent line"),
        ("review", "review line"),
        ("agent", "agent line 2"),
    ]


def test_unified_log_ring_buffer():
    state = TuiState(max_output_lines=3)
    for i in range(5):
        state.apply_event("task:agent_output", {"task_id": "t1", "line": f"line {i}"})
    assert len(state.unified_log["t1"]) == 3
    assert state.unified_log["t1"][0] == ("agent", "line 2")


def test_review_gate_passed_appends_to_unified_log():
    state = _make_state_with_task("t1")
    state.apply_event("review:gate_passed", {"task_id": "t1", "gate": "gate0_build", "details": "passed"})
    assert len(state.unified_log["t1"]) == 1
    assert state.unified_log["t1"][0][0] == "gate"
    assert "Build" in state.unified_log["t1"][0][1]
    assert "✓" in state.unified_log["t1"][0][1]


def test_review_gate_failed_appends_to_unified_log():
    state = _make_state_with_task("t1")
    state.apply_event("review:gate_failed", {"task_id": "t1", "gate": "gate1_lint", "details": "3 errors"})
    assert len(state.unified_log["t1"]) == 1
    assert state.unified_log["t1"][0][0] == "gate"
    assert "Lint" in state.unified_log["t1"][0][1]
    assert "✗" in state.unified_log["t1"][0][1]


def test_reset_clears_unified_log():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "x"})
    state.reset()
    assert state.unified_log == {}


def test_restarted_clears_unified_log():
    state = _make_state_with_task("t1")
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "x"})
    state.apply_event("pipeline:restarted", {})
    assert state.unified_log == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest forge/tui/state_test.py -v -k "unified_log"`
Expected: FAIL (unified_log not defined)

- [ ] **Step 3: Implement unified_log in TuiState**

In `forge/tui/state.py`:

1. Add to `__init__` (after `self.review_output` line):
```python
self.unified_log: dict[str, list[tuple[str, str]]] = defaultdict(list)
```

2. Modify `_on_agent_output` — add after existing `agent_output` append logic, before `self._notify`:
```python
        # Unified log
        ulog = self.unified_log[tid]
        ulog.append(("agent", line))
        if len(ulog) > self._max_output_lines:
            del ulog[: len(ulog) - self._max_output_lines]
```

3. Modify `_on_review_llm_output` — add after existing `review_output` append logic, before `self._notify`:
```python
            # Unified log
            ulog = self.unified_log[task_id]
            ulog.append(("review", line))
            if len(ulog) > self._max_output_lines:
                del ulog[: len(ulog) - self._max_output_lines]
```

4. Modify `_on_review_gate_passed` — add before `self._notify("tasks")`:
```python
        # Unified log
        _GATE_LABELS = {"gate0_build": "🔨 Build", "gate1_lint": "📏 Lint",
                        "gate1_5_test": "🧪 Tests", "gate2_llm_review": "🤖 LLM Review"}
        gate_label = _GATE_LABELS.get(gate, gate)
        self.unified_log[task_id].append(("gate", f"{gate_label}: ✓ {data.get('details', 'passed')}"))
```

5. Modify `_on_review_gate_failed` — same pattern with `✗`:
```python
        _GATE_LABELS = {"gate0_build": "🔨 Build", "gate1_lint": "📏 Lint",
                        "gate1_5_test": "🧪 Tests", "gate2_llm_review": "🤖 LLM Review"}
        gate_label = _GATE_LABELS.get(gate, gate)
        self.unified_log[task_id].append(("gate", f"{gate_label}: ✗ {data.get('details', 'failed')}"))
```

6. Add `self.unified_log.clear()` to `reset()` method.

7. Add `self.unified_log.clear()` to `_on_restarted()` method (alongside existing `.clear()` calls).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest forge/tui/state_test.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```
git add forge/tui/state.py forge/tui/state_test.py
git commit -m "feat(tui): add unified_log to TuiState for chronological agent+review stream"
```

---

### Task 2: Add `format_unified_output` to agent_output.py

**Files:**
- Modify: `forge/tui/widgets/agent_output.py`
- Modify: `forge/tui/widgets/agent_output_test.py`

- [ ] **Step 1: Write tests for format_unified_output**

Add to `forge/tui/widgets/agent_output_test.py`:

```python
from forge.tui.widgets.agent_output import format_unified_output

# ── format_unified_output tests ────────────────────────────────────────


def test_format_unified_output_empty_shows_spinner():
    result = format_unified_output([])
    assert "Waiting" in result


def test_format_unified_output_agent_section_header():
    entries = [("agent", "line 1"), ("agent", "line 2")]
    result = format_unified_output(entries)
    assert "AGENT" in result
    assert "─────" in result
    assert "line 1" in result
    assert "line 2" in result


def test_format_unified_output_review_section_header():
    entries = [("review", "review line")]
    result = format_unified_output(entries)
    assert "REVIEW 1" in result
    assert "review line" in result


def test_format_unified_output_interleaved_sections():
    entries = [
        ("agent", "agent 1"),
        ("review", "review 1"),
        ("agent", "agent 2"),
    ]
    result = format_unified_output(entries)
    # Should have AGENT header, then REVIEW 1, then AGENT again
    assert result.count("AGENT") == 2
    assert "REVIEW 1" in result


def test_format_unified_output_review_count_increments():
    entries = [
        ("agent", "a1"),
        ("review", "r1"),
        ("agent", "a2"),
        ("review", "r2"),
    ]
    result = format_unified_output(entries)
    assert "REVIEW 1" in result
    assert "REVIEW 2" in result


def test_format_unified_output_gate_merges_into_review():
    """Gate entries should appear under the review section, not create their own header."""
    entries = [
        ("agent", "coding..."),
        ("gate", "🔨 Build: ✓ passed"),
        ("review", "analyzing..."),
    ]
    result = format_unified_output(entries)
    # gate should trigger a REVIEW section, not a GATE section
    assert "REVIEW 1" in result
    assert "🔨 Build: ✓ passed" in result
    assert "GATE" not in result


def test_format_unified_output_gate_formatting():
    """Gate lines should be indented and colored."""
    entries = [("gate", "🔨 Build: ✓ passed")]
    result = format_unified_output(entries)
    assert "#79c0ff" in result  # gate color


def test_format_unified_output_streaming_indicator():
    entries = [("agent", "working...")]
    result = format_unified_output(entries, streaming=True, typing_frame=0)
    assert "Typing" in result


def test_format_unified_output_no_streaming_indicator_by_default():
    entries = [("agent", "done")]
    result = format_unified_output(entries)
    assert "Typing" not in result


def test_format_unified_output_valid_rich_markup():
    """Output should be valid Rich markup."""
    from rich.console import Console
    from io import StringIO
    entries = [
        ("agent", "line 1"),
        ("gate", "🔨 Build: ✓ ok"),
        ("review", "looks good"),
    ]
    result = format_unified_output(entries)
    console = Console(file=StringIO(), force_terminal=True)
    console.print(result)  # Raises MarkupError if broken
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest forge/tui/widgets/agent_output_test.py -v -k "unified"`
Expected: FAIL (format_unified_output not defined)

- [ ] **Step 3: Implement format_unified_output**

Add to `forge/tui/widgets/agent_output.py` (after the `format_output` function):

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
        # Gate lines merge into review section
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

        if source_type == "gate":
            parts.append(f"  [#79c0ff]{line}[/]")
        else:
            parts.append(line)

    if streaming:
        cursor = _TYPING_FRAMES[typing_frame % len(_TYPING_FRAMES)]
        parts.append(f"[#58a6ff]● Typing{cursor}[/]")

    return "\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest forge/tui/widgets/agent_output_test.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```
git add forge/tui/widgets/agent_output.py forge/tui/widgets/agent_output_test.py
git commit -m "feat(tui): add format_unified_output for chronological log rendering"
```

---

### Task 3: Add `append_unified`/`update_unified` to AgentOutput widget

**Files:**
- Modify: `forge/tui/widgets/agent_output.py`
- Modify: `forge/tui/widgets/agent_output_test.py`

- [ ] **Step 1: Write tests**

Add to `forge/tui/widgets/agent_output_test.py`:

```python
# ── AgentOutput unified methods ────────────────────────────────────


def test_agent_output_init_has_unified_entries():
    widget = AgentOutput()
    assert widget._unified_entries == []


def test_append_unified_adds_to_entries():
    widget = AgentOutput()
    widget.append_unified("agent", "first line")
    assert widget._unified_entries == [("agent", "first line")]
    widget.append_unified("review", "review line")
    assert widget._unified_entries == [("agent", "first line"), ("review", "review line")]


def test_append_unified_before_compose():
    """append_unified should not raise before widget is composed."""
    widget = AgentOutput()
    widget.append_unified("agent", "safe to call")
    assert widget._unified_entries == [("agent", "safe to call")]


def test_update_unified_replaces_entries():
    widget = AgentOutput()
    widget._unified_entries = [("agent", "old")]
    widget.update_unified("t1", "Title", "running", [("agent", "new")])
    assert widget._unified_entries == [("agent", "new")]
    assert widget._task_id == "t1"
    assert widget._title == "Title"
    assert widget._state == "running"


def test_update_unified_resets_streaming():
    widget = AgentOutput()
    widget._streaming = True
    widget.update_unified("t1", "T", "s", [("agent", "x")])
    assert widget._streaming is False


def test_update_unified_before_compose():
    """update_unified should not raise before widget is composed."""
    widget = AgentOutput()
    widget.update_unified("t1", "Title", "running", [("agent", "line")])
    assert widget._unified_entries == [("agent", "line")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest forge/tui/widgets/agent_output_test.py -v -k "unified"`
Expected: FAIL (append_unified/update_unified not defined)

- [ ] **Step 3: Implement**

In `forge/tui/widgets/agent_output.py`:

1. Add to `__init__`:
```python
self._unified_entries: list[tuple[str, str]] = []
```

2. Add methods (after `update_header`):
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
            pass  # Not yet composed

    def update_unified(
        self,
        task_id: str | None,
        title: str | None,
        state: str | None,
        entries: list[tuple[str, str]],
    ) -> None:
        """Full refresh with unified log entries.

        Replaces _unified_entries with the authoritative state from TuiState.
        """
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
            pass  # Not yet composed
```

- [ ] **Step 4: Update `_tick_spinner` to guard on `_unified_entries`**

The existing `_tick_spinner` returns early when `self._lines` is non-empty (skipping spinner animation). It must also return early when `self._unified_entries` is non-empty, and use `format_unified_output` for the spinner:

```python
    def _tick_spinner(self) -> None:
        if self._lines or self._unified_entries:
            return
        self._spinner_frame += 1
        try:
            content = self.query_one("#agent-content", Static)
            content.update(format_output([], self._spinner_frame))
        except Exception:
            pass
```

Add test to `forge/tui/widgets/agent_output_test.py`:

```python
def test_tick_spinner_skipped_when_unified_entries_present():
    """_tick_spinner should not overwrite content when unified entries exist."""
    widget = AgentOutput()
    widget._unified_entries = [("agent", "some content")]
    initial_frame = widget._spinner_frame
    widget._tick_spinner()
    assert widget._spinner_frame == initial_frame  # Should not increment
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest forge/tui/widgets/agent_output_test.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```
git add forge/tui/widgets/agent_output.py forge/tui/widgets/agent_output_test.py
git commit -m "feat(tui): add append_unified/update_unified to AgentOutput widget"
```

---

## Chunk 2: PipelineScreen Changes

Depends on Chunk 1. Modifies PipelineScreen compose, CSS, PhaseBanner, view switching, and streaming paths.

### Task 4: PhaseBanner — wide-spaced labels + height 5

**Files:**
- Modify: `forge/tui/screens/pipeline.py` (PhaseBanner class + PipelineScreen CSS)
- Modify: `forge/tui/screens/pipeline_test.py`

- [ ] **Step 1: Write test for wide-spaced rendering**

Add to `forge/tui/screens/pipeline_test.py`:

```python
# ── PhaseBanner wide-spacing tests ──────────────────────────────


def test_phase_banner_wide_spacing():
    from forge.tui.screens.pipeline import PhaseBanner
    banner = PhaseBanner()
    banner._phase = "planning"
    rendered = banner.render()
    # Should contain wide-spaced "P L A N N I N G"
    assert "P  L  A  N  N  I  N  G" in rendered


def test_phase_banner_multiword_wide_spacing():
    from forge.tui.screens.pipeline import PhaseBanner
    banner = PhaseBanner()
    banner._phase = "planned"
    rendered = banner.render()
    # "Plan Approval" → "P L A N   A P P R O V A L" (triple-space between words)
    assert "P  L  A  N" in rendered
    assert "A  P  P  R  O  V  A  L" in rendered


def test_phase_banner_icon_preserved():
    from forge.tui.screens.pipeline import PhaseBanner
    banner = PhaseBanner()
    banner._phase = "executing"
    rendered = banner.render()
    assert "⚡" in rendered
    assert "E  X  E  C  U  T  I  O  N" in rendered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest forge/tui/screens/pipeline_test.py -v -k "phase_banner_wide"`
Expected: FAIL

- [ ] **Step 3: Implement PhaseBanner render changes**

In `forge/tui/screens/pipeline.py`, replace `PhaseBanner.render()`:

```python
    def render(self) -> str:
        label, colour = _PHASE_BANNER.get(self._phase, ("Unknown", "#8b949e"))
        # Extract icon prefix and text
        icon, _, text = label.partition(" ")
        if not text:
            text, icon = icon, ""
        # Wide-space: double-space within words, triple-space between words
        words = text.upper().split()
        spaced_words = ["  ".join(w) for w in words]
        spaced = "   ".join(spaced_words)
        icon_prefix = f"{icon}  " if icon else ""
        banner = f"[bold {colour}]{icon_prefix}{spaced}[/]"
        if self._read_only_banner:
            banner += f"\n[dim]{self._read_only_banner}[/]"
        return banner
```

Update CSS — both `PhaseBanner.DEFAULT_CSS` and `PipelineScreen.DEFAULT_CSS`:

In `PhaseBanner.DEFAULT_CSS`, change `height: 3` to `height: 5`:
```css
    PhaseBanner {
        width: 1fr;
        height: 5;
        content-align: center middle;
        text-align: center;
        background: #0d1117;
        border-bottom: tall #30363d;
    }
```

In `PipelineScreen.DEFAULT_CSS`, update the `PipelineScreen > PhaseBanner` rule, change `height: 3` to `height: 5`:
```css
    PipelineScreen > PhaseBanner {
        width: 100%;
        height: 5;
        ...
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest forge/tui/screens/pipeline_test.py -v -k "phase_banner"`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```
git add forge/tui/screens/pipeline.py forge/tui/screens/pipeline_test.py
git commit -m "feat(tui): PhaseBanner wide-spaced labels + height 5"
```

---

### Task 5: Dynamic sidebar — CSS class toggling

**Files:**
- Modify: `forge/tui/screens/pipeline.py` (CSS + `_refresh_all`)
- Modify: `forge/tui/screens/pipeline_test.py`

- [ ] **Step 1: Write tests**

Add to `forge/tui/screens/pipeline_test.py`:

```python
# ── Dynamic sidebar tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_sidebar_hidden_during_planning():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "planning"})
        await pilot.pause()
        split_pane = app.screen.query_one("#split-pane")
        assert split_pane.has_class("full-width")


@pytest.mark.asyncio
async def test_sidebar_shown_during_execution():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "executing"})
        await pilot.pause()
        split_pane = app.screen.query_one("#split-pane")
        assert not split_pane.has_class("full-width")


@pytest.mark.asyncio
async def test_sidebar_hidden_during_complete():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "complete"})
        await pilot.pause()
        split_pane = app.screen.query_one("#split-pane")
        assert split_pane.has_class("full-width")


@pytest.mark.asyncio
async def test_sidebar_shown_during_error():
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:phase_changed", {"phase": "error"})
        await pilot.pause()
        split_pane = app.screen.query_one("#split-pane")
        assert not split_pane.has_class("full-width")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest forge/tui/screens/pipeline_test.py -v -k "sidebar"`
Expected: FAIL

- [ ] **Step 3: Implement**

In `forge/tui/screens/pipeline.py`:

1. Add constant at module level (after `_VIEW_NAMES`):
```python
_SIDEBAR_HIDDEN_PHASES = frozenset({
    "idle", "planning", "planned", "contracts",
    "final_approval", "complete", "pr_creating", "pr_created", "cancelled",
})
```

2. Add CSS rules to `PipelineScreen.DEFAULT_CSS` (after `#split-pane` rule):
```css
    #split-pane.full-width #left-panel {
        display: none;
    }
    #split-pane.full-width #right-panel {
        width: 100%;
    }
```

3. In `_refresh_all()`, add after `phase_banner.update_phase(state.phase)`:
```python
        split_pane = self.query_one("#split-pane")
        if state.phase in _SIDEBAR_HIDDEN_PHASES:
            split_pane.add_class("full-width")
        else:
            split_pane.remove_class("full-width")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest forge/tui/screens/pipeline_test.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```
git add forge/tui/screens/pipeline.py forge/tui/screens/pipeline_test.py
git commit -m "feat(tui): dynamic sidebar — hidden during planning, shown during execution"
```

---

### Task 6: Remove ViewLabel/ReviewGates, integrate unified log

This is the largest task. Removes two widgets from compose, removes the `v` binding, updates view switching, fast paths, and `_refresh_all`.

**Files:**
- Modify: `forge/tui/screens/pipeline.py`
- Modify: `forge/tui/screens/pipeline_test.py`

- [ ] **Step 1: Remove ViewLabel and ReviewGates from compose + imports**

In `forge/tui/screens/pipeline.py`:

1. Remove `from forge.tui.widgets.review_gates import ReviewGates` import.

2. Remove `ViewLabel` class definition entirely (lines 109-145). Keep `DecisionBadge` and `PhaseBanner`.

3. Update `_VIEW_NAMES`:
```python
_VIEW_NAMES = ("output", "chat", "diff")
```

4. Update `compose()` — remove `ViewLabel()` and `ReviewGates()`:
```python
    def compose(self) -> ComposeResult:
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

5. Remove `ViewLabel` CSS from `PipelineScreen.DEFAULT_CSS` (the rule for ViewLabel if any).

6. Remove `#right-panel ReviewGates` CSS rule.

- [ ] **Step 2: Update `_set_view()` — remove ReviewGates and ViewLabel references**

```python
    def _set_view(self, view: str) -> None:
        """Show one right-panel view widget and hide the others."""
        assert view in _VIEW_NAMES, f"Unknown view: {view!r}"
        self._active_view = view

        widget_map: dict[str, type[Widget]] = {
            "output": AgentOutput,
            "chat": ChatThread,
            "diff": DiffViewer,
        }

        for name, cls in widget_map.items():
            w = self.query_one(cls)
            w.display = (name == view)
```

- [ ] **Step 3: Remove `v` binding and `action_view_review`**

Remove from `BINDINGS`:
```python
Binding("v", "view_review", "Review", show=True),
```

Remove method:
```python
def action_view_review(self) -> None:
    self._set_view("review")
```

- [ ] **Step 4: Update `_handle_review_output_fast` — route to AgentOutput**

Replace entirely:
```python
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

- [ ] **Step 5: Update `_handle_agent_output_fast` — use append_unified**

Replace entirely:
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
        agent_output.append_unified("agent", lines[-1])
```

- [ ] **Step 6: Update `_update_streaming_lifecycle` — remove ReviewGates references**

Replace entirely:
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
                    unified = state.unified_log.get(tid, [])
                    task = state.tasks.get(tid, {})
                    ao.update_unified(tid, task.get("title"), task.get("state"), unified)
                except Exception:
                    pass
```

- [ ] **Step 7: Update `_refresh_all` — remove ReviewGates, use unified log**

Replace the task output section (the block starting with `tid = state.selected_task_id`):

```python
        tid = state.selected_task_id

        # Show error detail view for errored tasks
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            unified = state.unified_log.get(tid, [])
            if task.get("state") == "error":
                agent_output.render_error_detail(tid, task, state.agent_output.get(tid, []))
            elif tid in self._agent_streaming_tasks or tid in self._review_streaming_tasks:
                # Streaming active — only update header, not content/scroll
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

        # Update diff for selected task (ReviewGates removed — data flows through unified log)
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
```

Remove the `review_gates = self.query_one(ReviewGates)` line and all ReviewGates method calls.

- [ ] **Step 8: Update `_check_review_auto_transition` notification text**

The notification at line 340 says `"press v to view"`, but the `v` binding is being removed. Update to:

```python
                        self.app.notify(
                            f"Task {title} entered review — finish typing to auto-open",
                            timeout=5,
                        )
```

- [ ] **Step 9: Update PipelineScreen docstring**

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
      - ChatThread    (auto-shown for questions)
      - DiffViewer    (toggled with 'd')
    """
```

- [ ] **Step 10: Update tests**

In `forge/tui/screens/pipeline_test.py`:

1. Remove `ViewLabel` import from line 9:
```python
# BEFORE:
from forge.tui.screens.pipeline import PipelineScreen, ViewLabel
# AFTER:
from forge.tui.screens.pipeline import PipelineScreen
```

2. Remove `test_v_key_opens_review_view` test entirely.

3. Remove `test_view_label_render_shows_new_keys` test entirely.

4. Update `test_agent_output_fast_path_calls_append_line` — change `append_line` to `append_unified`:
```python
@pytest.mark.asyncio
async def test_agent_output_fast_path_calls_append_unified():
    """agent_output fast path uses append_unified on AgentOutput widget."""
    state = TuiState()
    app = PipelineTestApp(state=state)
    async with app.run_test() as pilot:
        state.apply_event("pipeline:plan_ready", {
            "tasks": [{"id": "t1", "title": "Test", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
        })
        await pilot.pause()
        agent_output = app.screen.query_one("AgentOutput")
        with patch.object(agent_output, "append_unified") as mock_append:
            state.apply_event("task:agent_output", {"task_id": "t1", "line": "hello"})
            await pilot.pause()
            mock_append.assert_called_once_with("agent", "hello")
```

- [ ] **Step 11: Run all pipeline tests**

Run: `pytest forge/tui/screens/pipeline_test.py -v`
Expected: ALL PASS

- [ ] **Step 12: Commit**

```
git add forge/tui/screens/pipeline.py forge/tui/screens/pipeline_test.py
git commit -m "feat(tui): remove ViewLabel/ReviewGates, integrate unified log stream"
```

---

## Chunk 3: ReviewScreen Changes

Independent of Chunk 2 (but depends on Chunk 1 for unified_log existence).

### Task 7: Remove TaskList from ReviewScreen, full-width DiffViewer

**Files:**
- Modify: `forge/tui/screens/review.py`
- Modify: `forge/tui/screens/review_test.py`

- [ ] **Step 1: Write tests**

Replace `forge/tui/screens/review_test.py`:

```python
"""Tests for ReviewScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.review import ReviewScreen
from forge.tui.state import TuiState


class ReviewTestApp(App):
    def compose(self) -> ComposeResult:
        yield ReviewScreen(TuiState())


@pytest.mark.asyncio
async def test_review_screen_mounts():
    app = ReviewTestApp()
    async with app.run_test() as pilot:
        assert app.query_one("DiffViewer") is not None


@pytest.mark.asyncio
async def test_review_screen_no_task_list():
    """ReviewScreen should NOT contain a TaskList (sidebar removed)."""
    app = ReviewTestApp()
    async with app.run_test() as pilot:
        assert len(app.query("TaskList")) == 0


@pytest.mark.asyncio
async def test_review_screen_status_bar_text():
    """Status bar should show scroll/jump-task instructions, not navigate."""
    app = ReviewTestApp()
    async with app.run_test() as pilot:
        # Find the status bar
        status = app.query_one("#review-status")
        rendered = str(status.renderable)
        assert "scroll" in rendered
        assert "jump" in rendered or "1-9" in rendered
```

- [ ] **Step 2: Run tests to verify the new test_review_screen_no_task_list fails**

Run: `pytest forge/tui/screens/review_test.py -v`
Expected: `test_review_screen_no_task_list` FAILS (TaskList still in compose)

- [ ] **Step 3: Implement ReviewScreen changes**

In `forge/tui/screens/review.py`:

1. Remove imports:
```python
# REMOVE:
from textual.containers import Horizontal
from forge.tui.widgets.task_list import TaskList
```

2. Update `DEFAULT_CSS` — remove `#review-pane`:
```python
    DEFAULT_CSS = """
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
    """
```

3. Update `compose()`:
```python
    def compose(self) -> ComposeResult:
        yield Static("[bold #a371f7]REVIEW[/]", id="review-header")
        yield DiffViewer()
        yield Static("[a] approve  [x] reject  [e] editor  [j/k] scroll  [1-9] jump task", id="review-status")
```

4. Update `_refresh()` — remove TaskList update:
```python
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
```

5. Replace `action_cursor_down/up` — scroll DiffViewer:
```python
    def action_cursor_down(self) -> None:
        self.query_one(DiffViewer).scroll_relative(y=3)

    def action_cursor_up(self) -> None:
        self.query_one(DiffViewer).scroll_relative(y=-3)
```

6. Remove `on_task_list_selected` method entirely.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest forge/tui/screens/review_test.py -v`
Expected: ALL PASS

- [ ] **Step 5: Add integration test for j/k scroll via DiffViewer.scroll_relative**

Add to `forge/tui/screens/review_test.py`:

```python
@pytest.mark.asyncio
async def test_review_screen_j_k_scrolls_diff_viewer():
    """j/k keys should call scroll_relative on DiffViewer without error."""
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [{"id": "t1", "title": "Test", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
    })
    state.selected_task_id = "t1"

    class ReviewTestAppWithState(App):
        def compose(self) -> ComposeResult:
            yield ReviewScreen(state)

    app = ReviewTestAppWithState()
    async with app.run_test() as pilot:
        # Populate DiffViewer with enough content to scroll
        diff_viewer = app.query_one("DiffViewer")
        diff_viewer.update_diff("t1", "Test", "line\n" * 100)
        await pilot.pause()
        # j/k should not raise
        await pilot.press("j")
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        # If we get here without error, scroll_relative works
```

- [ ] **Step 6: Run full test suite**

Run: `pytest forge/tui/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```
git add forge/tui/screens/review.py forge/tui/screens/review_test.py
git commit -m "feat(tui): ReviewScreen full-width DiffViewer, no sidebar"
```

---

## Chunk 4: Final Verification

### Task 8: Full test pass + syntax check

- [ ] **Step 1: Run all TUI tests**

Run: `pytest forge/tui/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Python syntax check all modified files**

Run: `python -m py_compile forge/tui/state.py && python -m py_compile forge/tui/widgets/agent_output.py && python -m py_compile forge/tui/screens/pipeline.py && python -m py_compile forge/tui/screens/review.py && echo "All OK"`
Expected: "All OK"

- [ ] **Step 3: Verify no broken imports**

Run: `python -c "from forge.tui.screens.pipeline import PipelineScreen; from forge.tui.screens.review import ReviewScreen; from forge.tui.state import TuiState; print('OK')"`
Expected: "OK"

- [ ] **Step 4: Fix any remaining failures and commit**

---

## Verification Plan

1. **Unit tests:** `pytest forge/tui/ -v` — all existing + new tests pass
2. **Import test:** `python -c "from forge.tui.screens.pipeline import PipelineScreen"` succeeds
3. **Smoke test:** `forge tui` launches without crash
4. **Visual:** Start a pipeline → PhaseBanner shows wide-spaced text, no sidebar during planning, sidebar appears during execution, unified log shows both agent and review sections
