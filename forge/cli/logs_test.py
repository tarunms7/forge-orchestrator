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
def central_db(tmp_path, monkeypatch):
    """Point FORGE_DATA_DIR to a temp dir and create a dummy DB file."""
    data_dir = tmp_path / "forge-data"
    data_dir.mkdir()
    (data_dir / "forge.db").touch()
    monkeypatch.setenv("FORGE_DATA_DIR", str(data_dir))
    return data_dir


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


def test_logs_displays_colored_timeline(central_db):
    """Logs command displays a colored timeline for a valid pipeline."""
    runner = CliRunner()

    with patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=SAMPLE_EVENTS)):
        result = runner.invoke(cli, ["logs", "pipe-1"])

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


def test_logs_pipeline_with_no_events(central_db):
    """Logs command shows a message when pipeline has no events."""
    runner = CliRunner()

    with patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=[])):
        result = runner.invoke(cli, ["logs", "pipe-empty"])

    assert result.exit_code == 0
    assert "No events found" in result.output
    assert "pipe-empty" in result.output


def test_logs_nonexistent_pipeline_id(central_db):
    """Logs command handles a pipeline-id that does not exist in the DB."""
    runner = CliRunner()

    with patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=[])):
        result = runner.invoke(cli, ["logs", "nonexistent-pipe-999"])

    assert result.exit_code == 0
    assert "No events found" in result.output
    assert "nonexistent-pipe-999" in result.output


def test_event_type_color_coding():
    """Event type keywords map to expected Rich color styles."""
    # Success-related -> green
    assert _color_for_event("task_success") == "green"
    assert _color_for_event("pipeline_complete") == "green"
    assert _color_for_event("done") == "green"

    # Error-related -> red
    assert _color_for_event("pipeline_error") == "red"
    assert _color_for_event("task_fail") == "red"

    # Warning-related -> yellow
    assert _color_for_event("warning_issued") == "yellow"
    assert _color_for_event("warn") == "yellow"

    # Start/info -> cyan
    assert _color_for_event("pipeline_started") == "cyan"
    assert _color_for_event("task_info") == "cyan"

    # Pending -> dim
    assert _color_for_event("pending") == "dim"

    # Unknown -> white fallback
    assert _color_for_event("something_unknown") == "white"


def test_event_type_color_coding_in_output(central_db):
    """ANSI color codes appear in the rendered console output for event types."""
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
        result = runner.invoke(cli, ["logs", "pipe-color"])

    assert result.exit_code == 0

    colored_output = buf.getvalue()
    # ANSI escape sequences should be present (color rendering)
    assert "\x1b[" in colored_output
    # Event types still appear in the colored output
    assert "task_success" in colored_output
    assert "pipeline_error" in colored_output


def test_logs_db_override(tmp_path, monkeypatch):
    """--db flag overrides the default central DB path."""
    data_dir = tmp_path / "forge-data"
    data_dir.mkdir()
    monkeypatch.setenv("FORGE_DATA_DIR", str(data_dir))

    custom_db = tmp_path / "custom.db"
    custom_db.touch()

    runner = CliRunner()
    with patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=[])):
        result = runner.invoke(cli, ["logs", "pipe-1", "--db", str(custom_db)])

    assert result.exit_code == 0
    assert "No events found" in result.output


def test_logs_central_db_default(central_db):
    """Without --db, logs uses the central forge_db_path() as default."""
    runner = CliRunner()
    with patch("forge.cli.logs._fetch_events", new=AsyncMock(return_value=SAMPLE_EVENTS)):
        result = runner.invoke(cli, ["logs", "pipe-1"])

    assert result.exit_code == 0
    assert "pipeline_started" in result.output
