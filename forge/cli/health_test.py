"""Tests for forge health CLI command — pure functions and registration."""

from __future__ import annotations

from rich.table import Table

from forge.cli.health import (
    _fmt_cost,
    _fmt_tokens,
    build_context_panel,
    build_cost_table,
    build_health_dag,
    build_scheduler_panel,
)

# ── build_health_dag tests ───────────────────────────────────────────


def test_build_health_dag_empty():
    """Empty task list returns styled 'No tasks'."""
    result = build_health_dag([])
    assert result == "[#8b949e]No tasks[/]"


def test_build_health_dag_single_task():
    """Single task with no deps shows bullet and title."""
    tasks = [{"id": "task-1", "title": "Build auth", "state": "done", "depends_on": []}]
    result = build_health_dag(tasks)
    assert "task-1" in result
    assert "Build auth" in result
    assert "\u25cf" in result  # bullet
    # done color
    assert "#3fb950" in result


def test_build_health_dag_with_dependencies():
    """Tasks with dependencies show arrow notation."""
    tasks = [
        {"id": "task-1", "title": "Setup DB", "state": "done", "depends_on": []},
        {"id": "task-2", "title": "Add models", "state": "in_progress", "depends_on": ["task-1"]},
    ]
    result = build_health_dag(tasks)
    assert "\u2190" in result  # ← arrow
    assert "task-1" in result
    assert "task-2" in result
    # in_progress color
    assert "#f0883e" in result


def test_build_health_dag_all_states():
    """All task states get their correct colors."""
    states = {
        "todo": "#8b949e",
        "in_progress": "#f0883e",
        "in_review": "#a371f7",
        "awaiting_approval": "#d29922",
        "merging": "#79c0ff",
        "done": "#3fb950",
        "cancelled": "#8b949e",
        "error": "#f85149",
    }
    for state, color in states.items():
        tasks = [{"id": f"t-{state}", "title": "Test", "state": state, "depends_on": []}]
        result = build_health_dag(tasks)
        assert color in result, f"Missing color {color} for state {state}"


def test_build_health_dag_long_title_truncated():
    """Titles longer than 30 chars are truncated with ellipsis."""
    tasks = [
        {
            "id": "task-1",
            "title": "A very long task title that exceeds thirty characters",
            "state": "todo",
            "depends_on": [],
        }
    ]
    result = build_health_dag(tasks)
    assert "\u2026" in result  # ellipsis


def test_build_health_dag_dep_not_in_task_list():
    """Dependencies pointing to non-existent tasks are omitted."""
    tasks = [
        {"id": "task-2", "title": "Child", "state": "todo", "depends_on": ["task-999"]},
    ]
    result = build_health_dag(tasks)
    # Should not show arrow since dep is not in task_map
    assert "\u2190" not in result


def test_build_health_dag_unknown_state():
    """Unknown states fall back to default color."""
    tasks = [{"id": "task-1", "title": "Test", "state": "unknown_state", "depends_on": []}]
    result = build_health_dag(tasks)
    # Should use fallback #8b949e
    assert "#8b949e" in result


# ── build_cost_table tests ───────────────────────────────────────────


def test_build_cost_table_returns_rich_table():
    """Returns a Rich Table instance."""
    tasks = [
        {
            "id": "task-1",
            "assigned_agent": "agent-1",
            "input_tokens": 5000,
            "output_tokens": 2000,
            "cost_usd": 0.25,
            "model_history": [{"model": "claude:sonnet"}],
        }
    ]
    pipeline = {"total_cost_usd": 0.25, "total_input_tokens": 5000, "total_output_tokens": 2000}
    result = build_cost_table(tasks, pipeline)
    assert isinstance(result, Table)


def test_build_cost_table_empty_tasks():
    """Empty tasks still produces a table with footer row."""
    pipeline = {"total_cost_usd": 0.0, "total_input_tokens": 0, "total_output_tokens": 0}
    result = build_cost_table([], pipeline)
    assert isinstance(result, Table)
    # Should have at least footer row
    assert result.row_count == 1


