"""Tests for PipelineProgress widget."""

from forge.tui.widgets.progress_bar import (
    PipelineProgress,
    format_progress,
    format_task_progress,
)


def test_format_progress_planning():
    result = format_progress(0, 0, 0.50, 15.0, "planning")
    assert "Planning" in result
    assert "$0.50" in result
    assert "0:15" in result


def test_format_progress_planned():
    result = format_progress(0, 3, 1.20, 30.0, "planned")
    assert "Plan ready" in result
    assert "$1.20" in result


def test_format_progress_executing():
    result = format_progress(2, 5, 3.00, 120.0, "executing")
    assert "40%" in result
    assert "2/5" in result
    assert "$3.00" in result
    assert "2:00" in result
    assert "█" in result


def test_format_progress_complete():
    result = format_progress(5, 5, 4.50, 300.0, "complete")
    assert "Complete" in result
    assert "5/5" in result
    assert "$4.50" in result


def test_format_progress_error():
    result = format_progress(2, 5, 2.00, 60.0, "error")
    assert "Error" in result
    assert "$2.00" in result


def test_format_progress_idle():
    result = format_progress(0, 0, 0.0, 0.0, "idle")
    assert "idle" in result
    assert "$0.00" in result


def test_format_task_progress_segmented():
    """format_task_progress should build per-task segments."""
    tasks = [
        {"state": "done"},
        {"state": "in_progress"},
        {"state": "todo"},
    ]
    result = format_task_progress(tasks, 1.50, 120, "executing")
    assert "█" in result  # Done + active segments
    assert "░" in result  # Pending segment
    assert "33%" in result
    assert "1/3" in result


def test_format_task_progress_all_done():
    tasks = [{"state": "done"}, {"state": "done"}]
    result = format_task_progress(tasks, 2.0, 300, "executing")
    assert "100%" in result
    assert "2/2" in result


def test_format_task_progress_error_segment():
    tasks = [{"state": "done"}, {"state": "error"}, {"state": "todo"}]
    result = format_task_progress(tasks, 1.0, 60, "executing")
    assert "f85149" in result  # Red for error segment


def test_format_task_progress_planning_phase():
    """Non-task phases should show status text, not segments."""
    result = format_task_progress([], 0.0, 10, "planning")
    assert "Planning" in result


def test_pipeline_progress_has_pulse_state():
    pp = PipelineProgress()
    assert hasattr(pp, "_pulse_frame")
    assert pp._pulse_frame == 0
    assert pp._tasks == []


def test_pipeline_progress_update_tasks():
    pp = PipelineProgress()
    tasks = [{"state": "done"}, {"state": "in_progress"}]
    pp.update_tasks(tasks)
    assert pp._tasks == tasks
