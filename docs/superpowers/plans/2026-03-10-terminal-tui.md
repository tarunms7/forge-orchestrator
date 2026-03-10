# Terminal TUI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the web UI with a full terminal TUI using Textual — users run `forge` and get a rich interactive terminal experience with split-pane layout, streaming agent output, inline diffs, and vim-style keybindings.

**Architecture:** EventBus pattern with two swappable sources (EmbeddedSource for in-process daemon, ClientSource for WebSocket to running server). TuiState reactive container subscribes to EventBus and drives Textual widget updates. Smart launch probes localhost:8000 to pick the right source.

**Tech Stack:** Python 3.12, Textual >=0.50, Rich (for syntax highlighting/SVG export), asyncio, SQLAlchemy (via existing Database class)

**Spec:** `docs/superpowers/specs/2026-03-10-terminal-tui-design.md`

**Test convention:** Co-located `_test.py` files (e.g., `forge/tui/bus_test.py`), pytest, Textual's `pilot` API for widget testing.

---

## File Structure

```
forge/tui/
  __init__.py          — Package init, exports ForgeApp
  bus.py               — EventBus, EmbeddedSource, ClientSource
  bus_test.py          — Tests for bus
  state.py             — TuiState reactive container
  state_test.py        — Tests for state
  app.py               — ForgeApp(textual.App), screen management, smart launch
  app_test.py          — Tests for app
  screens/
    __init__.py        — Screen exports
    home.py            — HomeScreen: logo + prompt input + recent pipelines
    home_test.py       — Tests for home
    pipeline.py        — PipelineScreen: split-pane task list + agent output
    pipeline_test.py   — Tests for pipeline
    review.py          — ReviewScreen: diff viewer + approve/reject
    review_test.py     — Tests for review
    settings.py        — SettingsScreen: config display + $EDITOR launch
    settings_test.py   — Tests for settings
  widgets/
    __init__.py        — Widget exports
    logo.py            — Forge flame logo widget
    task_list.py       — Task list panel with status icons
    task_list_test.py  — Tests for task list
    agent_output.py    — Streaming agent output panel
    agent_output_test.py — Tests for agent output
    progress_bar.py    — Pipeline progress bar + cost display
    dag.py             — ASCII DAG overlay (toggleable)
    diff_viewer.py     — Inline diff viewer with syntax highlighting
```

---

## Chunk 1: Foundation — EventBus + TuiState

Core infrastructure that everything depends on. No Textual imports — pure async Python.

### Task 1: EventBus with EmbeddedSource

**Files:**
- Create: `forge/tui/__init__.py`
- Create: `forge/tui/bus.py`
- Create: `forge/tui/bus_test.py`

**Context:** The existing `forge/core/events.py` has an `EventEmitter` class that the daemon uses. The TUI bus wraps this same interface but adds source-switching (embedded vs client). The daemon's `_emit()` method (in `forge/core/daemon.py:100-108`) calls `self._events.emit(event_type, data)`. For embedded mode, the TUI subscribes directly to the daemon's EventEmitter instance. For client mode, it receives events over WebSocket.

**Event types the TUI cares about** (from `forge/api/routes/tasks.py:1713-1734` `_bridge_events`):
- `pipeline:phase_changed` — `{"phase": "planning"|"planned"|"executing"|"contracts"|"complete"}`
- `pipeline:plan_ready` — `{"tasks": [{"id", "title", "description", "files", "depends_on", "complexity"}]}`
- `task:state_changed` — `{"task_id": str, "state": str, "error"?: str}`
- `task:agent_output` — `{"task_id": str, "line": str}`
- `task:files_changed` — `{"task_id": str, "files": [str]}`
- `task:review_update` — `{"task_id": str, "review": str, ...}`
- `task:merge_result` — `{"task_id": str, "success": bool, "error"?: str}`
- `task:cost_update` — `{"task_id": str, "agent_cost": float, ...}`
- `task:awaiting_approval` — `{"task_id": str, ...}`
- `planner:output` — `{"line": str}`
- `pipeline:cost_update` — `{"total_cost_usd": float, ...}`

- [ ] **Step 1: Create package init**

```python
# forge/tui/__init__.py
"""Forge Terminal UI — Textual-based TUI for multi-agent orchestration."""
```

- [ ] **Step 2: Write failing tests for EventBus**

```python
# forge/tui/bus_test.py
"""Tests for the TUI event bus."""

import asyncio
import pytest
from forge.tui.bus import EventBus, EmbeddedSource


@pytest.mark.asyncio
async def test_bus_subscribe_and_receive():
    """Subscribers receive events emitted by bus."""
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("task:state_changed", handler)
    await bus.emit("task:state_changed", {"task_id": "t1", "state": "done"})

    assert len(received) == 1
    assert received[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_bus_unsubscribe():
    """Unsubscribed handlers stop receiving events."""
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("test:event", handler)
    await bus.emit("test:event", {"n": 1})
    bus.unsubscribe("test:event", handler)
    await bus.emit("test:event", {"n": 2})

    assert len(received) == 1


@pytest.mark.asyncio
async def test_bus_multiple_event_types():
    """Different event types route to different handlers."""
    bus = EventBus()
    a_events = []
    b_events = []

    async def handler_a(data):
        a_events.append(data)

    async def handler_b(data):
        b_events.append(data)

    bus.subscribe("type_a", handler_a)
    bus.subscribe("type_b", handler_b)

    await bus.emit("type_a", {"x": 1})
    await bus.emit("type_b", {"x": 2})

    assert len(a_events) == 1
    assert len(b_events) == 1


@pytest.mark.asyncio
async def test_bus_handler_error_does_not_crash():
    """A failing handler logs but doesn't prevent other handlers."""
    bus = EventBus()
    received = []

    async def bad_handler(data):
        raise RuntimeError("boom")

    async def good_handler(data):
        received.append(data)

    bus.subscribe("evt", bad_handler)
    bus.subscribe("evt", good_handler)
    await bus.emit("evt", {"ok": True})

    assert len(received) == 1


@pytest.mark.asyncio
async def test_embedded_source_bridges_emitter():
    """EmbeddedSource forwards EventEmitter events to the bus."""
    from forge.core.events import EventEmitter

    emitter = EventEmitter()
    bus = EventBus()
    source = EmbeddedSource(emitter, bus)

    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("task:state_changed", handler)
    source.connect()

    # Emit through the daemon's emitter — should arrive at bus
    await emitter.emit("task:state_changed", {"task_id": "t1", "state": "done"})

    assert len(received) == 1
    assert received[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_embedded_source_disconnect():
    """After disconnect, events no longer forward."""
    from forge.core.events import EventEmitter

    emitter = EventEmitter()
    bus = EventBus()
    source = EmbeddedSource(emitter, bus)

    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("task:state_changed", handler)
    source.connect()
    source.disconnect()

    await emitter.emit("task:state_changed", {"task_id": "t1", "state": "done"})

    assert len(received) == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/tui/bus_test.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'forge.tui.bus'`

- [ ] **Step 4: Implement EventBus and EmbeddedSource**

```python
# forge/tui/bus.py
"""TUI Event Bus — routes events from daemon to UI subscribers.

Two sources:
  - EmbeddedSource: bridges daemon's EventEmitter (in-process mode)
  - ClientSource: receives events over WebSocket (client mode)

The bus itself is source-agnostic. Widgets subscribe to event types
and receive data dicts.
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from forge.core.events import EventEmitter

logger = logging.getLogger("forge.tui.bus")

# Event types the TUI subscribes to (from daemon's _emit calls)
TUI_EVENT_TYPES = [
    "pipeline:phase_changed",
    "pipeline:plan_ready",
    "pipeline:cost_update",
    "pipeline:cost_estimate",
    "pipeline:budget_exceeded",
    "pipeline:contracts_ready",
    "pipeline:contracts_failed",
    "pipeline:cancelled",
    "pipeline:restarted",
    "pipeline:paused",
    "pipeline:resumed",
    "pipeline:pr_created",
    "pipeline:pr_failed",
    "pipeline:worktrees_cleaned",
    "pipeline:error",
    "task:state_changed",
    "task:agent_output",
    "task:files_changed",
    "task:review_update",
    "task:merge_result",
    "task:cost_update",
    "task:awaiting_approval",
    "planner:output",
    "contracts:output",
]


class EventBus:
    """Source-agnostic event bus for TUI widgets."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Register an async handler for an event type."""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """Remove a handler. No-op if not registered."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event_type: str, data: Any = None) -> None:
        """Dispatch event to all subscribers."""
        for handler in self._handlers.get(event_type, []):
            try:
                await handler(data)
            except Exception:
                logger.exception("Handler error for %r", event_type)


class EmbeddedSource:
    """Bridges daemon's EventEmitter to the TUI EventBus.

    Used in embedded mode when daemon runs in the same process.
    """

    def __init__(self, emitter: EventEmitter, bus: EventBus) -> None:
        self._emitter = emitter
        self._bus = bus
        self._connected = False
        self._bridge_handlers: dict[str, Callable] = {}

    def connect(self) -> None:
        """Start forwarding emitter events to bus."""
        if self._connected:
            return
        for event_type in TUI_EVENT_TYPES:
            async def _bridge(data: Any, _type: str = event_type) -> None:
                await self._bus.emit(_type, data)
            self._bridge_handlers[event_type] = _bridge
            self._emitter.on(event_type, _bridge)
        self._connected = True

    def disconnect(self) -> None:
        """Stop forwarding events."""
        if not self._connected:
            return
        for event_type, handler in self._bridge_handlers.items():
            handlers = self._emitter._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
        self._bridge_handlers.clear()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/tui/bus_test.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add forge/tui/__init__.py forge/tui/bus.py forge/tui/bus_test.py
git commit -m "feat(tui): add EventBus with EmbeddedSource"
```