def test_build_cost_table_multiple_tasks():
    """Multiple tasks produce correct number of rows."""
    tasks = [
        {
            "id": "task-1",
            "assigned_agent": "agent-1",
            "input_tokens": 3000,
            "output_tokens": 1000,
            "cost_usd": 0.15,
            "model_history": [],
        },
        {
            "id": "task-2",
            "assigned_agent": "agent-2",
            "input_tokens": 7000,
            "output_tokens": 3000,
            "cost_usd": 0.35,
            "model_history": [],
        },
    ]
    pipeline = {"total_cost_usd": 0.50, "total_input_tokens": 10000, "total_output_tokens": 4000}
    result = build_cost_table(tasks, pipeline)
    assert isinstance(result, Table)
    # 2 task rows + 1 footer
    assert result.row_count == 3


def test_build_cost_table_no_agent():
    """Tasks without assigned_agent show '-'."""
    tasks = [
        {
            "id": "task-1",
            "assigned_agent": None,
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": 0.01,
            "model_history": [],
        }
    ]
    pipeline = {"total_cost_usd": 0.01, "total_input_tokens": 100, "total_output_tokens": 50}
    result = build_cost_table(tasks, pipeline)
    assert isinstance(result, Table)


# ── build_context_panel tests ────────────────────────────────────────


def test_build_context_panel_no_active():
    """No in_progress tasks returns 'No active agents'."""
    tasks = [{"id": "task-1", "state": "done", "assigned_agent": "a-1", "model_history": []}]
    result = build_context_panel(tasks)
    assert result == "No active agents"


def test_build_context_panel_empty():
    """Empty task list returns 'No active agents'."""
    result = build_context_panel([])
    assert result == "No active agents"


def test_build_context_panel_active_task_normal():
    """Active task with normal pressure shows green."""
    tasks = [
        {
            "id": "task-1",
            "state": "in_progress",
            "assigned_agent": "agent-1",
            "model_history": [
                {"context_pressure": "normal", "context_utilization_pct": 0.42},
            ],
        }
    ]
    result = build_context_panel(tasks)
    assert "agent-1" in result
    assert "task-1" in result
    assert "[green]normal[/green]" in result
    assert "42%" in result


def test_build_context_panel_elevated():
    """Elevated pressure uses yellow."""
    tasks = [
        {
            "id": "task-2",
            "state": "in_progress",
            "assigned_agent": "agent-2",
            "model_history": [
                {"context_pressure": "elevated", "context_utilization_pct": 0.65},
            ],
        }
    ]
    result = build_context_panel(tasks)
    assert "[yellow]elevated[/yellow]" in result
    assert "65%" in result


def test_build_context_panel_high():
    """High pressure uses #ff8800."""
    tasks = [
        {
            "id": "task-3",
            "state": "in_progress",
            "assigned_agent": "agent-3",
            "model_history": [
                {"context_pressure": "high", "context_utilization_pct": 0.82},
            ],
        }
    ]
    result = build_context_panel(tasks)
    assert "[#ff8800]high[/#ff8800]" in result
    assert "82%" in result


def test_build_context_panel_critical():
    """Critical pressure uses red."""
    tasks = [
        {
            "id": "task-4",
            "state": "in_progress",
            "assigned_agent": "agent-4",
            "model_history": [
                {"context_pressure": "critical", "context_utilization_pct": 0.95},
            ],
        }
    ]
    result = build_context_panel(tasks)
    assert "[red]critical[/red]" in result
    assert "95%" in result


def test_build_context_panel_empty_model_history():
    """Active task with no model_history defaults to normal/0%."""
    tasks = [
        {
            "id": "task-5",
            "state": "in_progress",
            "assigned_agent": "agent-5",
            "model_history": [],
        }
    ]
    result = build_context_panel(tasks)
    assert "agent-5" in result
    assert "normal" in result
    assert "0%" in result


