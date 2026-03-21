"""End-to-end integration test: guard triggers → lesson captured → lesson in DB."""

import pytest
from dataclasses import dataclass

from forge.learning.guard import RuntimeGuard, GuardTriggered
from forge.learning.store import LessonStore
from forge.learning.extractor import extract_from_command_failures


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


def _make_bash_call(tool_id: str, command: str) -> FakeMsg:
    return FakeMsg([FakeToolUse(tool_id, "Bash", {"command": command})])


def _make_bash_error(tool_id: str, error: str) -> FakeMsg:
    return FakeMsg([FakeToolResult(tool_id, error, True)])


def _make_bash_success(tool_id: str) -> FakeMsg:
    return FakeMsg([FakeToolResult(tool_id, "OK", False)])


@pytest.mark.asyncio
async def test_full_flow_guard_to_lesson_capture(tmp_path):
    """Simulate: 3 identical Bash failures → guard triggers → lesson stored in DB."""
    # Setup
    store = LessonStore(str(tmp_path / "lessons.db"))
    await store.initialize()
    guard = RuntimeGuard()

    error_msg = "ModuleNotFoundError: No module named 'nonexistent'"
    command = ".venv/bin/python -m pytest tests/ -x -v"

    # Attempt 1: fail
    guard.inspect(_make_bash_call("t1", command))
    guard.inspect(_make_bash_error("t1", error_msg))

    # Attempt 2: fail — should warn
    guard.inspect(_make_bash_call("t2", command))
    result = guard.inspect(_make_bash_error("t2", error_msg))
    assert result == "warning"

    # Attempt 3: fail — should trigger
    guard.inspect(_make_bash_call("t3", command))
    with pytest.raises(GuardTriggered) as exc_info:
        guard.inspect(_make_bash_error("t3", error_msg))

    # Extract lesson from guard's failures
    lesson = extract_from_command_failures(
        exc_info.value.failures, project_dir=str(tmp_path)
    )
    assert lesson.category == "command_failure"
    assert lesson.trigger  # not empty

    # Store the lesson
    await store.add_lesson(lesson)

    # Verify it's in the DB
    all_lessons = await store.all_lessons()
    assert len(all_lessons) == 1
    assert "module_not_found" in all_lessons[0].title
    assert all_lessons[0].hit_count == 1

    # Verify find_matching works for future prevention
    match = await store.find_matching(command)
    assert match is not None
    assert match.id == lesson.id


@pytest.mark.asyncio
async def test_different_approach_resets_counter():
    """Different base commands should NOT accumulate."""
    guard = RuntimeGuard()

    # Fail with .venv/bin/python
    guard.inspect(_make_bash_call("t1", ".venv/bin/python -m pytest tests/"))
    guard.inspect(_make_bash_error("t1", "command not found"))

    guard.inspect(_make_bash_call("t2", ".venv/bin/python -m pytest tests/"))
    guard.inspect(_make_bash_error("t2", "command not found"))

    # Switch to python -m pytest (different approach) — counter resets
    guard.inspect(_make_bash_call("t3", "python -m pytest tests/"))
    result = guard.inspect(_make_bash_error("t3", "command not found"))
    # This is a FIRST failure for the new approach, not a third
    assert result is None  # no warning yet


@pytest.mark.asyncio
async def test_success_does_not_trigger():
    """Successful commands between failures should not affect counters."""
    guard = RuntimeGuard()

    # Fail once
    guard.inspect(_make_bash_call("t1", "pytest tests/"))
    guard.inspect(_make_bash_error("t1", "error"))

    # Succeed
    guard.inspect(_make_bash_call("t2", "pytest tests/"))
    guard.inspect(_make_bash_success("t2"))

    # Fail again — this is only the 2nd failure, not 3rd
    guard.inspect(_make_bash_call("t3", "pytest tests/"))
    result = guard.inspect(_make_bash_error("t3", "error"))
    assert result == "warning"  # 2nd failure = warning, not trigger


@pytest.mark.asyncio
async def test_lesson_dedup_bumps_hit_count(tmp_path):
    """If the same lesson trigger already exists, bump hit_count instead of creating duplicate."""
    store = LessonStore(str(tmp_path / "lessons.db"))
    await store.initialize()

    # Create a guard, trigger it, capture lesson
    for run in range(2):
        guard = RuntimeGuard()
        command = ".venv/bin/python -m pytest tests/"
        error = "ModuleNotFoundError: No module"
        for i in range(3):
            guard.inspect(_make_bash_call(f"t{run}_{i}", command))
            try:
                guard.inspect(_make_bash_error(f"t{run}_{i}", error))
            except GuardTriggered as exc:
                lesson = extract_from_command_failures(exc.failures)
                existing = await store.find_matching(lesson.trigger)
                if existing:
                    await store.bump_hit(existing.id)
                else:
                    await store.add_lesson(lesson)

    # Should have 1 lesson with hit_count=2, not 2 lessons
    all_lessons = await store.all_lessons()
    assert len(all_lessons) == 1
    assert all_lessons[0].hit_count == 2
