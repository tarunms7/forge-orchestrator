"""Tests for forge costs CLI command."""

from __future__ import annotations

import asyncio
import os

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
                        "planner_cost_usd = :pcost, "
                        "total_input_tokens = :inp, total_output_tokens = :out, "
                        "tasks_succeeded = :succ, tasks_failed = :fail, "
                        "total_retries = :ret "
                        "WHERE id = :pid"
                    ),
                    {
                        "dur": p.get("duration_s", 0.0),
                        "cost": p.get("total_cost_usd", 0.0),
                        "pcost": p.get("planner_cost_usd", 0.0),
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
                    description=task.get("description", ""),
                    files=[],
                    depends_on=[],
                    complexity="medium",
                    pipeline_id=p["id"],
                )
                if any(
                    k in task
                    for k in ["cost_usd", "agent_cost_usd", "review_cost_usd", "input_tokens"]
                ):
                    async with db._session_factory() as session:
                        await session.execute(
                            text(
                                "UPDATE tasks SET "
                                "state = :state, "
                                "cost_usd = :cost, "
                                "agent_cost_usd = :acost, "
                                "review_cost_usd = :rcost, "
                                "input_tokens = :inp, "
                                "output_tokens = :out "
                                "WHERE id = :tid"
                            ),
                            {
                                "state": task.get("state", "done"),
                                "cost": task.get("cost_usd", 0.0),
                                "acost": task.get("agent_cost_usd", 0.0),
                                "rcost": task.get("review_cost_usd", 0.0),
                                "inp": task.get("input_tokens", 0),
                                "out": task.get("output_tokens", 0),
                                "tid": task["id"],
                            },
                        )
                        await session.commit()
    finally:
        await db.close()


# ── Default overview tests ────────────────────────────────────────────


def test_costs_no_pipelines(central_db):
    """Graceful message when DB has no pipelines."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, []))
    runner = CliRunner()
    result = runner.invoke(cli, ["costs"])
    assert result.exit_code == 0
    assert "No pipelines found" in result.output


def test_costs_overview_shows_cost_columns(central_db):
    """Default view shows pipeline table with cost breakdown columns."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-cost-1",
                    "description": "Cost test pipeline",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 120.5,
                    "total_cost_usd": 1.50,
                    "planner_cost_usd": 0.20,
                    "total_input_tokens": 32000,
                    "total_output_tokens": 12000,
                    "tasks_succeeded": 2,
                    "tasks_failed": 0,
                    "total_retries": 0,
                    "tasks": [
                        {
                            "id": "task-c1",
                            "title": "Auth endpoints",
                            "state": "done",
                            "cost_usd": 0.80,
                            "agent_cost_usd": 0.60,
                            "review_cost_usd": 0.20,
                            "input_tokens": 20000,
                            "output_tokens": 8000,
                        },
                        {
                            "id": "task-c2",
                            "title": "User model",
                            "state": "done",
                            "cost_usd": 0.50,
                            "agent_cost_usd": 0.35,
                            "review_cost_usd": 0.15,
                            "input_tokens": 12000,
                            "output_tokens": 4000,
                        },
                    ],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["costs", "--project-dir", cwd])
    assert result.exit_code == 0
    assert "pipe-cos" in result.output  # truncated ID
    assert "Cost Breakdown" in result.output
    assert "Cost Summary" in result.output
    # Column headers may be truncated by Rich; check for partial matches
    assert "Plann" in result.output
    assert "Agent" in result.output
    assert "Revi" in result.output
    assert "CI Fix" in result.output
    assert "Total" in result.output


def test_costs_all_flag(central_db):
    """--all shows pipelines from all projects."""
    db_url = _db_url(central_db)
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-all-a",
                    "description": "Project Alpha",
                    "project_path": "/projects/alpha",
                    "project_name": "alpha",
                    "tasks_succeeded": 1,
                    "tasks_failed": 0,
                },
                {
                    "id": "pipe-all-b",
                    "description": "Project Beta",
                    "project_path": "/projects/beta",
                    "project_name": "beta",
                    "tasks_succeeded": 1,
                    "tasks_failed": 0,
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["costs", "--all"])
    assert result.exit_code == 0
    assert "pipe-all" in result.output


