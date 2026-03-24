"""Tests for forge export CLI command."""

from __future__ import annotations

import asyncio
import json

import pytest
from click.testing import CliRunner

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


async def _seed_pipelines(db_url: str, pipelines: list[dict]) -> None:
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

            from sqlalchemy import text

            async with db._session_factory() as session:
                await session.execute(
                    text(
                        "UPDATE pipelines SET "
                        "duration_s = :dur, total_cost_usd = :cost, "
                        "total_input_tokens = :inp, total_output_tokens = :out, "
                        "tasks_succeeded = :succ, tasks_failed = :fail, "
                        "total_retries = :ret "
                        "WHERE id = :pid"
                    ),
                    {
                        "dur": p.get("duration_s", 0.0),
                        "cost": p.get("total_cost_usd", 0.0),
                        "inp": p.get("total_input_tokens", 0),
                        "out": p.get("total_output_tokens", 0),
                        "succ": p.get("tasks_succeeded", 0),
                        "fail": p.get("tasks_failed", 0),
                        "ret": p.get("total_retries", 0),
                        "pid": p["id"],
                    },
                )
                await session.commit()

            for task in p.get("tasks", []):
                await db.create_task(
                    id=task["id"],
                    title=task.get("title", task["id"]),
                    description=task.get("description", "Test task"),
                    files=task.get("files", []),
                    depends_on=[],
                    complexity=task.get("complexity", "medium"),
                    pipeline_id=p["id"],
                )
                if any(
                    k in task
                    for k in [
                        "agent_duration_s",
                        "cost_usd",
                        "input_tokens",
                        "retry_count",
                        "num_turns",
                    ]
                ):
                    async with db._session_factory() as session:
                        await session.execute(
                            text(
                                "UPDATE tasks SET "
                                "state = :state, "
                                "agent_duration_s = :agent, "
                                "review_duration_s = :review, "
                                "lint_duration_s = :lint, "
                                "merge_duration_s = :merge, "
                                "cost_usd = :cost, "
                                "agent_cost_usd = :acost, "
                                "review_cost_usd = :rcost, "
                                "input_tokens = :inp, "
                                "output_tokens = :out, "
                                "retry_count = :ret, "
                                "num_turns = :turns "
                                "WHERE id = :tid"
                            ),
                            {
                                "state": task.get("state", "done"),
                                "agent": task.get("agent_duration_s", 0.0),
                                "review": task.get("review_duration_s", 0.0),
                                "lint": task.get("lint_duration_s", 0.0),
                                "merge": task.get("merge_duration_s", 0.0),
                                "cost": task.get("cost_usd", 0.0),
                                "acost": task.get("agent_cost_usd", 0.0),
                                "rcost": task.get("review_cost_usd", 0.0),
                                "inp": task.get("input_tokens", 0),
                                "out": task.get("output_tokens", 0),
                                "ret": task.get("retry_count", 0),
                                "turns": task.get("num_turns", 0),
                                "tid": task["id"],
                            },
                        )
                        await session.commit()
    finally:
        await db.close()


_SAMPLE_PIPELINE = {
    "id": "pipe-export-1",
    "description": "Build REST API endpoints",
    "status": "done",
    "project_path": "/tmp",
    "project_name": "test-project",
    "duration_s": 300.0,
    "total_cost_usd": 1.25,
    "total_input_tokens": 50000,
    "total_output_tokens": 12000,
    "tasks_succeeded": 2,
    "tasks_failed": 0,
    "total_retries": 1,
    "tasks": [
        {
            "id": "task-e1",
            "title": "Add auth endpoints",
            "description": "Implement authentication endpoints",
            "state": "done",
            "files": ["src/auth.py", "src/models.py"],
            "complexity": "medium",
            "agent_duration_s": 45.0,
            "review_duration_s": 12.0,
            "lint_duration_s": 3.0,
            "merge_duration_s": 2.0,
            "cost_usd": 0.45,
            "agent_cost_usd": 0.35,
            "review_cost_usd": 0.10,
            "input_tokens": 25000,
            "output_tokens": 6000,
            "retry_count": 1,
            "num_turns": 8,
        },
        {
            "id": "task-e2",
            "title": "Add user model",
            "description": "Create user data model",
            "state": "done",
            "files": ["src/models.py"],
            "complexity": "low",
            "agent_duration_s": 30.0,
            "review_duration_s": 8.0,
            "lint_duration_s": 2.0,
            "merge_duration_s": 1.0,
            "cost_usd": 0.35,
            "agent_cost_usd": 0.25,
            "review_cost_usd": 0.10,
            "input_tokens": 20000,
            "output_tokens": 5000,
            "retry_count": 0,
            "num_turns": 5,
        },
    ],
}


def test_export_json(central_db):
    """Export as JSON produces valid JSON with tasks."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, [_SAMPLE_PIPELINE]))

    runner = CliRunner()
    result = runner.invoke(cli, ["export", "pipe-export-1", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == "pipe-export-1"
    assert "tasks" in data
    assert len(data["tasks"]) == 2


def test_export_md(central_db):
    """Export as Markdown contains table markers and description."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, [_SAMPLE_PIPELINE]))

    runner = CliRunner()
    result = runner.invoke(cli, ["export", "pipe-export-1", "--format", "md"])
    assert result.exit_code == 0
    assert "|" in result.output
    assert "Build REST API" in result.output


def test_export_csv(central_db):
    """Export as CSV contains header row."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, [_SAMPLE_PIPELINE]))

    runner = CliRunner()
    result = runner.invoke(cli, ["export", "pipe-export-1", "--format", "csv"])
    assert result.exit_code == 0
    assert "task_id" in result.output
    assert "title" in result.output
    assert "cost_usd" in result.output


def test_export_not_found(central_db):
    """Export with unknown pipeline ID shows not found and exits 1."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, []))

    runner = CliRunner()
    result = runner.invoke(cli, ["export", "nonexistent-id"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "not found" in (result.stderr or "").lower()


def test_export_default_format_is_json(central_db):
    """Default format (no --format) produces valid JSON."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, [_SAMPLE_PIPELINE]))

    runner = CliRunner()
    result = runner.invoke(cli, ["export", "pipe-export-1"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == "pipe-export-1"


def test_export_to_file(central_db, tmp_path):
    """--output writes to file and confirms on stdout."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, [_SAMPLE_PIPELINE]))

    out_file = tmp_path / "export.json"
    runner = CliRunner()
    result = runner.invoke(cli, ["export", "pipe-export-1", "-o", str(out_file)])
    assert result.exit_code == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text())
    assert data["id"] == "pipe-export-1"
    assert "Exported to" in result.output


def test_export_help():
    """Help output shows format options."""
    runner = CliRunner()
    result = runner.invoke(cli, ["export", "--help"])
    assert result.exit_code == 0
    assert "json" in result.output
    assert "md" in result.output
    assert "csv" in result.output