### Task 2: TuiState reactive container

**Files:**
- Create: `forge/tui/state.py`
- Create: `forge/tui/state_test.py`

**Context:** TuiState is the single source of truth for all TUI data. It holds pipeline phase, task list, agent outputs, costs, etc. The EventBus pushes events into TuiState, and Textual widgets read from it. This is analogous to a Zustand store in the React frontend (see `web/src/stores/taskStore.ts`).

TuiState does NOT use Textual's `reactive` — it's a plain Python class with callbacks so it can be tested without Textual. The ForgeApp will bridge TuiState changes to Textual widget updates.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/state_test.py
"""Tests for TuiState."""

import pytest
from forge.tui.state import TuiState


def test_initial_state():
    """Fresh TuiState has sensible defaults."""
    state = TuiState()
    assert state.phase == "idle"
    assert state.tasks == {}
    assert state.selected_task_id is None
    assert state.total_cost_usd == 0.0
    assert state.pipeline_id is None


def test_apply_phase_changed():
    state = TuiState()
    state.apply_event("pipeline:phase_changed", {"phase": "planning"})
    assert state.phase == "planning"


def test_apply_plan_ready_populates_tasks():
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [
            {"id": "t1", "title": "Setup DB", "description": "...", "files": ["db.py"], "depends_on": [], "complexity": "low"},
            {"id": "t2", "title": "Add API", "description": "...", "files": ["api.py"], "depends_on": ["t1"], "complexity": "medium"},
        ]
    })
    assert len(state.tasks) == 2
    assert state.tasks["t1"]["title"] == "Setup DB"
    assert state.tasks["t1"]["state"] == "todo"
    assert state.selected_task_id == "t1"  # auto-select first


def test_apply_task_state_changed():
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [{"id": "t1", "title": "X", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
    })
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "in_progress"})
    assert state.tasks["t1"]["state"] == "in_progress"


def test_apply_agent_output_appends():
    state = TuiState()
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "Creating file..."})
    state.apply_event("task:agent_output", {"task_id": "t1", "line": "Done."})
    assert state.agent_output["t1"] == ["Creating file...", "Done."]


def test_agent_output_ring_buffer():
    """Output is capped at max_output_lines."""
    state = TuiState(max_output_lines=5)
    for i in range(10):
        state.apply_event("task:agent_output", {"task_id": "t1", "line": f"line {i}"})
    assert len(state.agent_output["t1"]) == 5
    assert state.agent_output["t1"][0] == "line 5"


def test_apply_cost_update():
    state = TuiState()
    state.apply_event("pipeline:cost_update", {"total_cost_usd": 1.23})
    assert state.total_cost_usd == 1.23


def test_apply_task_cost_update():
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [{"id": "t1", "title": "X", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"}]
    })
    state.apply_event("task:cost_update", {"task_id": "t1", "agent_cost": 0.5})
    assert state.tasks["t1"]["agent_cost"] == 0.5


def test_on_change_callback():
    """on_change callbacks fire when state changes."""
    state = TuiState()
    changes = []
    state.on_change(lambda field: changes.append(field))
    state.apply_event("pipeline:phase_changed", {"phase": "executing"})
    assert "phase" in changes