# ── Pipeline drill-down tests ────────────────────────────────────────


def test_costs_pipeline_drilldown(central_db):
    """--pipeline shows stage breakdown and top tasks."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-drill-cost",
                    "description": "Drilldown cost test",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 300.0,
                    "total_cost_usd": 2.50,
                    "planner_cost_usd": 0.30,
                    "total_input_tokens": 50000,
                    "total_output_tokens": 20000,
                    "tasks_succeeded": 3,
                    "tasks_failed": 0,
                    "total_retries": 0,
                    "tasks": [
                        {
                            "id": "task-dc1",
                            "title": "Expensive task",
                            "state": "done",
                            "cost_usd": 1.20,
                            "agent_cost_usd": 0.90,
                            "review_cost_usd": 0.30,
                            "input_tokens": 25000,
                            "output_tokens": 10000,
                        },
                        {
                            "id": "task-dc2",
                            "title": "Medium task",
                            "state": "done",
                            "cost_usd": 0.60,
                            "agent_cost_usd": 0.45,
                            "review_cost_usd": 0.15,
                            "input_tokens": 15000,
                            "output_tokens": 6000,
                        },
                        {
                            "id": "task-dc3",
                            "title": "Cheap task",
                            "state": "done",
                            "cost_usd": 0.20,
                            "agent_cost_usd": 0.15,
                            "review_cost_usd": 0.05,
                            "input_tokens": 10000,
                            "output_tokens": 4000,
                        },
                    ],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["costs", "--pipeline", "pipe-drill-cost"])
    assert result.exit_code == 0
    assert "Stage Cost Breakdown" in result.output
    assert "Top 5 Most Expensive Tasks" in result.output
    assert "Planner" in result.output
    assert "Agent" in result.output
    assert "Review" in result.output
    assert "CI Fix" in result.output
    # Most expensive task should appear
    assert "Expensive" in result.output


def test_costs_pipeline_not_found(central_db):
    """--pipeline with unknown ID gives helpful message."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, []))
    runner = CliRunner()
    result = runner.invoke(cli, ["costs", "--pipeline", "nonexistent"])
    assert result.exit_code == 0
    assert "not found" in result.output


def test_costs_pipeline_no_events(central_db):
    """Drill-down with no cost events shows message."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-no-events",
                    "description": "No events pipeline",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "total_cost_usd": 0.50,
                    "planner_cost_usd": 0.10,
                    "tasks_succeeded": 1,
                    "tasks_failed": 0,
                    "tasks": [
                        {
                            "id": "task-ne1",
                            "title": "Simple task",
                            "state": "done",
                            "cost_usd": 0.40,
                            "agent_cost_usd": 0.30,
                            "review_cost_usd": 0.10,
                            "input_tokens": 5000,
                            "output_tokens": 2000,
                        },
                    ],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["costs", "--pipeline", "pipe-no-events"])
    assert result.exit_code == 0
    assert "No cost update events" in result.output


# ── Help test ─────────────────────────────────────────────────────────


def test_costs_help():
    """Costs command appears in --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["costs", "--help"])
    assert result.exit_code == 0
    assert "cost" in result.output.lower()


# ── Formatting helper tests ──────────────────────────────────────────


def test_costs_fmt_cost():
    """Cost formatting from costs module."""
    from forge.cli.costs import _fmt_cost

    assert _fmt_cost(0) == "-"
    assert _fmt_cost(-1) == "-"
    assert _fmt_cost(1.5) == "$1.50"
    assert _fmt_cost(0.005) == "$0.0050"


def test_costs_fmt_duration():
    """Duration formatting from costs module."""
    from forge.cli.costs import _fmt_duration

    assert _fmt_duration(0) == "-"
    assert _fmt_duration(30.5) == "30.5s"
    assert "m" in _fmt_duration(90)
    assert "h" in _fmt_duration(3700)


def test_costs_truncate():
    """String truncation from costs module."""
    from forge.cli.costs import _truncate

    assert _truncate("short", 10) == "short"
    assert len(_truncate("a very long string indeed", 10)) == 10
