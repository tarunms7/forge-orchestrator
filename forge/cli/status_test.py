"""Tests for forge status CLI command."""

import asyncio
import os

import pytest
from click.testing import CliRunner

from forge.cli.main import cli
from forge.storage.db import Database


@pytest.fixture()
def forge_project(tmp_path):
    """Create a temporary project with an initialized Forge DB."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    return tmp_path


@pytest.fixture()
def db_url(forge_project):
    db_path = os.path.join(str(forge_project), ".forge", "forge.db")
    return f"sqlite+aiosqlite:///{db_path}"


def _run_async(coro):
    return asyncio.run(coro)


async def _seed_db(db_url: str, pipelines: list[dict]) -> None:
    """Seed the database with pipeline rows and optional tasks."""
    db = Database(db_url)
    await db.initialize()
    try:
        for p in pipelines:
            await db.create_pipeline(
                id=p["id"],
                description=p["description"],
                project_dir=p.get("project_dir", "/tmp"),
            )
            if p.get("status"):
                await db.update_pipeline_status(p["id"], p["status"])
            for task in p.get("tasks", []):
                await db.create_task(
                    id=task["id"],
                    title=task.get("title", task["id"]),
                    description=task.get("description", ""),
                    files=[],
                    depends_on=[],
                    complexity="medium",
                    pipeline_id=p["id"],
                )
    finally:
        await db.close()


def test_status_missing_db(tmp_path):
    """Error message when .forge/forge.db does not exist."""
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "Error" in result.output
    assert "not found" in result.output


def test_status_no_pipelines(forge_project, db_url):
    """Graceful message when DB exists but is empty."""
    _run_async(_seed_db(db_url, []))
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    assert "No pipelines found" in result.output


def test_status_shows_pipelines(forge_project, db_url):
    """Table includes pipeline data."""
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "pipe-1",
                    "description": "Build REST API",
                    "status": "executing",
                    "tasks": [
                        {"id": "t1"},
                        {"id": "t2"},
                    ],
                },
                {
                    "id": "pipe-2",
                    "description": "Add auth",
                    "tasks": [],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    assert "pipe-1" in result.output
    assert "Build REST API" in result.output
    assert "pipe-2" in result.output
    assert "Add auth" in result.output


def test_status_task_count(forge_project, db_url):
    """Task count column reflects number of tasks per pipeline."""
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "p-count",
                    "description": "Counting tasks",
                    "tasks": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    # The table should show "3" for 3 tasks
    assert "3" in result.output


def test_status_shows_status_text(forge_project, db_url):
    """Status column shows status value."""
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "p-done",
                    "description": "Completed pipeline",
                    "status": "complete",
                    "tasks": [],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", str(forge_project)])
    assert result.exit_code == 0
    assert "complete" in result.output


def test_status_help():
    """Status command appears in --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output.lower()
