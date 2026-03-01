"""Tests for forge logs CLI command."""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from forge.cli.logs import _color_for_event
from forge.cli.main import cli


@pytest.fixture()
def forge_project(tmp_path):
    """Create a temporary project with a dummy forge DB file."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "forge.db").touch()
    return tmp_path


SAMPLE_EVENTS = [
    {
        "created_at": "2024-01-15T10:00:00+00:00",
        "event_type": "pipeline_started",
        "task_id": None,
        "payload": {"message": "Pipeline execution began"},
    },
    {
        "created_at": "2024-01-15T10:01:00+00:00",
        "event_type": "task_success",
        "task_id": "task-abc",
        "payload": {"message": "Task completed successfully"},
    },
    {
        "created_at": "2024-01-15T10:02:00+00:00",
        "event_type": "pipeline_error",
        "task_id": "task-def",
        "payload": {"error": "Something went wrong"},
    },
]


def test_logs_displays_colored_timeline(forge_project):
    """Logs command displays a colored timeline for a valid pipeline."""
    db_path = str(forge_project / ".forge" / "forge.db")
    runner = CliRunner()

    with patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=SAMPLE_EVENTS)):
        result = runner.invoke(cli, ["logs", "pipe-1", "--db", db_path])

    assert result.exit_code == 0
    # Timestamps appear
    assert "2024-01-15T10:00:00" in result.output
    assert "2024-01-15T10:01:00" in result.output
    # Event types appear
    assert "pipeline_started" in result.output
    assert "task_success" in result.output
    assert "pipeline_error" in result.output
    # Task IDs appear
    assert "task-abc" in result.output
    assert "task-def" in result.output
    # Payload summaries appear
    assert "Pipeline execution began" in result.output
    assert "Something went wrong" in result.output


def test_logs_pipeline_with_no_events(forge_project):
    """Logs command shows a message when pipeline has no events."""
    db_path = str(forge_project / ".forge" / "forge.db")
    runner = CliRunner()

    with patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=[])):
        result = runner.invoke(cli, ["logs", "pipe-empty", "--db", db_path])

    assert result.exit_code == 0
    assert "No events found" in result.output
    assert "pipe-empty" in result.output


def test_logs_nonexistent_pipeline_id(forge_project):
    """Logs command handles a pipeline-id that does not exist in the DB."""
    db_path = str(forge_project / ".forge" / "forge.db")
    runner = CliRunner()

    with patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=[])):
        result = runner.invoke(cli, ["logs", "nonexistent-pipe-999", "--db", db_path])

    assert result.exit_code == 0
    assert "No events found" in result.output
    assert "nonexistent-pipe-999" in result.output


def test_event_type_color_coding():
    """Event type keywords map to expected Rich color styles."""
    # Success-related → green
    assert _color_for_event("task_success") == "green"
    assert _color_for_event("pipeline_complete") == "green"
    assert _color_for_event("done") == "green"

    # Error-related → red
    assert _color_for_event("pipeline_error") == "red"
    assert _color_for_event("task_fail") == "red"

    # Warning-related → yellow
    assert _color_for_event("warning_issued") == "yellow"
    assert _color_for_event("warn") == "yellow"

    # Start/info → cyan
    assert _color_for_event("pipeline_started") == "cyan"
    assert _color_for_event("task_info") == "cyan"

    # Pending → dim
    assert _color_for_event("pending") == "dim"

    # Unknown → white fallback
    assert _color_for_event("something_unknown") == "white"


def test_event_type_color_coding_in_output(forge_project):
    """ANSI color codes appear in the rendered console output for event types."""
    db_path = str(forge_project / ".forge" / "forge.db")
    events = [
        {
            "created_at": "2024-01-15T10:00:00",
            "event_type": "task_success",
            "task_id": None,
            "payload": {},
        },
        {
            "created_at": "2024-01-15T10:01:00",
            "event_type": "pipeline_error",
            "task_id": None,
            "payload": {},
        },
    ]

    buf = io.StringIO()

    with (
        patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=events)),
        patch(
            "forge.cli.logs.Console",
            lambda: Console(file=buf, force_terminal=True),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["logs", "pipe-color", "--db", db_path])

    assert result.exit_code == 0

    colored_output = buf.getvalue()
    # ANSI escape sequences should be present (color rendering)
    assert "\x1b[" in colored_output
    # Event types still appear in the colored output
    assert "task_success" in colored_output
    assert "pipeline_error" in colored_output
