"""Tests for forge stats CLI command."""

from __future__ import annotations

import asyncio
import json
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
    """Seed the database with pipeline rows and optional tasks with metrics."""
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

            # Update pipeline-level metrics columns directly
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
                    description=task.get("description", ""),
                    files=[],
                    depends_on=[],
                    complexity="medium",
                    pipeline_id=p["id"],
                )
                # Update task metrics if provided
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
                                "num_turns = :turns, "
                                "max_turns = :max_turns "
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
                                "max_turns": task.get("max_turns", 25),
                                "tid": task["id"],
                            },
                        )
                        await session.commit()
    finally:
        await db.close()


# ── Default overview tests ────────────────────────────────────────────


def test_stats_no_pipelines(central_db):
    """Graceful message when DB has no pipelines."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, []))
    runner = CliRunner()
    result = runner.invoke(cli, ["stats"])
    assert result.exit_code == 0
    assert "No pipelines found" in result.output


def test_stats_overview_shows_pipelines(central_db):
    """Default view shows pipeline table with key columns."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-stats-1",
                    "description": "Build REST API",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 120.5,
                    "total_cost_usd": 0.45,
                    "total_input_tokens": 32000,
                    "total_output_tokens": 12000,
                    "tasks_succeeded": 3,
                    "tasks_failed": 0,
                    "total_retries": 1,
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--project-dir", cwd])
    assert result.exit_code == 0
    assert "pipe-sta" in result.output  # truncated ID
    assert "Build" in result.output  # description may be wrapped by Rich
    assert "done" in result.output
    # Summary panel should appear
    assert "Summary" in result.output
    assert "Pipelines" in result.output


def test_stats_overview_shows_summary_stats(central_db):
    """Summary panel shows aggregate statistics."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-a1",
                    "description": "Pipeline A",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 100.0,
                    "total_cost_usd": 1.0,
                    "total_input_tokens": 10000,
                    "total_output_tokens": 5000,
                    "tasks_succeeded": 2,
                    "tasks_failed": 0,
                    "total_retries": 0,
                },
                {
                    "id": "pipe-a2",
                    "description": "Pipeline B",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 200.0,
                    "total_cost_usd": 2.0,
                    "total_input_tokens": 20000,
                    "total_output_tokens": 10000,
                    "tasks_succeeded": 3,
                    "tasks_failed": 1,
                    "total_retries": 2,
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--project-dir", cwd])
    assert result.exit_code == 0
    assert "Pipelines" in result.output
    assert "Total cost" in result.output
    assert "Avg duration" in result.output
    assert "Retry rate" in result.output


def test_stats_json_output(central_db):
    """--json flag outputs valid JSON."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-json1",
                    "description": "JSON test",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 60.0,
                    "total_cost_usd": 0.5,
                    "total_input_tokens": 5000,
                    "total_output_tokens": 2000,
                    "tasks_succeeded": 1,
                    "tasks_failed": 0,
                    "total_retries": 0,
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--json", "--project-dir", cwd])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "pipe-json1"


# ── Pipeline drill-down tests ────────────────────────────────────────


def test_stats_pipeline_drilldown(central_db):
    """--pipeline shows per-task metrics table."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-drill",
                    "description": "Drilldown test pipeline",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 300.0,
                    "total_cost_usd": 1.5,
                    "total_input_tokens": 50000,
                    "total_output_tokens": 20000,
                    "tasks_succeeded": 2,
                    "tasks_failed": 0,
                    "total_retries": 1,
                    "tasks": [
                        {
                            "id": "task-d1",
                            "title": "Add auth endpoints",
                            "state": "done",
                            "agent_duration_s": 45.0,
                            "review_duration_s": 12.0,
                            "lint_duration_s": 3.0,
                            "merge_duration_s": 2.0,
                            "cost_usd": 0.45,
                            "agent_cost_usd": 0.35,
                            "review_cost_usd": 0.10,
                            "input_tokens": 25000,
                            "output_tokens": 10000,
                            "retry_count": 1,
                            "num_turns": 8,
                            "max_turns": 25,
                        },
                        {
                            "id": "task-d2",
                            "title": "Add user model",
                            "state": "done",
                            "agent_duration_s": 30.0,
                            "review_duration_s": 8.0,
                            "lint_duration_s": 2.0,
                            "merge_duration_s": 1.0,
                            "cost_usd": 0.35,
                            "agent_cost_usd": 0.25,
                            "review_cost_usd": 0.10,
                            "input_tokens": 20000,
                            "output_tokens": 8000,
                            "retry_count": 0,
                            "num_turns": 5,
                            "max_turns": 25,
                        },
                    ],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--pipeline", "pipe-drill"])
    assert result.exit_code == 0
    assert "Drilldown test pipeline" in result.output
    assert "task-d1" in result.output or "Add auth" in result.output
    assert "task-d2" in result.output or "Add user" in result.output
    assert "Task Metrics" in result.output
    # Waterfall should appear
    assert "Timing Waterfall" in result.output


def test_stats_pipeline_not_found(central_db):
    """--pipeline with unknown ID gives helpful message."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, []))
    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--pipeline", "nonexistent"])
    assert result.exit_code == 0
    assert "not found" in result.output


