# TUI Overhaul — Fix Everything Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every broken feature, wire up the DB, add plan approval flow, and polish the UI so the TUI is a fully functional replacement for the web UI.

**Architecture:** The TUI's `ForgeApp` currently calls `daemon.run()` which blasts through plan+execute in one shot with no DB access. The fix: ForgeApp opens `.forge/forge.db` directly, calls `daemon.plan()` → shows plan for approval → calls `daemon.execute()` separately. HomeScreen queries the DB for recent pipelines. All phase transitions and task states are visible in real-time via the existing EventBus→TuiState pipeline (PR #75 fixes the ID mismatch).

**Tech Stack:** Python 3.12, Textual ≥0.50, SQLAlchemy (async), Forge daemon/DB

**Spec:** `docs/superpowers/specs/2026-03-10-terminal-tui-design.md`

**Prerequisite:** PR #75 must be merged first (fixes task ID mismatch, stale selection, call_from_thread).

---

## File Structure

### Modified files:
- `forge/tui/app.py` — Rewrite `_start_pipeline` to split plan/approve/execute; add DB init; add help overlay binding
- `forge/tui/state.py` — Add `plan_tasks` field for pre-execution plan display; add `recent_pipelines` field
- `forge/tui/screens/home.py` — Load recent pipelines from DB; make them selectable
- `forge/tui/screens/pipeline.py` — Add plan approval UI (show plan, approve button); add planning spinner; improve CSS
- `forge/tui/widgets/task_list.py` — Colored state icons; highlight bar instead of grey ►
- `forge/tui/widgets/agent_output.py` — Spinner for "Waiting for output..."; streaming indicator
- `forge/tui/widgets/progress_bar.py` — Phase-aware display (planning/executing/complete)
- `forge/tui/widgets/logo.py` — Simpler ASCII art that renders everywhere
- `forge/cli/main.py` — No changes needed (DB path is derived from project_dir)

### New files:
- `forge/tui/screens/plan_approval.py` — Dedicated screen showing task plan with approve/edit/reject

### Test files (co-located):
- `forge/tui/screens/plan_approval_test.py`
- Update existing test files for changed behavior

---

## Chunk 1: DB Wiring + Pipeline History

### Task 1: Wire DB into ForgeApp

**Files:**
- Modify: `forge/tui/app.py`

**Context:** Currently ForgeApp has zero DB access. The daemon's `run()` creates its own DB at `.forge/forge.db`. We need ForgeApp to open the same DB so it can query history and manage the plan/approve/execute lifecycle.

- [ ] **Step 1: Write failing test for DB initialization**

Create `forge/tui/app_db_test.py`:
```python
"""Tests for ForgeApp DB integration."""
import os
import pytest
from unittest.mock import AsyncMock, patch

@pytest.fixture
def tmp_project(tmp_path):
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    return str(tmp_path)

@pytest.mark.asyncio
async def test_app_creates_db_on_mount(tmp_project):
    """ForgeApp should initialize a DB connection on mount."""
    from forge.tui.app import ForgeApp
    app = ForgeApp(project_dir=tmp_project)
    # After _init_db, app._db should be set
    await app._init_db()
    assert app._db is not None
    await app._db.close()

@pytest.mark.asyncio
async def test_app_db_path(tmp_project):
    """DB path should be .forge/forge.db inside project dir."""
    from forge.tui.app import ForgeApp
    app = ForgeApp(project_dir=tmp_project)
    expected = os.path.join(tmp_project, ".forge", "forge.db")
    assert app._db_path == expected
```

- [ ] **Step 2: Run test — expect FAIL (no _init_db or _db_path)**

Run: `python3 -m pytest forge/tui/app_db_test.py -v`

- [ ] **Step 3: Implement DB wiring in ForgeApp**

In `forge/tui/app.py`, add to `__init__`:
```python
self._db_path = os.path.join(self._project_dir, ".forge", "forge.db")
self._db = None
```

Add method:
```python
async def _init_db(self):
    """Initialize database connection."""
    from forge.storage.db import Database
    os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
    self._db = Database(f"sqlite+aiosqlite:///{self._db_path}")
    await self._db.initialize()
```

Change `on_mount` to async and call `_init_db`:
```python
async def on_mount(self) -> None:
    """Initialize DB, push home screen, wire state changes."""
    await self._init_db()
    recent = await self._load_recent_pipelines()
    self.push_screen(HomeScreen(recent_pipelines=recent))
    self._state.on_change(self._on_state_change)
```

