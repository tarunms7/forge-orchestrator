"""Tests for forge status CLI command."""

from __future__ import annotations

import asyncio
import os

import pytest
from click.testing import CliRunner
from unittest.mock import patch

from forge.cli.main import cli
from forge.storage.db import Database


@pytest.fixture()
def central_db(tmp_path, monkeypatch):
    """Point FORGE_DATA_DIR to a temp dir so the central DB is isolated."""
    data_dir = tmp_path / "forge-data"
    data_dir.mkdir()
    monkeypatch.setenv("FORGE_DATA_DIR", str(data_dir))
    return data_dir


def _db_url(central_db):
    return f"sqlite+aiosqlite:///{central_db / 'forge.db'}"


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
                project_path=p.get("project_path"),
                project_name=p.get("project_name"),
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


def test_status_no_pipelines(central_db):
    """Graceful message when DB exists but is empty."""
    db_url = _db_url(central_db)
    _run_async(_seed_db(db_url, []))
    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "No pipelines found" in result.output


def test_status_shows_pipelines(central_db):
    """Table includes pipeline data."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "pipe-1",
                    "description": "Build REST API",
                    "status": "executing",
                    "project_path": cwd,
                    "project_name": os.path.basename(cwd),
                    "tasks": [
                        {"id": "t1"},
                        {"id": "t2"},
                    ],
                },
                {
                    "id": "pipe-2",
                    "description": "Add auth",
                    "project_path": cwd,
                    "project_name": os.path.basename(cwd),
                    "tasks": [],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", cwd])
    assert result.exit_code == 0
    assert "pipe-1" in result.output
    assert "Build REST API" in result.output
    assert "pipe-2" in result.output
    assert "Add auth" in result.output


def test_status_task_count(central_db):
    """Task count column reflects number of tasks per pipeline."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "p-count",
                    "description": "Counting tasks",
                    "project_path": cwd,
                    "project_name": os.path.basename(cwd),
                    "tasks": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", cwd])
    assert result.exit_code == 0
    assert "3" in result.output


def test_status_shows_status_text(central_db):
    """Status column shows status value."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "p-done",
                    "description": "Completed pipeline",
                    "status": "complete",
                    "project_path": cwd,
                    "project_name": os.path.basename(cwd),
                    "tasks": [],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", cwd])
    assert result.exit_code == 0
    assert "complete" in result.output


def test_status_all_flag(central_db):
    """--all flag shows pipelines from all projects with Project column."""
    db_url = _db_url(central_db)
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "pipe-a",
                    "description": "Project A pipeline",
                    "project_path": "/projects/alpha",
                    "project_name": "alpha",
                    "tasks": [],
                },
                {
                    "id": "pipe-b",
                    "description": "Project B pipeline",
                    "project_path": "/projects/beta",
                    "project_name": "beta",
                    "tasks": [],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--all"])
    assert result.exit_code == 0
    assert "pipe-a" in result.output
    assert "pipe-b" in result.output
    # Project names should appear in the Project column
    assert "alpha" in result.output
    assert "beta" in result.output


def test_status_without_all_filters_to_project(central_db):
    """Without --all, only the current project's pipelines are shown."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "pipe-here",
                    "description": "Current project",
                    "project_path": cwd,
                    "project_name": os.path.basename(cwd),
                    "tasks": [],
                },
                {
                    "id": "pipe-other",
                    "description": "Other project",
                    "project_path": "/some/other/project",
                    "project_name": "other",
                    "tasks": [],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", cwd])
    assert result.exit_code == 0
    assert "pipe-here" in result.output
    assert "pipe-other" not in result.output


def test_status_help():
    """Status command appears in --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output.lower()


def test_status_central_db_usage(central_db):
    """Status uses the central DB from forge_db_url, not a project-local DB."""
    db_url = _db_url(central_db)
    _run_async(
        _seed_db(
            db_url,
            [
                {
                    "id": "central-pipe",
                    "description": "From central DB",
                    "project_path": os.getcwd(),
                    "project_name": "test",
                    "tasks": [],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--project-dir", os.getcwd()])
    assert result.exit_code == 0
    assert "central-pipe" in result.output