def test_task_counts():
    state = TuiState()
    state.apply_event("pipeline:plan_ready", {
        "tasks": [
            {"id": "t1", "title": "A", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"},
            {"id": "t2", "title": "B", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"},
            {"id": "t3", "title": "C", "description": "", "files": ["f"], "depends_on": [], "complexity": "low"},
        ]
    })
    state.apply_event("task:state_changed", {"task_id": "t1", "state": "done"})
    state.apply_event("task:state_changed", {"task_id": "t2", "state": "in_progress"})
    assert state.done_count == 1
    assert state.total_count == 3
    assert state.progress_pct == pytest.approx(33.3, abs=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/tui/state_test.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement TuiState**

```python
# forge/tui/state.py
"""Reactive state container for the TUI.

Holds all data the UI needs: pipeline phase, tasks, agent output, costs.
Widgets read from this; EventBus writes to this via apply_event().
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

logger = logging.getLogger("forge.tui.state")


class TuiState:
    """Single source of truth for TUI data."""

    def __init__(self, max_output_lines: int = 1000) -> None:
        self._max_output_lines = max_output_lines
        self._change_callbacks: list[Callable[[str], None]] = []

        # Pipeline state
        self.pipeline_id: str | None = None
        self.phase: str = "idle"
        self.total_cost_usd: float = 0.0
        self.elapsed_seconds: float = 0.0

        # Tasks: {task_id: {id, title, description, files, depends_on, complexity, state, ...}}
        self.tasks: dict[str, dict] = {}
        self.task_order: list[str] = []  # insertion-order task IDs
        self.selected_task_id: str | None = None

        # Agent output: {task_id: [lines]}
        self.agent_output: dict[str, list[str]] = defaultdict(list)

        # Planner output lines
        self.planner_output: list[str] = []

        # Error state
        self.error: str | None = None

    def on_change(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked with the changed field name."""
        self._change_callbacks.append(callback)

    def _notify(self, field: str) -> None:
        for cb in self._change_callbacks:
            try:
                cb(field)
            except Exception:
                logger.exception("Change callback error for field %r", field)

    def apply_event(self, event_type: str, data: dict) -> None:
        """Apply a daemon event to update state."""
        handler = self._EVENT_MAP.get(event_type)
        if handler:
            handler(self, data)

    # ── Event handlers ────────────────────────────────────────────

    def _on_phase_changed(self, data: dict) -> None:
        self.phase = data.get("phase", self.phase)
        self._notify("phase")

    def _on_plan_ready(self, data: dict) -> None:
        self.tasks.clear()
        self.task_order.clear()
        for t in data.get("tasks", []):
            tid = t["id"]
            self.tasks[tid] = {
                "id": tid,
                "title": t.get("title", ""),
                "description": t.get("description", ""),
                "files": t.get("files", []),
                "depends_on": t.get("depends_on", []),
                "complexity": t.get("complexity", "medium"),
                "state": "todo",
                "agent_cost": 0.0,
                "error": None,
            }
            self.task_order.append(tid)
        if self.task_order and not self.selected_task_id:
            self.selected_task_id = self.task_order[0]
        self._notify("tasks")

    def _on_task_state_changed(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["state"] = data.get("state", self.tasks[tid]["state"])
            if "error" in data:
                self.tasks[tid]["error"] = data["error"]
            self._notify("tasks")

    def _on_agent_output(self, data: dict) -> None:
        tid = data.get("task_id", "")
        line = data.get("line", "")
        lines = self.agent_output[tid]
        lines.append(line)
        if len(lines) > self._max_output_lines:
            del lines[: len(lines) - self._max_output_lines]
        self._notify("agent_output")

    def _on_planner_output(self, data: dict) -> None:
        self.planner_output.append(data.get("line", ""))
        self._notify("planner_output")

    def _on_cost_update(self, data: dict) -> None:
        if "total_cost_usd" in data:
            self.total_cost_usd = data["total_cost_usd"]
        self._notify("cost")

    def _on_task_cost_update(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            if "agent_cost" in data:
                self.tasks[tid]["agent_cost"] = data["agent_cost"]
            self._notify("tasks")

    def _on_review_update(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["review"] = data
            self._notify("tasks")

    def _on_merge_result(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["merge_result"] = data
            self._notify("tasks")

    def _on_awaiting_approval(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["state"] = "awaiting_approval"
            self._notify("tasks")

    def _on_pipeline_error(self, data: dict) -> None:
        self.error = data.get("error", "Unknown error")
        self._notify("error")

    # ── Derived properties ────────────────────────────────────────

    @property
    def done_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t["state"] == "done")

    @property
    def error_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t["state"] == "error")

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def progress_pct(self) -> float:
        if not self.tasks:
            return 0.0
        return (self.done_count / self.total_count) * 100

    @property
    def active_task_ids(self) -> list[str]:
        """Task IDs currently being worked on."""
        return [tid for tid, t in self.tasks.items() if t["state"] in ("in_progress", "in_review", "merging")]

    # ── Event dispatch map ────────────────────────────────────────

    _EVENT_MAP: dict[str, Callable[["TuiState", dict], None]] = {
        "pipeline:phase_changed": _on_phase_changed,
        "pipeline:plan_ready": _on_plan_ready,
        "pipeline:cost_update": _on_cost_update,
        "pipeline:error": _on_pipeline_error,
        "task:state_changed": _on_task_state_changed,
        "task:agent_output": _on_agent_output,
        "task:cost_update": _on_task_cost_update,
        "task:review_update": _on_review_update,
        "task:merge_result": _on_merge_result,
        "task:awaiting_approval": _on_awaiting_approval,
        "planner:output": _on_planner_output,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/tui/state_test.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add forge/tui/state.py forge/tui/state_test.py
git commit -m "feat(tui): add TuiState reactive container"
```

---

## Chunk 2: Widgets

Building blocks for screens. Each widget reads from TuiState and renders itself.

### Task 3: Logo widget

**Files:**
- Create: `forge/tui/widgets/__init__.py`
- Create: `forge/tui/widgets/logo.py`

**Context:** The logo is the minimal flame icon + "F O R G E" text (Option C from brainstorming). Uses Rich markup for color. Displayed on the HomeScreen.

- [ ] **Step 1: Create widgets package and logo widget**

```python
# forge/tui/widgets/__init__.py
"""Forge TUI widgets."""
```

```python
# forge/tui/widgets/logo.py
"""Forge flame logo widget."""

from textual.widget import Widget
from textual.app import ComposeResult
from textual.widgets import Static


FORGE_LOGO = """\
[#f0883e]  ╭╮[/]
[#f0883e] ╔██╗╮[/]   [bold #58a6ff]F O R G E[/]
[#f0883e] ╔████╗[/]
[#f0883e]  ╔█╗[/]    [#8b949e]multi-agent orchestration[/]
[#f0883e]   ╗[/]\
"""


class ForgeLogo(Static):
    """Renders the Forge flame logo with Rich markup."""

    DEFAULT_CSS = """
    ForgeLogo {
        width: auto;
        height: 5;
        content-align: center middle;
        text-align: center;
        padding: 1 0;
    }
    """

    def __init__(self) -> None:
        super().__init__(FORGE_LOGO)
```

- [ ] **Step 2: Commit**

```bash
git add forge/tui/widgets/__init__.py forge/tui/widgets/logo.py
git commit -m "feat(tui): add Forge flame logo widget"
```

### Task 4: TaskList widget

**Files:**
- Create: `forge/tui/widgets/task_list.py`
- Create: `forge/tui/widgets/task_list_test.py`

**Context:** Left pane of the split-pane layout. Shows tasks with status icons, allows j/k navigation. Reads from TuiState.tasks and TuiState.selected_task_id.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/widgets/task_list_test.py
"""Tests for TaskList widget."""

import pytest
from forge.tui.widgets.task_list import format_task_line, STATE_ICONS


def test_state_icons_all_states():
    """Every TaskState has an icon."""
    from forge.core.models import TaskState
    for state in TaskState:
        assert state.value in STATE_ICONS, f"Missing icon for {state.value}"


def test_format_task_line_todo():
    task = {"id": "t1", "title": "Setup database", "state": "todo", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert "Setup database" in line
    assert STATE_ICONS["todo"] in line


def test_format_task_line_selected():
    task = {"id": "t1", "title": "Setup database", "state": "todo", "complexity": "low"}
    line = format_task_line(task, selected=True)
    assert "▶" in line or "►" in line  # selection indicator


def test_format_task_line_done():
    task = {"id": "t1", "title": "Setup database", "state": "done", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert STATE_ICONS["done"] in line


def test_format_task_line_error():
    task = {"id": "t1", "title": "Setup database", "state": "error", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert STATE_ICONS["error"] in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/tui/widgets/task_list_test.py -v`

- [ ] **Step 3: Implement TaskList**

```python
# forge/tui/widgets/task_list.py
"""Task list widget — left pane of the split-pane layout."""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Static
from textual.message import Message
from textual.app import ComposeResult
from textual import on

STATE_ICONS: dict[str, str] = {
    "todo": "○",
    "in_progress": "●",
    "in_review": "◉",
    "awaiting_approval": "⊙",
    "merging": "◈",
    "done": "✔",
    "cancelled": "✘",
    "error": "✖",
}

STATE_COLORS: dict[str, str] = {
    "todo": "#8b949e",
    "in_progress": "#f0883e",
    "in_review": "#a371f7",
    "awaiting_approval": "#d29922",
    "merging": "#79c0ff",
    "done": "#3fb950",
    "cancelled": "#8b949e",
    "error": "#f85149",
}


def format_task_line(task: dict, *, selected: bool) -> str:
    """Format a single task line with Rich markup."""
    state = task.get("state", "todo")
    icon = STATE_ICONS.get(state, "?")
    color = STATE_COLORS.get(state, "#8b949e")
    title = task.get("title", "Untitled")
    indicator = "► " if selected else "  "
    return f"{indicator}[{color}]{icon}[/] {title}"


class TaskList(Widget):
    """Scrollable task list with keyboard navigation."""

    DEFAULT_CSS = """
    TaskList {
        width: 1fr;
        min-width: 25;
        max-width: 40;
        border-right: solid #30363d;
        padding: 0 1;
    }
    """

    class Selected(Message):
        """Posted when a task is selected."""
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._tasks: list[dict] = []
        self._selected_index: int = 0

    def update_tasks(self, tasks: list[dict], selected_id: str | None = None) -> None:
        """Refresh the task list."""
        self._tasks = tasks
        if selected_id:
            for i, t in enumerate(tasks):
                if t["id"] == selected_id:
                    self._selected_index = i
                    break
        self._selected_index = min(self._selected_index, max(0, len(tasks) - 1))
        self.refresh()

    @property
    def selected_task(self) -> dict | None:
        if 0 <= self._selected_index < len(self._tasks):
            return self._tasks[self._selected_index]
        return None

    def render(self) -> str:
        if not self._tasks:
            return "[#8b949e]No tasks yet[/]"
        lines = []
        for i, task in enumerate(self._tasks):
            lines.append(format_task_line(task, selected=(i == self._selected_index)))
        return "\n".join(lines)

    def action_cursor_down(self) -> None:
        if self._selected_index < len(self._tasks) - 1:
            self._selected_index += 1
            self.refresh()
            if task := self.selected_task:
                self.post_message(self.Selected(task["id"]))

    def action_cursor_up(self) -> None:
        if self._selected_index > 0:
            self._selected_index -= 1
            self.refresh()
            if task := self.selected_task:
                self.post_message(self.Selected(task["id"]))
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/widgets/task_list.py forge/tui/widgets/task_list_test.py
git commit -m "feat(tui): add TaskList widget with state icons"
```

### Task 5: AgentOutput widget

**Files:**
- Create: `forge/tui/widgets/agent_output.py`
- Create: `forge/tui/widgets/agent_output_test.py`

**Context:** Right pane showing streaming output from the currently selected agent. Ring buffer from TuiState, auto-scrolls to bottom.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/widgets/agent_output_test.py
"""Tests for AgentOutput widget."""

from forge.tui.widgets.agent_output import format_header, format_output


def test_format_header_with_task():
    header = format_header("task-1", "Auth middleware", "in_progress")
    assert "Auth middleware" in header
    assert "task-1" in header


def test_format_header_no_task():
    header = format_header(None, None, None)
    assert "No task selected" in header


def test_format_output_empty():
    result = format_output([])
    assert "Waiting" in result or result == ""


def test_format_output_with_lines():
    lines = ["Creating auth/jwt.py...", "Adding middleware...", "Done."]
    result = format_output(lines)
    assert "Creating auth/jwt.py..." in result
    assert "Done." in result
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement AgentOutput**

```python
# forge/tui/widgets/agent_output.py
"""Agent output panel — streams output for the selected task."""

from __future__ import annotations

from textual.widgets import Static
from textual.widget import Widget


def format_header(task_id: str | None, title: str | None, state: str | None) -> str:
    """Format the agent output header."""
    if not task_id:
        return "[#8b949e]No task selected[/]"
    state_label = f" [{state}]" if state else ""
    return f"[bold #58a6ff]{task_id}[/]: {title or 'Untitled'} [#8b949e]{state_label}[/]"


def format_output(lines: list[str]) -> str:
    """Format agent output lines."""
    if not lines:
        return "[#8b949e]Waiting for output...[/]"
    return "\n".join(lines)


class AgentOutput(Widget):
    """Scrollable agent output panel."""

    DEFAULT_CSS = """
    AgentOutput {
        width: 3fr;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._task_id: str | None = None
        self._title: str | None = None
        self._state: str | None = None
        self._lines: list[str] = []

    def update_output(
        self,
        task_id: str | None,
        title: str | None,
        state: str | None,
        lines: list[str],
    ) -> None:
        """Update the displayed output."""
        self._task_id = task_id
        self._title = title
        self._state = state
        self._lines = lines
        self.refresh()

    def render(self) -> str:
        header = format_header(self._task_id, self._title, self._state)
        body = format_output(self._lines)
        separator = "[#30363d]" + "─" * 50 + "[/]"
        return f"{header}\n{separator}\n{body}"
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/widgets/agent_output.py forge/tui/widgets/agent_output_test.py
git commit -m "feat(tui): add AgentOutput streaming widget"
```

### Task 6: ProgressBar widget

**Files:**
- Create: `forge/tui/widgets/progress_bar.py`

**Context:** Bottom bar showing pipeline progress + cost. Shows: `████████░░░░ 60% | 3/5 tasks | $2.41 | 4:32`

- [ ] **Step 1: Implement ProgressBar**

```python
# forge/tui/widgets/progress_bar.py
"""Pipeline progress bar with cost and timing."""

from __future__ import annotations

from textual.widget import Widget


def format_progress(
    done: int,
    total: int,
    cost_usd: float,
    elapsed_seconds: float,
    phase: str,
    *,
    bar_width: int = 30,
) -> str:
    """Render progress bar as Rich markup."""
    if total == 0:
        return f"[#8b949e]{phase}[/]"

    pct = done / total
    filled = int(pct * bar_width)
    empty = bar_width - filled
    bar = f"[#3fb950]{'█' * filled}[/][#21262d]{'░' * empty}[/]"

    minutes = int(elapsed_seconds) // 60
    seconds = int(elapsed_seconds) % 60
    time_str = f"{minutes}:{seconds:02d}"

    return f"{bar} {pct:.0%} │ {done}/{total} tasks │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"


class PipelineProgress(Widget):
    """Bottom progress bar."""

    DEFAULT_CSS = """
    PipelineProgress {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._done = 0
        self._total = 0
        self._cost_usd = 0.0
        self._elapsed = 0.0
        self._phase = "idle"

    def update_progress(
        self,
        done: int,
        total: int,
        cost_usd: float,
        elapsed: float,
        phase: str,
    ) -> None:
        self._done = done
        self._total = total
        self._cost_usd = cost_usd
        self._elapsed = elapsed
        self._phase = phase
        self.refresh()

    def render(self) -> str:
        return format_progress(
            self._done, self._total, self._cost_usd, self._elapsed, self._phase,
        )
```

- [ ] **Step 2: Commit**

```bash
git add forge/tui/widgets/progress_bar.py
git commit -m "feat(tui): add PipelineProgress bar widget"
```

### Task 7: DAG overlay widget

**Files:**
- Create: `forge/tui/widgets/dag.py`

**Context:** ASCII DAG showing task dependencies, toggleable with `g` key. Uses box-drawing characters to show connections between tasks.

- [ ] **Step 1: Implement DAG widget**

```python
# forge/tui/widgets/dag.py
"""ASCII DAG overlay showing task dependency graph."""

from __future__ import annotations

from textual.widget import Widget


def build_dag_text(tasks: list[dict]) -> str:
    """Build an ASCII representation of the task dependency graph.

    Args:
        tasks: List of task dicts with 'id', 'title', 'state', 'depends_on' keys.

    Returns:
        Multi-line string with ASCII DAG visualization.
    """
    if not tasks:
        return "[#8b949e]No tasks[/]"

    state_colors = {
        "todo": "#8b949e",
        "in_progress": "#f0883e",
        "in_review": "#a371f7",
        "awaiting_approval": "#d29922",
        "merging": "#79c0ff",
        "done": "#3fb950",
        "cancelled": "#8b949e",
        "error": "#f85149",
    }

    task_map = {t["id"]: t for t in tasks}
    lines = []

    for task in tasks:
        color = state_colors.get(task.get("state", "todo"), "#8b949e")
        deps = task.get("depends_on", [])
        title = task.get("title", task["id"])
        short_title = title[:30] + "…" if len(title) > 30 else title

        if deps:
            dep_str = ", ".join(d for d in deps if d in task_map)
            lines.append(f"  [{color}]●[/] {task['id']}: {short_title} [#8b949e]← {dep_str}[/]")
        else:
            lines.append(f"  [{color}]●[/] {task['id']}: {short_title}")

    return "\n".join(lines)


class DagOverlay(Widget):
    """Toggleable DAG overlay."""

    DEFAULT_CSS = """
    DagOverlay {
        width: 100%;
        height: auto;
        max-height: 15;
        padding: 1;
        background: #0d1117;
        border: solid #30363d;
        display: none;
    }
    DagOverlay.visible {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._tasks: list[dict] = []

    def update_tasks(self, tasks: list[dict]) -> None:
        self._tasks = tasks
        self.refresh()

    def toggle(self) -> None:
        self.toggle_class("visible")

    def render(self) -> str:
        header = "[bold #58a6ff]Task Dependencies[/] [#8b949e](g to close)[/]\n"
        return header + build_dag_text(self._tasks)
```

- [ ] **Step 2: Commit**

```bash
git add forge/tui/widgets/dag.py
git commit -m "feat(tui): add DAG overlay widget"
```

### Task 8: DiffViewer widget

**Files:**
- Create: `forge/tui/widgets/diff_viewer.py`

**Context:** Inline diff viewer for the review screen. Uses Rich syntax highlighting. Shows unified diff format with +/- coloring.

- [ ] **Step 1: Implement DiffViewer**

```python
# forge/tui/widgets/diff_viewer.py
"""Inline diff viewer with syntax highlighting."""

from __future__ import annotations

from textual.widget import Widget


def format_diff(diff_text: str) -> str:
    """Apply Rich markup to unified diff text."""
    if not diff_text:
        return "[#8b949e]No diff available[/]"

    lines = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            lines.append(f"[bold #8b949e]{_escape(line)}[/]")
        elif line.startswith("@@"):
            lines.append(f"[#79c0ff]{_escape(line)}[/]")
        elif line.startswith("+"):
            lines.append(f"[#3fb950]{_escape(line)}[/]")
        elif line.startswith("-"):
            lines.append(f"[#f85149]{_escape(line)}[/]")
        else:
            lines.append(_escape(line))
    return "\n".join(lines)


def _escape(text: str) -> str:
    """Escape Rich markup characters in diff content."""
    return text.replace("[", "\\[")


class DiffViewer(Widget):
    """Scrollable diff viewer."""

    DEFAULT_CSS = """
    DiffViewer {
        width: 100%;
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._diff_text: str = ""
        self._task_id: str | None = None
        self._task_title: str | None = None

    def update_diff(self, task_id: str, title: str, diff_text: str) -> None:
        self._task_id = task_id
        self._task_title = title
        self._diff_text = diff_text
        self.refresh()

    def render(self) -> str:
        if not self._task_id:
            return "[#8b949e]Select a task to view its diff[/]"
        header = f"[bold #58a6ff]{self._task_id}[/]: {self._task_title or ''}\n"
        separator = "[#30363d]" + "─" * 60 + "[/]\n"
        return header + separator + format_diff(self._diff_text)
```

- [ ] **Step 2: Commit**

```bash
git add forge/tui/widgets/diff_viewer.py
git commit -m "feat(tui): add DiffViewer widget"
```

---

## Chunk 3: Screens

Compose widgets into full screens. Each screen is a Textual Screen subclass.

### Task 9: Screens package + HomeScreen

**Files:**
- Create: `forge/tui/screens/__init__.py`
- Create: `forge/tui/screens/home.py`
- Create: `forge/tui/screens/home_test.py`

**Context:** The HomeScreen shows the Forge logo, a prompt input for new pipelines, and a list of recent pipelines. This is what users see when they first launch `forge`.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/screens/home_test.py
"""Tests for HomeScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.home import HomeScreen


class HomeTestApp(App):
    def compose(self) -> ComposeResult:
        yield HomeScreen()


@pytest.mark.asyncio
async def test_home_screen_mounts():
    """HomeScreen can be mounted without errors."""
    app = HomeTestApp()
    async with app.run_test() as pilot:
        # Screen should have the logo and input
        assert app.query_one("ForgeLogo") is not None
        assert app.query_one("Input") is not None
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement HomeScreen**

```python
# forge/tui/screens/__init__.py
"""Forge TUI screens."""
```

```python
# forge/tui/screens/home.py
"""Home screen — logo, prompt input, recent pipelines."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Input, Static
from textual.containers import Vertical, Center
from textual.message import Message

from forge.tui.widgets.logo import ForgeLogo


class HomeScreen(Screen):
    """Landing screen with logo and task input."""

    DEFAULT_CSS = """
    HomeScreen {
        align: center middle;
    }
    #home-container {
        width: 80;
        height: auto;
        max-height: 100%;
    }
    #prompt-input {
        margin: 1 2;
    }
    #recent-label {
        margin: 1 2 0 2;
        color: #8b949e;
    }
    #recent-list {
        margin: 0 2;
        height: auto;
        max-height: 10;
        color: #8b949e;
    }
    """

    BINDINGS = [
        ("escape", "app.quit", "Quit"),
    ]

    class TaskSubmitted(Message):
        """Posted when user submits a task description."""
        def __init__(self, task: str) -> None:
            self.task = task
            super().__init__()

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="home-container"):
                yield ForgeLogo()
                yield Input(placeholder="What should I build?", id="prompt-input")
                yield Static("Recent pipelines", id="recent-label")
                yield Static("[#8b949e]No recent pipelines[/]", id="recent-list")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        task = event.value.strip()
        if task:
            self.post_message(self.TaskSubmitted(task))
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/screens/__init__.py forge/tui/screens/home.py forge/tui/screens/home_test.py
git commit -m "feat(tui): add HomeScreen with logo and prompt input"
```

### Task 10: PipelineScreen

**Files:**
- Create: `forge/tui/screens/pipeline.py`
- Create: `forge/tui/screens/pipeline_test.py`

**Context:** The main screen during pipeline execution. Split-pane layout: TaskList on left, AgentOutput on right. Progress bar at bottom. DAG overlay toggled with `g`. This is the screen users spend most time on.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/screens/pipeline_test.py
"""Tests for PipelineScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.pipeline import PipelineScreen
from forge.tui.state import TuiState


class PipelineTestApp(App):
    def compose(self) -> ComposeResult:
        yield PipelineScreen(TuiState())


@pytest.mark.asyncio
async def test_pipeline_screen_mounts():
    """PipelineScreen can be mounted without errors."""
    app = PipelineTestApp()
    async with app.run_test() as pilot:
        assert app.query_one("TaskList") is not None
        assert app.query_one("AgentOutput") is not None
        assert app.query_one("PipelineProgress") is not None


@pytest.mark.asyncio
async def test_pipeline_screen_dag_toggle():
    """Pressing g toggles DAG overlay visibility."""
    app = PipelineTestApp()
    async with app.run_test() as pilot:
        dag = app.query_one("DagOverlay")
        assert not dag.has_class("visible")
        await pilot.press("g")
        assert dag.has_class("visible")
        await pilot.press("g")
        assert not dag.has_class("visible")
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement PipelineScreen**

```python
# forge/tui/screens/pipeline.py
"""Pipeline screen — split-pane task list + agent output."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Horizontal, Vertical
from textual.binding import Binding

from forge.tui.state import TuiState
from forge.tui.widgets.task_list import TaskList
from forge.tui.widgets.agent_output import AgentOutput
from forge.tui.widgets.progress_bar import PipelineProgress
from forge.tui.widgets.dag import DagOverlay


class PipelineScreen(Screen):
    """Main pipeline execution screen with split-pane layout."""

    DEFAULT_CSS = """
    PipelineScreen {
        layout: vertical;
    }
    #pipeline-header {
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #58a6ff;
    }
    #split-pane {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "toggle_dag", "Toggle DAG"),
        Binding("tab", "cycle_agent", "Next agent", show=False),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        yield DagOverlay()
        with Horizontal(id="split-pane"):
            yield TaskList()
            yield AgentOutput()
        yield PipelineProgress()

    def on_mount(self) -> None:
        """Wire up state changes to widget updates."""
        self._state.on_change(self._on_state_change)
        self._refresh_all()

    def _on_state_change(self, field: str) -> None:
        """React to state changes."""
        if field in ("tasks", "agent_output", "cost", "phase"):
            self._refresh_all()

    def _refresh_all(self) -> None:
        """Push current state to all widgets."""
        state = self._state
        task_list = self.query_one(TaskList)
        agent_output = self.query_one(AgentOutput)
        progress = self.query_one(PipelineProgress)
        dag = self.query_one(DagOverlay)

        # Update task list
        ordered_tasks = [state.tasks[tid] for tid in state.task_order if tid in state.tasks]
        task_list.update_tasks(ordered_tasks, state.selected_task_id)

        # Update agent output for selected task
        tid = state.selected_task_id
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            lines = state.agent_output.get(tid, [])
            agent_output.update_output(tid, task.get("title"), task.get("state"), lines)
        else:
            agent_output.update_output(None, None, None, [])

        # Update progress
        progress.update_progress(
            state.done_count, state.total_count,
            state.total_cost_usd, state.elapsed_seconds, state.phase,
        )

        # Update DAG
        dag.update_tasks(ordered_tasks)

    def on_task_list_selected(self, event: TaskList.Selected) -> None:
        """Handle task selection from TaskList."""
        self._state.selected_task_id = event.task_id
        self._refresh_all()

    def action_cursor_down(self) -> None:
        self.query_one(TaskList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(TaskList).action_cursor_up()

    def action_toggle_dag(self) -> None:
        self.query_one(DagOverlay).toggle()

    def action_cycle_agent(self) -> None:
        """Cycle to next active task's output."""
        active = self._state.active_task_ids
        if not active:
            return
        current = self._state.selected_task_id
        if current in active:
            idx = (active.index(current) + 1) % len(active)
        else:
            idx = 0
        self._state.selected_task_id = active[idx]
        self._refresh_all()

    def action_help(self) -> None:
        """Show help overlay."""
        pass  # Implemented in Task 14 (ForgeApp)
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/screens/pipeline.py forge/tui/screens/pipeline_test.py
git commit -m "feat(tui): add PipelineScreen with split-pane layout"
```

### Task 11: ReviewScreen

**Files:**
- Create: `forge/tui/screens/review.py`
- Create: `forge/tui/screens/review_test.py`

**Context:** Shows diffs for tasks in review. Approve with `a`, reject with `x`, navigate hunks with `j/k`, open in `$EDITOR` with `e`. Left side: task list filtered to reviewable tasks. Right side: diff viewer.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/screens/review_test.py
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
    """ReviewScreen can be mounted without errors."""
    app = ReviewTestApp()
    async with app.run_test() as pilot:
        assert app.query_one("DiffViewer") is not None
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement ReviewScreen**

```python
# forge/tui/screens/review.py
"""Review screen — diff viewer + approve/reject controls."""

from __future__ import annotations

import os
import subprocess
import tempfile

from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Horizontal, Vertical
from textual.widgets import Static
from textual.binding import Binding

from forge.tui.state import TuiState
from forge.tui.widgets.task_list import TaskList
from forge.tui.widgets.diff_viewer import DiffViewer

_REVIEWABLE_STATES = {"in_review", "awaiting_approval"}


class ReviewScreen(Screen):
    """Review screen with inline diff viewer."""

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
    #review-pane {
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

    BINDINGS = [
        Binding("a", "approve", "Approve"),
        Binding("x", "reject", "Reject"),
        Binding("e", "edit", "Open in $EDITOR"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, state: TuiState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        yield Static("[bold #a371f7]REVIEW[/]", id="review-header")
        with Horizontal(id="review-pane"):
            yield TaskList()
            yield DiffViewer()
        yield Static("[a] approve  [x] reject  [e] editor  [j/k] navigate", id="review-status")

    def on_mount(self) -> None:
        self._state.on_change(self._on_state_change)
        self._refresh()

    def _on_state_change(self, field: str) -> None:
        if field == "tasks":
            self._refresh()

    def _refresh(self) -> None:
        state = self._state
        reviewable = [
            state.tasks[tid] for tid in state.task_order
            if tid in state.tasks and state.tasks[tid]["state"] in _REVIEWABLE_STATES
        ]
        task_list = self.query_one(TaskList)
        task_list.update_tasks(reviewable, state.selected_task_id)

        # Show diff for selected task
        tid = state.selected_task_id
        if tid and tid in state.tasks:
            task = state.tasks[tid]
            diff = task.get("merge_result", {}).get("diff", "")
            if not diff:
                diff = task.get("review", {}).get("diff", "")
            self.query_one(DiffViewer).update_diff(tid, task.get("title", ""), diff)

    def on_task_list_selected(self, event: TaskList.Selected) -> None:
        self._state.selected_task_id = event.task_id
        self._refresh()

    def action_cursor_down(self) -> None:
        self.query_one(TaskList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(TaskList).action_cursor_up()

    def action_approve(self) -> None:
        """Post approval message for the app to handle."""
        tid = self._state.selected_task_id
        if tid:
            self.post_message(ReviewAction(tid, "approve"))

    def action_reject(self) -> None:
        tid = self._state.selected_task_id
        if tid:
            self.post_message(ReviewAction(tid, "reject"))

    def action_edit(self) -> None:
        """Open current diff in $EDITOR."""
        tid = self._state.selected_task_id
        if not tid or tid not in self._state.tasks:
            return
        task = self._state.tasks[tid]
        diff = task.get("merge_result", {}).get("diff", "")
        if not diff:
            return
        editor = os.environ.get("EDITOR", "vim")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
            f.write(diff)
            f.flush()
            self.app.suspend()
            try:
                subprocess.run([editor, f.name])
            finally:
                self.app.resume()
                os.unlink(f.name)


class ReviewAction:
    """Message for approve/reject actions."""
    def __init__(self, task_id: str, action: str) -> None:
        self.task_id = task_id
        self.action = action
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/screens/review.py forge/tui/screens/review_test.py
git commit -m "feat(tui): add ReviewScreen with diff viewer"
```

### Task 12: SettingsScreen

**Files:**
- Create: `forge/tui/screens/settings.py`
- Create: `forge/tui/screens/settings_test.py`

**Context:** Displays current ForgeSettings values. `Enter` opens `$EDITOR` on the config. Simple read-only display with editor escape hatch.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/screens/settings_test.py
"""Tests for SettingsScreen."""

import pytest
from textual.app import App, ComposeResult

from forge.tui.screens.settings import SettingsScreen, format_settings
from forge.config.settings import ForgeSettings


def test_format_settings():
    """Settings are formatted as key-value lines."""
    settings = ForgeSettings()
    text = format_settings(settings)
    assert "model_strategy" in text
    assert "max_agents" in text
    assert "budget_limit_usd" in text


class SettingsTestApp(App):
    def compose(self) -> ComposeResult:
        yield SettingsScreen(ForgeSettings())


@pytest.mark.asyncio
async def test_settings_screen_mounts():
    app = SettingsTestApp()
    async with app.run_test() as pilot:
        # Should have rendered settings content
        pass  # mount without crash is the test
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement SettingsScreen**

```python
# forge/tui/screens/settings.py
"""Settings screen — displays current configuration."""

from __future__ import annotations

import os
import subprocess

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.containers import Vertical
from textual.binding import Binding

from forge.config.settings import ForgeSettings

# Settings to display, grouped by category
_DISPLAY_GROUPS = {
    "Model": ["model_strategy"],
    "Agents": ["max_agents", "agent_timeout_seconds", "max_retries"],
    "Build & Test": ["build_cmd", "test_cmd"],
    "Budget": ["budget_limit_usd"],
    "Pipeline": ["pipeline_timeout_seconds", "require_approval", "contracts_required"],
    "Resources": ["cpu_threshold", "memory_threshold_pct", "disk_threshold_gb"],
}


def format_settings(settings: ForgeSettings) -> str:
    """Format settings as Rich markup for display."""
    lines = []
    for group_name, fields in _DISPLAY_GROUPS.items():
        lines.append(f"\n[bold #58a6ff]{group_name}[/]")
        for field in fields:
            value = getattr(settings, field, "?")
            env_var = f"FORGE_{field.upper()}"
            lines.append(f"  [#8b949e]{field}[/]: {value}  [dim]({env_var})[/dim]")
    return "\n".join(lines)


class SettingsScreen(Screen):
    """Settings display with $EDITOR launch."""

    DEFAULT_CSS = """
    SettingsScreen {
        layout: vertical;
    }
    #settings-header {
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #58a6ff;
    }
    #settings-body {
        padding: 1 2;
        overflow-y: auto;
    }
    #settings-footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #8b949e;
    }
    """

    BINDINGS = [
        Binding("enter", "edit_config", "Edit config"),
    ]

    def __init__(self, settings: ForgeSettings) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        yield Static("[bold #58a6ff]SETTINGS[/]", id="settings-header")
        yield Static(format_settings(self._settings), id="settings-body")
        yield Static("[Enter] edit config with $EDITOR", id="settings-footer")

    def action_edit_config(self) -> None:
        """Open settings in $EDITOR."""
        editor = os.environ.get("EDITOR", "vim")
        config_path = os.path.join(os.getcwd(), ".forge", "config.toml")
        self.app.suspend()
        try:
            subprocess.run([editor, config_path])
        finally:
            self.app.resume()
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/screens/settings.py forge/tui/screens/settings_test.py
git commit -m "feat(tui): add SettingsScreen"
```

---

## Chunk 4: App + CLI Integration

Wire everything together: ForgeApp, smart launch, CLI command.

### Task 13: ForgeApp (main application)

**Files:**
- Create: `forge/tui/app.py`
- Create: `forge/tui/app_test.py`

**Context:** ForgeApp is the top-level Textual App. It manages screen switching (Home → Pipeline → Review → Settings), wires EventBus → TuiState, and handles global keybindings.

The smart launch logic probes `localhost:8000/health` to decide between EmbeddedSource and ClientSource. The daemon startup follows the pattern in `forge/cli/main.py:run` — create `ForgeDaemon(project_dir, settings)`, call `daemon.run(task)`.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/app_test.py
"""Tests for ForgeApp."""

import pytest
from forge.tui.app import ForgeApp


@pytest.mark.asyncio
async def test_app_mounts():
    """ForgeApp starts on HomeScreen."""
    app = ForgeApp(project_dir="/tmp/test-forge")
    async with app.run_test() as pilot:
        assert app.screen.__class__.__name__ == "HomeScreen"


@pytest.mark.asyncio
async def test_app_quit():
    """Pressing q exits the app."""
    app = ForgeApp(project_dir="/tmp/test-forge")
    async with app.run_test() as pilot:
        await pilot.press("q")
        # App should have exited
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement ForgeApp**

```python
# forge/tui/app.py
"""Forge TUI Application — main entry point for the terminal UI."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from textual.app import App, ComposeResult
from textual.binding import Binding

from forge.config.settings import ForgeSettings
from forge.core.events import EventEmitter
from forge.tui.bus import EventBus, EmbeddedSource
from forge.tui.state import TuiState
from forge.tui.screens.home import HomeScreen
from forge.tui.screens.pipeline import PipelineScreen
from forge.tui.screens.review import ReviewScreen
from forge.tui.screens.settings import SettingsScreen

logger = logging.getLogger("forge.tui.app")


class ForgeApp(App):
    """Forge Terminal UI."""

    TITLE = "Forge"
    CSS = """
    Screen {
        background: #0d1117;
        color: #c9d1d9;
    }
    """

    BINDINGS = [
        Binding("1", "switch_home", "Home", show=True),
        Binding("2", "switch_pipeline", "Pipeline", show=True),
        Binding("3", "switch_review", "Review", show=True),
        Binding("4", "switch_settings", "Settings", show=True),
        Binding("q", "quit_app", "Quit"),
        Binding("s", "screenshot", "Screenshot", show=False),
    ]

    def __init__(
        self,
        project_dir: str,
        settings: ForgeSettings | None = None,
        server_url: str | None = None,
    ) -> None:
        super().__init__()
        self._project_dir = os.path.abspath(project_dir)
        self._settings = settings or ForgeSettings()
        self._server_url = server_url
        self._bus = EventBus()
        self._state = TuiState()
        self._source: EmbeddedSource | None = None
        self._daemon = None
        self._daemon_task: asyncio.Task | None = None
        self._elapsed_timer = None

    def compose(self) -> ComposeResult:
        yield HomeScreen()

    def on_mount(self) -> None:
        """Wire bus → state."""
        async def _bus_to_state(event_type: str):
            async def _handler(data):
                self._state.apply_event(event_type, data)
            return _handler

        for evt_type in self._bus._handlers.keys():
            pass  # Already wired via state.on_change in screens

        # State changes trigger screen refresh
        self._state.on_change(self._on_state_change)

    def _on_state_change(self, field: str) -> None:
        """Refresh current screen when state changes."""
        try:
            self.screen.refresh()
        except Exception:
            pass

    async def on_home_screen_task_submitted(self, event: HomeScreen.TaskSubmitted) -> None:
        """User submitted a task from HomeScreen."""
        task = event.task
        logger.info("Task submitted: %s", task)

        # Switch to pipeline screen
        pipeline_screen = PipelineScreen(self._state)
        self.push_screen(pipeline_screen)

        # Start daemon in background
        await self._start_pipeline(task)

    async def _start_pipeline(self, task: str) -> None:
        """Launch the daemon and start executing the task."""
        from forge.core.daemon import ForgeDaemon

        emitter = EventEmitter()
        self._bus = EventBus()
        self._source = EmbeddedSource(emitter, self._bus)
        self._source.connect()

        # Wire bus events to state
        from forge.tui.bus import TUI_EVENT_TYPES
        for evt_type in TUI_EVENT_TYPES:
            async def _handler(data, _type=evt_type):
                self._state.apply_event(_type, data)
            self._bus.subscribe(evt_type, _handler)

        self._daemon = ForgeDaemon(
            self._project_dir,
            settings=self._settings,
            event_emitter=emitter,
        )

        # Start elapsed timer
        self._start_time = asyncio.get_event_loop().time()
        self._elapsed_timer = self.set_interval(1.0, self._tick_elapsed)

        # Run daemon in background task
        self._daemon_task = asyncio.create_task(self._run_daemon(task))
        self._daemon_task.add_done_callback(self._on_daemon_done)

    async def _run_daemon(self, task: str) -> None:
        """Run the daemon pipeline."""
        try:
            await self._daemon.run(task)
        except Exception as e:
            logger.error("Daemon failed: %s", e, exc_info=True)
            self._state.apply_event("pipeline:error", {"error": str(e)})

    def _on_daemon_done(self, task: asyncio.Task) -> None:
        """Called when daemon task completes."""
        if self._elapsed_timer:
            self._elapsed_timer.stop()
        if not task.cancelled() and task.exception():
            logger.error("Daemon crashed: %s", task.exception())

    def _tick_elapsed(self) -> None:
        """Update elapsed time every second."""
        if hasattr(self, "_start_time"):
            self._state.elapsed_seconds = asyncio.get_event_loop().time() - self._start_time

    # ── Screen switching ──────────────────────────────────────────

    def action_switch_home(self) -> None:
        self.switch_screen(HomeScreen())

    def action_switch_pipeline(self) -> None:
        self.push_screen(PipelineScreen(self._state))

    def action_switch_review(self) -> None:
        self.push_screen(ReviewScreen(self._state))

    def action_switch_settings(self) -> None:
        self.push_screen(SettingsScreen(self._settings))

    def action_quit_app(self) -> None:
        """Quit with confirmation if pipeline is running."""
        if self._daemon_task and not self._daemon_task.done():
            # Pipeline running — ask for confirmation
            self.push_screen(ConfirmQuitScreen())
        else:
            self.exit()

    def action_screenshot(self) -> None:
        """Export current screen as SVG."""
        path = os.path.join(self._project_dir, "screenshots")
        os.makedirs(path, exist_ok=True)
        filename = os.path.join(path, f"forge-{self._state.phase}.svg")
        self.save_screenshot(filename)
        self.notify(f"Screenshot saved: {filename}")


class ConfirmQuitScreen(App):
    """Simple quit confirmation."""
    pass  # Placeholder — implemented inline via self.exit() for now
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/app.py forge/tui/app_test.py
git commit -m "feat(tui): add ForgeApp with screen management and daemon integration"
```

### Task 14: CLI entry point

**Files:**
- Modify: `forge/cli/main.py` (add `tui` command, ~5 lines after `run` command)

**Context:** Add `forge tui` as a new CLI command. Also make `forge run` launch the TUI instead of plain CLI output. The `forge` command (no args) should show the TUI HomeScreen.

The existing `run` command (lines 41-78 in `forge/cli/main.py`) does `asyncio.run(daemon.run(task))`. The TUI command will instead launch the Textual app which manages its own event loop.

- [ ] **Step 1: Write failing test**

```python
# forge/tui/cli_test.py
"""Tests for TUI CLI integration."""

from click.testing import CliRunner
from forge.cli.main import cli


def test_tui_command_exists():
    """The 'tui' command is registered."""
    runner = CliRunner()
    result = runner.invoke(cli, ["tui", "--help"])
    assert result.exit_code == 0
    assert "terminal" in result.output.lower() or "tui" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/tui/cli_test.py -v`

- [ ] **Step 3: Add `tui` command to CLI**

Add after the existing `run` command in `forge/cli/main.py`:

```python
@cli.command()
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality",
)
def tui(project_dir: str, strategy: str | None) -> None:
    """Launch the Forge terminal UI."""
    project_dir = os.path.abspath(project_dir)

    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        os.makedirs(forge_dir, exist_ok=True)

    from forge.config.settings import ForgeSettings
    from forge.tui.app import ForgeApp

    settings = ForgeSettings()
    if strategy:
        settings.model_strategy = strategy

    app = ForgeApp(project_dir=project_dir, settings=settings)
    app.run()
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/cli/main.py forge/tui/cli_test.py
git commit -m "feat(tui): add 'forge tui' CLI command"
```

### Task 15: Wire EventBus to TuiState in ForgeApp

**Files:**
- Modify: `forge/tui/app.py` (refine `on_mount` and `_start_pipeline`)

**Context:** The current `_start_pipeline` implementation creates the bus-to-state wiring inline. This task cleans it up into a dedicated method and ensures the wiring works end-to-end: daemon emits event → EventEmitter → EmbeddedSource → EventBus → TuiState → screen refresh.

- [ ] **Step 1: Write integration test**

```python
# forge/tui/integration_test.py
"""Integration test: event flow from EventEmitter to TuiState."""

import pytest
from forge.core.events import EventEmitter
from forge.tui.bus import EventBus, EmbeddedSource, TUI_EVENT_TYPES
from forge.tui.state import TuiState


@pytest.mark.asyncio
async def test_full_event_flow():
    """Events flow: EventEmitter → EmbeddedSource → EventBus → TuiState."""
    emitter = EventEmitter()
    bus = EventBus()
    state = TuiState()

    # Wire bus → state (same pattern as ForgeApp)
    for evt_type in TUI_EVENT_TYPES:
        async def _handler(data, _type=evt_type):
            state.apply_event(_type, data)
        bus.subscribe(evt_type, _handler)

    # Connect source
    source = EmbeddedSource(emitter, bus)
    source.connect()

    # Emit through daemon's emitter
    await emitter.emit("pipeline:phase_changed", {"phase": "planning"})
    assert state.phase == "planning"

    await emitter.emit("pipeline:plan_ready", {
        "tasks": [
            {"id": "t1", "title": "Build API", "description": "...", "files": ["api.py"], "depends_on": [], "complexity": "medium"},
        ]
    })
    assert len(state.tasks) == 1
    assert state.tasks["t1"]["title"] == "Build API"

    await emitter.emit("task:state_changed", {"task_id": "t1", "state": "in_progress"})
    assert state.tasks["t1"]["state"] == "in_progress"

    await emitter.emit("task:agent_output", {"task_id": "t1", "line": "Creating api.py..."})
    assert state.agent_output["t1"] == ["Creating api.py..."]

    await emitter.emit("pipeline:cost_update", {"total_cost_usd": 0.42})
    assert state.total_cost_usd == 0.42
```

- [ ] **Step 2: Run test to verify it passes** (should pass with existing code)

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/tui/integration_test.py -v`

- [ ] **Step 3: Commit**

```bash
git add forge/tui/integration_test.py
git commit -m "test(tui): add end-to-end event flow integration test"
```

---

## Chunk 5: ClientSource + Smart Launch

### Task 16: ClientSource (WebSocket client)

**Files:**
- Modify: `forge/tui/bus.py` (add ClientSource class)
- Create: `forge/tui/bus_client_test.py`

**Context:** ClientSource connects to a running Forge server via WebSocket at `ws://host:port/api/ws/{pipeline_id}`. It receives JSON messages and routes them to the EventBus. The message format matches what the server sends (see `forge/api/ws/handler.py` and `_bridge_events` in `tasks.py`): `{"type": "event_name", ...payload_fields}`.

- [ ] **Step 1: Write failing tests**

```python
# forge/tui/bus_client_test.py
"""Tests for ClientSource."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from forge.tui.bus import EventBus, ClientSource


@pytest.mark.asyncio
async def test_client_source_parses_messages():
    """ClientSource parses WebSocket JSON and emits to bus."""
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("task:state_changed", handler)

    source = ClientSource("ws://localhost:8000/api/ws/test-pipeline", bus, token="fake")

    # Simulate a WebSocket message
    message = {"type": "task:state_changed", "task_id": "t1", "state": "done"}
    await source._handle_message(json.dumps(message))

    assert len(received) == 1
    assert received[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_client_source_ignores_auth_ok():
    """auth_ok messages are handled internally, not forwarded."""
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("auth_ok", handler)

    source = ClientSource("ws://localhost:8000/api/ws/test", bus, token="fake")
    await source._handle_message(json.dumps({"type": "auth_ok", "user_id": "u1"}))

    assert len(received) == 0
    assert source._authenticated
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement ClientSource**

Add to `forge/tui/bus.py`:

```python
import asyncio
import json

class ClientSource:
    """Receives events from a running Forge server over WebSocket.

    Used in client mode when a Forge server is already running.
    Message format from server: {"type": "event_type", ...payload}
    """

    def __init__(self, ws_url: str, bus: EventBus, *, token: str) -> None:
        self._ws_url = ws_url
        self._bus = bus
        self._token = token
        self._connected = False
        self._authenticated = False
        self._task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Start WebSocket connection in background."""
        self._task = asyncio.create_task(self._listen())

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._task:
            self._task.cancel()
            self._task = None
        self._connected = False
        self._authenticated = False

    async def _listen(self) -> None:
        """WebSocket listen loop."""
        try:
            import websockets
            async with websockets.connect(self._ws_url) as ws:
                self._connected = True
                # Send auth token
                await ws.send(json.dumps({"token": self._token}))
                async for message in ws:
                    await self._handle_message(message)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("WebSocket error: %s", e)
            await self._bus.emit("pipeline:error", {"error": f"WebSocket disconnected: {e}"})
        finally:
            self._connected = False

    async def _handle_message(self, raw: str) -> None:
        """Parse and route a WebSocket message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from WebSocket: %s", raw[:100])
            return

        msg_type = msg.pop("type", None)
        if not msg_type:
            return

        if msg_type == "auth_ok":
            self._authenticated = True
            logger.info("WebSocket authenticated as %s", msg.get("user_id"))
            return

        await self._bus.emit(msg_type, msg)

    @property
    def connected(self) -> bool:
        return self._connected
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/bus.py forge/tui/bus_client_test.py
git commit -m "feat(tui): add ClientSource for WebSocket connection to server"
```

### Task 17: Smart launch logic

**Files:**
- Modify: `forge/tui/app.py` (add `_detect_server` and smart launch in `_start_pipeline`)

**Context:** Before starting the embedded daemon, probe `http://localhost:8000/health` with a 100ms timeout. If reachable, use ClientSource. Otherwise, use EmbeddedSource.

- [ ] **Step 1: Write tests**

```python
# forge/tui/smart_launch_test.py
"""Tests for smart launch detection."""

import pytest
from unittest.mock import AsyncMock, patch

from forge.tui.app import detect_server


@pytest.mark.asyncio
async def test_detect_server_reachable():
    """Returns True when server responds to health check."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = AsyncMock(status_code=200)
        result = await detect_server("http://localhost:8000")
        assert result is True


@pytest.mark.asyncio
async def test_detect_server_unreachable():
    """Returns False when server doesn't respond."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=Exception("Connection refused")):
        result = await detect_server("http://localhost:8000")
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Add `detect_server` to app.py**

Add to `forge/tui/app.py`:

```python
async def detect_server(base_url: str = "http://localhost:8000", timeout: float = 0.1) -> bool:
    """Probe the Forge server health endpoint."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base_url}/health")
            return resp.status_code == 200
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**
- [ ] **Step 5: Commit**

```bash
git add forge/tui/app.py forge/tui/smart_launch_test.py
git commit -m "feat(tui): add smart server detection for client/embedded mode"
```

---

## Chunk 6: Screenshot Automation + README

### Task 18: Screenshot export

**Files:**
- Modify: `forge/tui/app.py` (refine `action_screenshot`)

**Context:** The `S` key exports the current TUI state as an SVG file using Textual's built-in `save_screenshot()`. Auto-capture at key pipeline moments for README use.

- [ ] **Step 1: Add auto-screenshot triggers to ForgeApp**

In `forge/tui/app.py`, modify `_on_state_change`:

```python
def _on_state_change(self, field: str) -> None:
    """Refresh current screen and auto-capture screenshots."""
    try:
        self.screen.refresh()
    except Exception:
        pass

    # Auto-capture at key moments
    if field == "phase":
        phase = self._state.phase
        if phase in ("planning", "executing", "complete"):
            self._auto_screenshot(phase)

def _auto_screenshot(self, label: str) -> None:
    """Automatically save a screenshot for README."""
    path = os.path.join(self._project_dir, "screenshots")
    os.makedirs(path, exist_ok=True)
    filename = os.path.join(path, f"forge-{label}.svg")
    try:
        self.save_screenshot(filename)
        logger.info("Auto-screenshot: %s", filename)
    except Exception:
        logger.debug("Auto-screenshot failed for %s", label)
```

- [ ] **Step 2: Commit**

```bash
git add forge/tui/app.py
git commit -m "feat(tui): add auto-screenshot at pipeline milestones"
```

### Task 19: Full test suite pass

**Files:**
- All test files created in this plan

- [ ] **Step 1: Run all TUI tests**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/tui/ -v --tb=short`

- [ ] **Step 2: Fix any failures**

- [ ] **Step 3: Run existing tests to ensure no regressions**

Run: `cd /Users/mtarun/Desktop/SideHustles/claude-does/.claude/worktrees/crazy-ramanujan && python -m pytest forge/ -v --tb=short --ignore=forge/tui/ -x`

- [ ] **Step 4: Commit any fixes**

```bash
git commit -am "fix(tui): test suite fixes"
```

---

## Verification Plan

1. **Unit tests**: `pytest forge/tui/ -v` — all new tests pass
2. **Integration test**: `forge/tui/integration_test.py` — full event flow works
3. **No regressions**: `pytest forge/ -v` — existing tests still pass
4. **CLI command**: `forge tui --help` — shows help text
5. **Manual smoke test**: `forge tui` in a project directory — shows HomeScreen with logo
6. **Screenshot**: Press `S` in TUI — creates SVG file

## Critical Files Summary

| File | Purpose |
|------|---------|
| `forge/tui/__init__.py` | Package init |
| `forge/tui/bus.py` | EventBus, EmbeddedSource, ClientSource |
| `forge/tui/state.py` | TuiState reactive container |
| `forge/tui/app.py` | ForgeApp, smart launch, screen management |
| `forge/tui/screens/home.py` | HomeScreen: logo + prompt |
| `forge/tui/screens/pipeline.py` | PipelineScreen: split-pane |
| `forge/tui/screens/review.py` | ReviewScreen: diff viewer |
| `forge/tui/screens/settings.py` | SettingsScreen: config display |
| `forge/tui/widgets/logo.py` | Forge flame logo |
| `forge/tui/widgets/task_list.py` | Task list with status icons |
| `forge/tui/widgets/agent_output.py` | Streaming agent output |
| `forge/tui/widgets/progress_bar.py` | Progress bar + cost |
| `forge/tui/widgets/dag.py` | DAG overlay |
| `forge/tui/widgets/diff_viewer.py` | Diff viewer |
| `forge/cli/main.py` | CLI entry point (modified) |