Add history loader:
```python
async def _load_recent_pipelines(self) -> list[dict]:
    """Load recent pipelines from DB for HomeScreen."""
    if not self._db:
        return []
    try:
        pipelines = await self._db.list_pipelines()
        return [
            {
                "id": p.id,
                "description": p.description or "",
                "status": p.status or "unknown",
                "created_at": p.created_at or "",
                "cost": p.total_cost_usd or 0.0,
            }
            for p in pipelines[:10]  # Last 10
        ]
    except Exception:
        logger.debug("Failed to load pipeline history", exc_info=True)
        return []
```

- [ ] **Step 4: Run test — expect PASS**

Run: `python3 -m pytest forge/tui/app_db_test.py -v`

- [ ] **Step 5: Commit**

```
feat(tui): wire DB into ForgeApp for history and lifecycle management
```

---

### Task 2: Show Recent Pipelines on HomeScreen

**Files:**
- Modify: `forge/tui/screens/home.py`
- Modify: `forge/tui/screens/home_test.py`

**Context:** HomeScreen currently shows hardcoded "No recent pipelines". It should accept pipeline data and render a list.

- [ ] **Step 1: Write failing test**

In `forge/tui/screens/home_test.py`, add:
```python
def test_format_recent_pipelines():
    """Recent pipelines should format with status + description."""
    from forge.tui.screens.home import format_recent_pipelines
    pipelines = [
        {"id": "abc", "description": "Build auth system", "status": "complete", "created_at": "2026-03-10", "cost": 2.50},
        {"id": "def", "description": "Fix login bug", "status": "error", "created_at": "2026-03-09", "cost": 0.80},
    ]
    result = format_recent_pipelines(pipelines)
    assert "Build auth system" in result
    assert "Fix login bug" in result
    assert "✔" in result   # complete icon
    assert "✖" in result   # error icon

def test_format_recent_pipelines_empty():
    from forge.tui.screens.home import format_recent_pipelines
    result = format_recent_pipelines([])
    assert "No recent pipelines" in result
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement format_recent_pipelines and update HomeScreen**

In `forge/tui/screens/home.py`:

Add pipeline status formatting:
```python
_PIPELINE_STATUS_ICONS = {
    "complete": ("✔", "#3fb950"),
    "executing": ("●", "#f0883e"),
    "planned": ("◉", "#a371f7"),
    "planning": ("◌", "#58a6ff"),
    "error": ("✖", "#f85149"),
}

def format_recent_pipelines(pipelines: list[dict]) -> str:
    if not pipelines:
        return "[#8b949e]No recent pipelines[/]"
    lines = []
    for p in pipelines:
        status = p.get("status", "unknown")
        icon, color = _PIPELINE_STATUS_ICONS.get(status, ("?", "#8b949e"))
        desc = p.get("description", "Untitled")[:50]
        cost = p.get("cost", 0.0)
        date = p.get("created_at", "")[:10]
        lines.append(f"  [{color}]{icon}[/] {desc}  [#8b949e]{date} · ${cost:.2f}[/]")
    return "\n".join(lines)
```

Update HomeScreen.__init__ to accept pipelines:
```python
def __init__(self, recent_pipelines: list[dict] | None = None) -> None:
    super().__init__()
    self._recent_pipelines = recent_pipelines or []
```

Update compose to use formatted data:
```python
def compose(self) -> ComposeResult:
    with Center():
        with Vertical(id="home-container"):
            yield ForgeLogo()
            yield Input(placeholder="What should I build?", id="prompt-input")
            yield Static("Recent pipelines", id="recent-label")
            yield Static(
                format_recent_pipelines(self._recent_pipelines),
                id="recent-list",
            )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest forge/tui/screens/home_test.py -v`

- [ ] **Step 5: Commit**

```
feat(tui): show recent pipeline history on HomeScreen from DB
```

---

## Chunk 2: Plan Approval Flow

### Task 3: Create PlanApprovalScreen

**Files:**
- Create: `forge/tui/screens/plan_approval.py`
- Create: `forge/tui/screens/plan_approval_test.py`

**Context:** After planning completes, the TUI should show the plan for user review before executing. The plan shows task titles, descriptions, file targets, complexity, estimated cost. User presses Enter to approve or Escape to cancel.

- [ ] **Step 1: Write failing tests**

Create `forge/tui/screens/plan_approval_test.py`:
```python
"""Tests for PlanApprovalScreen."""
from forge.tui.screens.plan_approval import format_plan_task, format_plan_summary