def test_build_context_panel_multiple_entries_uses_latest():
    """When model_history has multiple entries, the latest is used."""
    tasks = [
        {
            "id": "task-6",
            "state": "in_progress",
            "assigned_agent": "agent-6",
            "model_history": [
                {"context_pressure": "normal", "context_utilization_pct": 0.20},
                {"context_pressure": "elevated", "context_utilization_pct": 0.60},
            ],
        }
    ]
    result = build_context_panel(tasks)
    assert "elevated" in result
    assert "60%" in result


# ── build_scheduler_panel tests ──────────────────────────────────────


def test_build_scheduler_panel_empty():
    """Empty task list returns 'No scheduling data'."""
    result = build_scheduler_panel([])
    assert result == "No scheduling data"


def test_build_scheduler_panel_single_ready_task():
    """Single ready task shows critical path and priority info."""
    tasks = [
        {
            "id": "task-1",
            "title": "Do stuff",
            "description": "Details",
            "files": [],
            "depends_on": [],
            "complexity": "medium",
            "state": "todo",
            "assigned_agent": None,
            "retry_count": 0,
        }
    ]
    result = build_scheduler_panel(tasks)
    assert "Critical path length" in result
    assert "task-1" in result
    assert "priority=" in result
    assert "downstream=" in result


def test_build_scheduler_panel_with_dependencies():
    """Tasks with dependencies show longer critical path."""
    tasks = [
        {
            "id": "task-1",
            "title": "First",
            "description": "",
            "files": [],
            "depends_on": [],
            "complexity": "medium",
            "state": "done",
            "retry_count": 0,
        },
        {
            "id": "task-2",
            "title": "Second",
            "description": "",
            "files": [],
            "depends_on": ["task-1"],
            "complexity": "medium",
            "state": "todo",
            "retry_count": 0,
        },
    ]
    result = build_scheduler_panel(tasks)
    assert "Critical path length" in result


def test_build_scheduler_panel_backpressure():
    """Tasks with retry_count > 1 show backpressure penalty."""
    tasks = [
        {
            "id": "task-1",
            "title": "Struggling task",
            "description": "",
            "files": [],
            "depends_on": [],
            "complexity": "medium",
            "state": "todo",
            "retry_count": 3,
        }
    ]
    result = build_scheduler_panel(tasks)
    assert "Backpressure" in result
    assert "3 retries" in result
    assert "penalty: -90" in result  # 3 * 30


def test_build_scheduler_panel_no_backpressure():
    """Tasks with retry_count <= 1 don't show backpressure section."""
    tasks = [
        {
            "id": "task-1",
            "title": "Normal task",
            "description": "",
            "files": [],
            "depends_on": [],
            "complexity": "medium",
            "state": "todo",
            "retry_count": 0,
        }
    ]
    result = build_scheduler_panel(tasks)
    assert "Backpressure" not in result


# ── Formatting helper tests ──────────────────────────────────────────


def test_fmt_cost():
    """Cost formatting mirrors stats.py patterns."""
    assert _fmt_cost(0) == "-"
    assert _fmt_cost(-1) == "-"
    assert _fmt_cost(1.5) == "$1.50"
    assert _fmt_cost(0.005) == "$0.0050"
    assert _fmt_cost(12.0) == "$12.00"


def test_fmt_tokens():
    """Single token count formatting."""
    assert _fmt_tokens(500) == "500"
    assert "k" in _fmt_tokens(5000)
    assert "M" in _fmt_tokens(1500000)
    assert _fmt_tokens(0) == "0"


# ── CLI registration test ───────────────────────────────────────────


def test_health_registered_in_cli():
    """health command is registered as a lazy subcommand."""
    from forge.cli.main import _LAZY_SUBCOMMANDS

    names = [name for name, _, _ in _LAZY_SUBCOMMANDS]
    assert "health" in names


def test_health_help():
    """Health command appears in --help output."""
    from click.testing import CliRunner

    from forge.cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["health", "--help"])
    assert result.exit_code == 0
    assert "health" in result.output.lower()
    assert "--project-dir" in result.output
    assert "--pipeline" in result.output
    assert "--interval" in result.output
