# Forge Terminal TUI — Design Spec

## Goal

Replace the web UI with a full terminal TUI using Textual. Users run `forge` and get a rich, interactive terminal experience — no browser needed. Smart default: connect to running server if available, else launch embedded daemon.

## Architecture

### Event Bus Pattern

```
EmbeddedSource (in-process daemon)
        \
         → EventBus → TuiState (Zustand-like) → Textual Widgets
        /
ClientSource (WebSocket to server)
```

- **EventBus** (`forge/tui/bus.py`): Routes events from either source to subscribers. Single interface, two backends.
- **EmbeddedSource**: Imports daemon directly, subscribes to `_emit()` in-process. Used when no server is running.
- **ClientSource**: Connects via WebSocket to running Forge server. Used when server detected on default port.
- **TuiState** (`forge/tui/state.py`): Reactive state container. Widgets subscribe to slices. Events update state; state changes trigger widget redraws.

### Smart Launch

1. Probe `localhost:8000/health` (100ms timeout)
2. If reachable → `ClientSource` (attach to server)
3. If not → `EmbeddedSource` (start daemon in-process with in-memory or file SQLite)

### File Structure

```
forge/tui/
  __init__.py
  app.py          # ForgeApp(textual.App) — top-level app, screen management
  state.py        # TuiState — reactive state container
  bus.py          # EventBus, EmbeddedSource, ClientSource
  screens/
    __init__.py
    home.py       # HomeScreen — new pipeline or select existing
    pipeline.py   # PipelineScreen — main split-pane view
    review.py     # ReviewScreen — diff viewer + approve/reject
    settings.py   # SettingsScreen — config editor
  widgets/
    __init__.py
    logo.py       # Forge flame logo
    task_list.py  # Task list panel (left pane)
    agent_output.py # Agent output panel (right pane)
    progress.py   # Pipeline progress bar
    dag.py        # DAG overlay (toggleable with 'g')
    diff.py       # Inline diff viewer
    cost.py       # Cost tracker widget
```

## Screens & Navigation

### Global Keybindings

| Key | Action |
|-----|--------|
| `1-4` | Switch screens (Home/Pipeline/Review/Settings) |
| `q` | Quit (with confirmation if pipeline active) |
| `?` | Help overlay |
| `S` | Screenshot (SVG export via Rich) |

### Home Screen

Forge flame logo + "F O R G E" + subtitle. Input field for prompt. Recent pipelines list below. `Enter` to start, `j/k` to navigate history.

### Pipeline Screen (Split-Pane)

Left pane: task list with status icons. Right pane: selected agent's streaming output. Bottom: progress bar + cost tracker.

| Key | Action |
|-----|--------|
| `j/k` | Navigate task list |
| `Enter` | Focus agent output for selected task |
| `g` | Toggle DAG overlay |
| `Tab` | Cycle between active agent outputs |
| `/` | Filter tasks |

### Review Screen

Inline diff viewer (Rich syntax highlighting). Approve/reject per task.

| Key | Action |
|-----|--------|
| `a` | Approve task |
| `x` | Reject task |
| `j/k` | Navigate hunks |
| `e` | Open in `$EDITOR` |

### Settings Screen

Current config display. Edit via `$EDITOR` on `Enter`.

## Logo

Minimal flame icon + spaced "F O R G E" text:

```
  ╭╮
 ╔██╗╮   F O R G E
 ╔████╗
  ╔█╗    multi-agent orchestration
   ╗
```

## Error Handling

### Connection Failures

- **Embedded mode**: Daemon crash → error panel with traceback summary + "Press r to restart"
- **Client mode**: Server unreachable → 3 retries with backoff → offer "Switch to embedded mode? [y/n]"
- **WebSocket disconnect**: Auto-reconnect, re-sync state from DB

### Resource Limits

- Terminal < 80 cols → single-pane mode (agent output only), status bar hint
- Terminal < 24 rows → hide progress bar and header
- Long agent output → ring buffer (1000 lines per agent), scrollable

### Pipeline Edge Cases

- No tasks yet → "Waiting for plan..." with spinner
- All tasks error → summary panel with per-task errors
- Approval required → flash status bar + bell, highlight task in yellow

### Graceful Exit

- `q` during pipeline → confirm: "Pipeline running. Quit? Continues in background. [y/n]"
- `Ctrl+C` → same as `q`
- `Ctrl+C` twice → force quit

## Screenshot Automation

- `S` key → `rich.console.export_svg()` → save to `screenshots/` directory
- Auto-capture at: pipeline start, first task complete, pipeline done
- Used for README documentation

## Testing Strategy

### Unit Tests (pytest, co-located `_test.py`)

- `bus.py` → event routing, source switching, subscription cleanup
- `state.py` → state transitions, event-to-state mapping
- `screens/*.py` → data formatting, keybinding dispatch via Textual pilot API

### Integration Tests

- `EmbeddedSource` + in-memory SQLite → 3-task pipeline → verify TUI state reflects all transitions
- Client/embedded mode switching

### Snapshot Tests (Textual SVG snapshots)

- Each screen at key states: empty, running, error, done
- Regression detection on layout changes

## Non-Goals

- No mouse support (keyboard-only for speed)
- No theming system (single dark theme matching terminal conventions)
- No plugin system for widgets
- No web UI changes (TUI is a full replacement, web UI stays as-is for now)