def test_format_plan_task():
    task = {
        "id": "task-1",
        "title": "Add user auth",
        "description": "Implement JWT-based authentication",
        "files": ["auth.py", "middleware.py"],
        "complexity": "high",
        "depends_on": [],
    }
    result = format_plan_task(task, index=1)
    assert "Add user auth" in result
    assert "auth.py" in result
    assert "high" in result

def test_format_plan_summary():
    tasks = [
        {"id": "t1", "title": "A", "complexity": "low"},
        {"id": "t2", "title": "B", "complexity": "high"},
        {"id": "t3", "title": "C", "complexity": "medium"},
    ]
    result = format_plan_summary(tasks, estimated_cost=4.50)
    assert "3 tasks" in result
    assert "$4.50" in result
```

- [ ] **Step 2: Run test — expect FAIL**

- [ ] **Step 3: Implement PlanApprovalScreen**

Create `forge/tui/screens/plan_approval.py`:
```python
"""Plan approval screen — shows planned tasks for user review before execution."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static
from textual.containers import Vertical, VerticalScroll
from textual.binding import Binding
from textual.message import Message


_COMPLEXITY_COLORS = {
    "low": "#3fb950",
    "medium": "#d29922",
    "high": "#f85149",
}


def format_plan_task(task: dict, index: int) -> str:
    title = task.get("title", "Untitled")
    desc = task.get("description", "")
    files = task.get("files", [])
    complexity = task.get("complexity", "medium")
    deps = task.get("depends_on", [])
    color = _COMPLEXITY_COLORS.get(complexity, "#8b949e")

    lines = [f"  [bold #58a6ff]{index}. {title}[/]  [{color}]{complexity}[/]"]
    if desc:
        # Wrap description to ~70 chars
        lines.append(f"     [#8b949e]{desc[:120]}[/]")
    if files:
        file_str = ", ".join(files[:5])
        if len(files) > 5:
            file_str += f" +{len(files) - 5} more"
        lines.append(f"     [#8b949e]Files:[/] {file_str}")
    if deps:
        lines.append(f"     [#8b949e]Depends on:[/] {', '.join(deps)}")
    return "\n".join(lines)


def format_plan_summary(tasks: list[dict], estimated_cost: float = 0.0) -> str:
    count = len(tasks)
    complexities = {"low": 0, "medium": 0, "high": 0}
    for t in tasks:
        c = t.get("complexity", "medium")
        complexities[c] = complexities.get(c, 0) + 1

    parts = [f"[bold]{count} tasks[/]"]
    for level, n in complexities.items():
        if n > 0:
            color = _COMPLEXITY_COLORS.get(level, "#8b949e")
            parts.append(f"[{color}]{n} {level}[/]")
    if estimated_cost > 0:
        parts.append(f"[#3fb950]~${estimated_cost:.2f}[/]")
    return " · ".join(parts)


class PlanApprovalScreen(Screen):
    """Shows the planned tasks for user approval before execution."""

    DEFAULT_CSS = """
    PlanApprovalScreen {
        layout: vertical;
    }
    #plan-header {
        height: 3;
        padding: 1 2;
        background: #161b22;
        color: #58a6ff;
    }
    #plan-body {
        padding: 1 2;
    }
    #plan-footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: #161b22;
        color: #8b949e;
    }
    """

    BINDINGS = [
        Binding("enter", "approve", "Approve & Execute", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    class PlanApproved(Message):
        """User approved the plan."""

    class PlanCancelled(Message):
        """User cancelled the plan."""

    def __init__(self, tasks: list[dict], estimated_cost: float = 0.0) -> None:
        super().__init__()
        self._tasks = tasks
        self._estimated_cost = estimated_cost

    def compose(self) -> ComposeResult:
        summary = format_plan_summary(self._tasks, self._estimated_cost)
        yield Static(f"[bold #58a6ff]PLAN REVIEW[/]  {summary}", id="plan-header")
        with VerticalScroll(id="plan-body"):
            for i, task in enumerate(self._tasks, 1):
                yield Static(format_plan_task(task, i))
                yield Static("")  # spacer
        yield Static("[Enter] approve & execute  [Esc] cancel", id="plan-footer")

    def action_approve(self) -> None:
        self.post_message(self.PlanApproved())

    def action_cancel(self) -> None:
        self.post_message(self.PlanCancelled())
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `python3 -m pytest forge/tui/screens/plan_approval_test.py -v`

- [ ] **Step 5: Commit**

```
feat(tui): add PlanApprovalScreen for reviewing plan before execution
```

---

### Task 4: Rewrite _start_pipeline to split plan/approve/execute

**Files:**
- Modify: `forge/tui/app.py`

**Context:** This is the core change. Instead of calling `daemon.run()`, we call `daemon.plan()` → show PlanApprovalScreen → on approval, call `daemon.execute()`. The daemon needs a DB and pipeline_id to be passed in (same as how the web API does it).

- [ ] **Step 1: Rewrite pipeline flow — replace `_start_pipeline` and `_run_daemon`**

**IMPORTANT:** Remove the existing `_start_pipeline` and `_run_daemon` methods entirely. They are replaced by `_run_plan`, `on_plan_approval_screen_plan_approved`, `on_plan_approval_screen_plan_cancelled`, and `_run_execute`.

Add to `__init__` (alongside existing attributes):
```python
self._graph = None
self._pipeline_id = None
```

Add `PlanApprovalScreen` to the top-level imports (alongside the other screen imports):
```python
from forge.tui.screens.plan_approval import PlanApprovalScreen
```

Replace the entire pipeline flow in `app.py`:

```python
async def on_home_screen_task_submitted(self, event: HomeScreen.TaskSubmitted) -> None:
    """User submitted a task from HomeScreen."""
    task = event.task
    logger.info("Task submitted: %s", task)
    # Show pipeline screen immediately with "planning" phase
    self._state.phase = "planning"
    self._state._notify("phase")
    pipeline_screen = PipelineScreen(self._state)
    self.push_screen(pipeline_screen)
    # CRITICAL: Use create_task, NOT await — planning is a long LLM call
    # that would block the Textual event loop and freeze the UI.
    asyncio.create_task(self._run_plan(task))

async def _run_plan(self, task: str) -> None:
    """Run planning phase only, then show plan for approval."""
    import uuid
    from forge.core.events import EventEmitter
    from forge.core.daemon import ForgeDaemon
    from forge.config.settings import ForgeSettings

    settings = self._settings or ForgeSettings()
    emitter = EventEmitter()
    self._bus = EventBus()
    self._source = EmbeddedSource(emitter, self._bus)
    self._source.connect()

    # Wire bus events to state
    for evt_type in TUI_EVENT_TYPES:
        async def _handler(data, _type=evt_type):
            self._state.apply_event(_type, data)
        self._bus.subscribe(evt_type, _handler)

    self._daemon = ForgeDaemon(
        self._project_dir,
        settings=settings,
        event_emitter=emitter,
    )

    # Create pipeline in DB (same as web API does)
    self._pipeline_id = str(uuid.uuid4())
    await self._db.create_pipeline(
        id=self._pipeline_id,
        description=task[:200],
        project_dir=self._project_dir,
        model_strategy=settings.model_strategy,
        budget_limit_usd=settings.budget_limit_usd,
    )

    self._pipeline_start_time = asyncio.get_event_loop().time()
    self._elapsed_timer = self.set_interval(1.0, self._tick_elapsed)

    # Run planning only
    try:
        self._graph = await self._daemon.plan(
            task, self._db, pipeline_id=self._pipeline_id,
        )
        # Show plan approval screen
        plan_tasks = [
            {"id": t.id, "title": t.title, "description": t.description,
             "files": t.files, "depends_on": t.depends_on,
             "complexity": t.complexity.value}
            for t in self._graph.tasks
        ]
        self.push_screen(PlanApprovalScreen(plan_tasks))
    except Exception as e:
        logger.error("Planning failed: %s", e, exc_info=True)
        self._state.apply_event("pipeline:error", {"error": str(e)})

async def on_plan_approval_screen_plan_approved(self, event) -> None:
    """User approved the plan — start execution."""
    self.pop_screen()  # Remove PlanApprovalScreen, back to PipelineScreen
    # Generate contracts then execute
    try:
        self._daemon._contracts = await self._daemon.generate_contracts(
            self._graph, self._db, self._pipeline_id,
        )
        self._daemon_task = asyncio.create_task(self._run_execute())
        self._daemon_task.add_done_callback(self._on_daemon_done)
    except Exception as e:
        logger.error("Contract generation failed: %s", e, exc_info=True)
        self._state.apply_event("pipeline:error", {"error": str(e)})

async def on_plan_approval_screen_plan_cancelled(self, event) -> None:
    """User cancelled the plan — clean up resources."""
    self.pop_screen()  # Remove PlanApprovalScreen
    if self._elapsed_timer:
        self._elapsed_timer.stop()
    # Clean up event bridge and mark pipeline as cancelled in DB
    if self._source:
        self._source.disconnect()
    if self._db and self._pipeline_id:
        try:
            await self._db.update_pipeline_status(self._pipeline_id, "cancelled")
        except Exception:
            logger.debug("Failed to update cancelled pipeline status", exc_info=True)
    self._daemon = None
    self._graph = None
    self.notify("Plan cancelled.", severity="warning")

async def _run_execute(self) -> None:
    """Execute the approved plan."""
    try:
        await self._daemon.execute(
            self._graph, self._db, pipeline_id=self._pipeline_id,
        )
    except Exception as e:
        logger.error("Execution failed: %s", e, exc_info=True)
        self._state.apply_event("pipeline:error", {"error": str(e)})
```

- [ ] **Step 2: Run full TUI test suite**

Run: `python3 -m pytest forge/tui/ -v`
Fix any tests broken by the refactor.

- [ ] **Step 3: Commit**

```
feat(tui): split pipeline into plan → approve → execute lifecycle
```

---

## Chunk 3: UI Polish — Colors, Loading, Indicators

### Task 5: Fix task list — colored icons + highlight bar

**Files:**
- Modify: `forge/tui/widgets/task_list.py`
- Update: `forge/tui/widgets/task_list_test.py`

**Context:** User complained about "grey pointers" and "all grey everywhere". The task list needs:
- Colored state icons (orange for in_progress, green for done, red for error)
- Highlight bar (full-width color band) instead of grey ► arrow

- [ ] **Step 1: Update format_task_line for colored indicators**

```python
def format_task_line(task: dict, *, selected: bool) -> str:
    state = task.get("state", "todo")
    icon = STATE_ICONS.get(state, "?")
    color = STATE_COLORS.get(state, "#8b949e")
    title = task.get("title", "Untitled")
    if selected:
        return f"[bold on #1f2937] [{color}]{icon}[/] {title} [/]"
    else:
        return f" [{color}]{icon}[/] [#c9d1d9]{title}[/]"
```

The `[bold on #1f2937]` creates a subtle dark highlight bar. No more grey ►.

- [ ] **Step 2: Update tests for new format**

- [ ] **Step 3: Commit**

```
fix(tui): colored task icons + highlight bar instead of grey arrows
```

---

### Task 6: Add spinner + streaming indicator to AgentOutput

**Files:**
- Modify: `forge/tui/widgets/agent_output.py`

**Context:** "Waiting for output..." is dead static text. Should show a spinner while waiting and a streaming dot while receiving output.

- [ ] **Step 1: Update format_output for spinner**

```python
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

def format_output(lines: list[str], spinner_frame: int = 0) -> str:
    if not lines:
        frame = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
        return f"[#58a6ff]{frame}[/] [#8b949e]Waiting for output...[/]"
    return "\n".join(lines)
```

Add a timer in AgentOutput widget to animate the spinner:
```python
def on_mount(self) -> None:
    self._spinner_frame = 0
    self.set_interval(0.1, self._tick_spinner)

def _tick_spinner(self) -> None:
    if not self._lines:
        self._spinner_frame += 1
        self.refresh()
```

- [ ] **Step 2: Update tests**

- [ ] **Step 3: Commit**

```
fix(tui): add animated spinner for waiting state in agent output
```

---

### Task 7: Phase-aware progress bar

**Files:**
- Modify: `forge/tui/widgets/progress_bar.py`

**Context:** Progress bar shows "idle" when planning, needs to show phase context.

- [ ] **Step 1: Update format_progress for phase awareness**

```python
def format_progress(done: int, total: int, cost_usd: float, elapsed_seconds: float, phase: str, *, bar_width: int = 30) -> str:
    minutes = int(elapsed_seconds) // 60
    seconds = int(elapsed_seconds) % 60
    time_str = f"{minutes}:{seconds:02d}"

    if phase == "planning":
        return f"[#58a6ff]◌ Planning...[/] │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
    if phase == "planned":
        return f"[#a371f7]◉ Plan ready — review required[/] │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
    if phase == "complete":
        return f"[#3fb950]✔ Complete[/] │ {done}/{total} tasks │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
    if phase == "error":
        return f"[#f85149]✖ Error[/] │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
    if total == 0:
        return f"[#8b949e]{phase}[/] │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"

    pct = done / total
    filled = int(pct * bar_width)
    empty = bar_width - filled
    bar = f"[#3fb950]{'█' * filled}[/][#21262d]{'░' * empty}[/]"
    return f"{bar} {pct:.0%} │ {done}/{total} tasks │ [#3fb950]${cost_usd:.2f}[/] │ {time_str}"
```

- [ ] **Step 2: Update tests**

- [ ] **Step 3: Commit**

```
fix(tui): phase-aware progress bar with planning/complete states
```

---

### Task 8: Improve PipelineScreen CSS + panel borders

**Files:**
- Modify: `forge/tui/screens/pipeline.py`

**Context:** No visual separation between panels. Add borders and proper layout.

- [ ] **Step 1: Update PipelineScreen CSS**

```python
DEFAULT_CSS = """
PipelineScreen {
    layout: vertical;
}
#split-pane {
    height: 1fr;
}
TaskList {
    width: 1fr;
    min-width: 30;
    max-width: 45;
    border-right: tall #30363d;
    padding: 1 1;
}
AgentOutput {
    width: 3fr;
    padding: 1 1;
    border-left: tall #30363d;
}
PipelineProgress {
    dock: bottom;
    height: 1;
    padding: 0 1;
    background: #161b22;
    border-top: tall #30363d;
}
"""
```

Move the CSS from individual widget files to the screen so it's cohesive.

- [ ] **Step 2: Commit**

```
fix(tui): add panel borders and improve pipeline layout
```

---

### Task 9: Fix logo rendering

**Files:**
- Modify: `forge/tui/widgets/logo.py`

**Context:** Unicode box-drawing chars render poorly in many terminals. Use simpler ASCII art.

- [ ] **Step 1: Replace logo with terminal-safe version**

```python
FORGE_LOGO = """\
[#f0883e]    ╱╲
   ╱  ╲
  ╱ ▲▲ ╲[/]   [bold #58a6ff]F O R G E[/]
[#f0883e]  ╲ ▼▼ ╱[/]
[#f0883e]   ╲  ╱[/]   [#8b949e]multi-agent orchestration[/]
[#f0883e]    ╲╱[/]\
"""
```

- [ ] **Step 2: Commit**

```
fix(tui): simpler logo that renders correctly across terminals
```

---

### Task 10: Add error panel to PipelineScreen

**Files:**
- Modify: `forge/tui/screens/pipeline.py`
- Modify: `forge/tui/state.py`

**Context:** When pipeline errors, nothing shows. Add an error notification panel.

- [ ] **Step 1: Handle error state in PipelineScreen._on_state_change**

Add to `_on_state_change`:
```python
if field == "error":
    error = self._state.error
    if error:
        self.app.notify(f"Pipeline error: {error}", severity="error", timeout=10)
```

- [ ] **Step 2: Commit**

```
fix(tui): show error notification when pipeline fails
```

---

## Chunk 4: Final Integration + Test Suite

### Task 11: Run full test suite and fix broken tests

**Files:**
- All test files in `forge/tui/`

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest forge/tui/ -v --tb=short`

- [ ] **Step 2: Fix any failures from Tasks 1-10**

- [ ] **Step 3: Verify all tests pass**

Run: `python3 -m pytest forge/tui/ -v`
Expected: ALL PASS

- [ ] **Step 4: Commit any test fixes**

```
test(tui): fix tests after overhaul
```

---

### Task 12: Manual verification checklist

- [ ] **Step 1: Verify app launches without crash**

Run: `cd <project-dir> && python3 -m forge.cli.main tui`
Expected: HomeScreen renders with logo, input, recent pipelines (or empty if no history)

- [ ] **Step 2: Verify all screens accessible**

Press 1-4 to switch screens. Each should render without error.

- [ ] **Step 3: Verify the full event flow in test**

Run the integration test: `python3 -m pytest forge/tui/integration_test.py -v`
This tests EventBus → TuiState → phase/task/output flow.

- [ ] **Step 4: Final commit and PR**

```
git push && gh pr create
```
