"""End-to-end integration test: guard triggers → lesson captured → central DB."""

import pytest
from dataclasses import dataclass

from forge.learning.guard import RuntimeGuard, GuardTriggered
from forge.learning.extractor import extract_from_command_failures
from forge.storage.db import Database


@dataclass
class FakeToolUse:
    id: str
    name: str
    input: dict


@dataclass
class FakeToolResult:
    tool_use_id: str
    content: str
    is_error: bool


@dataclass
class FakeMsg:
    content: list


def _bash_call(tid: str, cmd: str) -> FakeMsg:
    return FakeMsg([FakeToolUse(tid, "Bash", {"command": cmd})])


def _bash_error(tid: str, err: str) -> FakeMsg:
    return FakeMsg([FakeToolResult(tid, err, True)])


def _bash_ok(tid: str) -> FakeMsg:
    return FakeMsg([FakeToolResult(tid, "OK", False)])


@pytest.fixture
async def db(tmp_path):
    d = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await d.initialize()
    yield d
    await d.close()


@pytest.mark.asyncio
async def test_full_flow_guard_to_lesson_in_central_db(db):
    """3 identical Bash failures → guard triggers → lesson stored in central DB."""
    guard = RuntimeGuard()
    error_msg = "ModuleNotFoundError: No module named 'nonexistent'"
    command = ".venv/bin/python -m pytest tests/ -x -v"

    # Attempt 1
    guard.inspect(_bash_call("t1", command))
    guard.inspect(_bash_error("t1", error_msg))

    # Attempt 2 — warning
    guard.inspect(_bash_call("t2", command))
    result = guard.inspect(_bash_error("t2", error_msg))
    assert result == "warning"

    # Attempt 3 — trigger
    guard.inspect(_bash_call("t3", command))
    with pytest.raises(GuardTriggered) as exc_info:
        guard.inspect(_bash_error("t3", error_msg))

    # Extract and store lesson using central DB
    lesson = extract_from_command_failures(exc_info.value.failures, project_dir="/proj")
    await db.add_lesson(
        scope=lesson.scope, category=lesson.category,
        title=lesson.title, content=lesson.content,
        trigger=lesson.trigger, resolution=lesson.resolution,
        project_dir="/proj" if lesson.scope == "project" else None,
    )

    # Verify it's in the DB
    rows = await db.list_all_lessons()
    assert len(rows) == 1
    assert "module_not_found" in rows[0].title

    # Verify find_matching works
    match = await db.find_matching_lesson(command)
    assert match is not None


@pytest.mark.asyncio
async def test_dedup_bumps_hit_count(db):
    """Same lesson trigger → bump hit_count, not duplicate."""
    command = ".venv/bin/python -m pytest tests/"
    error = "ModuleNotFoundError: No module"

    for run in range(2):
        guard = RuntimeGuard()
        for i in range(3):
            guard.inspect(_bash_call(f"t{run}_{i}", command))
            try:
                guard.inspect(_bash_error(f"t{run}_{i}", error))
            except GuardTriggered as exc:
                lesson = extract_from_command_failures(exc.failures)
                existing = await db.find_matching_lesson(lesson.trigger)
                if existing:
                    await db.bump_lesson_hit(existing.id)
                else:
                    await db.add_lesson(
                        scope=lesson.scope, category=lesson.category,
                        title=lesson.title, content=lesson.content,
                        trigger=lesson.trigger, resolution=lesson.resolution,
                    )

    rows = await db.list_all_lessons()
    assert len(rows) == 1
    assert rows[0].hit_count == 2


@pytest.mark.asyncio
async def test_different_approach_resets_counter():
    """Different base commands don't accumulate."""
    guard = RuntimeGuard()

    guard.inspect(_bash_call("t1", ".venv/bin/python -m pytest tests/"))
    guard.inspect(_bash_error("t1", "command not found"))
    guard.inspect(_bash_call("t2", ".venv/bin/python -m pytest tests/"))
    guard.inspect(_bash_error("t2", "command not found"))

    # Different approach — counter resets
    guard.inspect(_bash_call("t3", "python -m pytest tests/"))
    result = guard.inspect(_bash_error("t3", "command not found"))
    assert result is None  # first failure for new approach