def test_stats_pipeline_json(central_db):
    """--pipeline with --json outputs valid JSON."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-pjson",
                    "description": "Pipeline JSON",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "tasks": [],
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--pipeline", "pipe-pjson", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["id"] == "pipe-pjson"
    assert "tasks" in data


# ── Trends tests ─────────────────────────────────────────────────────


def test_stats_trends_view(central_db):
    """--trends shows trend table with arrows."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": f"pipe-t{i}",
                    "description": f"Trend pipeline {i}",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 100.0 + i * 50,
                    "total_cost_usd": 0.5 + i * 0.3,
                    "total_input_tokens": 10000 * (i + 1),
                    "total_output_tokens": 5000 * (i + 1),
                    "tasks_succeeded": 2,
                    "tasks_failed": 0,
                    "total_retries": i,
                }
                for i in range(5)
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--trends", "--project-dir", cwd])
    assert result.exit_code == 0
    assert "Trends" in result.output
    assert "pipe-t0" in result.output or "pipe-t4" in result.output


def test_stats_trends_no_pipelines(central_db):
    """--trends with no data shows message."""
    db_url = _db_url(central_db)
    _run_async(_seed_pipelines(db_url, []))
    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--trends"])
    assert result.exit_code == 0
    assert "No pipelines found" in result.output


def test_stats_trends_json(central_db):
    """--trends --json outputs valid JSON."""
    db_url = _db_url(central_db)
    cwd = os.getcwd()
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-tj1",
                    "description": "Trend JSON",
                    "status": "done",
                    "project_path": cwd,
                    "project_name": "test",
                    "duration_s": 50.0,
                    "total_cost_usd": 0.3,
                    "total_input_tokens": 5000,
                    "total_output_tokens": 2000,
                    "tasks_succeeded": 1,
                    "tasks_failed": 0,
                    "total_retries": 0,
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--trends", "--json", "--project-dir", cwd])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)


# ── Filtering tests ──────────────────────────────────────────────────


def test_stats_all_flag(central_db):
    """--all shows pipelines from all projects."""
    db_url = _db_url(central_db)
    _run_async(
        _seed_pipelines(
            db_url,
            [
                {
                    "id": "pipe-proj-a",
                    "description": "Project A",
                    "project_path": "/projects/alpha",
                    "project_name": "alpha",
                    "tasks_succeeded": 1,
                    "tasks_failed": 0,
                },
                {
                    "id": "pipe-proj-b",
                    "description": "Project B",
                    "project_path": "/projects/beta",
                    "project_name": "beta",
                    "tasks_succeeded": 1,
                    "tasks_failed": 0,
                },
            ],
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--all"])
    assert result.exit_code == 0
    assert "pipe-pro" in result.output  # at least one shows up
    # Rich may wrap/truncate descriptions; check for partial match
    assert "Proje" in result.output


# ── Help test ─────────────────────────────────────────────────────────


def test_stats_help():
    """Stats command appears in --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["stats", "--help"])
    assert result.exit_code == 0
    assert "analytics" in result.output.lower() or "stats" in result.output.lower()


# ── Formatting helper tests ──────────────────────────────────────────


def test_fmt_duration():
    """Duration formatting covers edge cases."""
    from forge.cli.stats import _fmt_duration

    assert _fmt_duration(0) == "-"
    assert _fmt_duration(-1) == "-"
    assert _fmt_duration(30.5) == "30.5s"
    assert "m" in _fmt_duration(90)
    assert "h" in _fmt_duration(3700)


def test_fmt_cost():
    """Cost formatting."""
    from forge.cli.stats import _fmt_cost

    assert _fmt_cost(0) == "-"
    assert _fmt_cost(1.5) == "$1.50"
    assert _fmt_cost(0.005) == "$0.0050"


def test_fmt_tokens():
    """Token formatting with compact numbers."""
    from forge.cli.stats import _fmt_tokens

    assert _fmt_tokens(500, 200) == "500/200"
    assert "k" in _fmt_tokens(5000, 2000)
    assert "M" in _fmt_tokens(1500000, 500000)


def test_truncate():
    """String truncation."""
    from forge.cli.stats import _truncate

    assert _truncate("short", 10) == "short"
    assert len(_truncate("a very long string indeed", 10)) == 10


def test_trend_arrow():
    """Trend arrows reflect ratio to average."""
    from forge.cli.stats import _trend_arrow

    assert "↑" in _trend_arrow(200, 100)  # way above avg
    assert "↓" in _trend_arrow(50, 100)  # way below avg
    assert "→" in _trend_arrow(100, 100)  # at avg
    assert "→" in _trend_arrow(100, 0)  # no avg
