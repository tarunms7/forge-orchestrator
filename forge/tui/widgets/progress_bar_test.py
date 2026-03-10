"""Tests for PipelineProgress widget."""

from forge.tui.widgets.progress_bar import format_progress


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
