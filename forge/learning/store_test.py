"""Tests for lesson storage in the central Database."""

import pytest

from forge.learning.store import Lesson, format_lessons_block
from forge.storage.db import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await d.initialize()
    yield d
    await d.close()


@pytest.mark.asyncio
async def test_add_and_retrieve(db):
    lid = await db.add_lesson(
        scope="global", category="command_failure",
        title="test lesson", content="content",
        trigger="pytest", resolution="use python -m pytest",
    )
    assert lid
    rows = await db.list_all_lessons()
    assert len(rows) == 1
    assert rows[0].title == "test lesson"


@pytest.mark.asyncio
async def test_find_matching_by_trigger(db):
    await db.add_lesson(
        scope="global", category="command_failure",
        title="venv issue", content="content",
        trigger=".venv/bin/python", resolution="use python -m",
    )
    match = await db.find_matching_lesson(".venv/bin/python -m pytest tests/")
    assert match is not None
    assert match.title == "venv issue"


@pytest.mark.asyncio
async def test_find_matching_no_match(db):
    await db.add_lesson(
        scope="global", category="command_failure",
        title="venv issue", content="content",
        trigger=".venv/bin/python", resolution="use python -m",
    )
    match = await db.find_matching_lesson("cargo build")
    assert match is None


@pytest.mark.asyncio
async def test_bump_hit(db):
    lid = await db.add_lesson(
        scope="global", category="command_failure",
        title="test", content="c", trigger="t", resolution="r",
    )
    await db.bump_lesson_hit(lid)
    rows = await db.list_all_lessons()
    assert rows[0].hit_count == 2


@pytest.mark.asyncio
async def test_get_relevant_lessons_project_filter(db):
    await db.add_lesson(scope="global", category="command_failure",
        title="global one", content="c", trigger="t", resolution="r")
    await db.add_lesson(scope="project", category="command_failure",
        title="proj one", content="c", trigger="t2", resolution="r",
        project_dir="/proj/a")
    await db.add_lesson(scope="project", category="command_failure",
        title="other proj", content="c", trigger="t3", resolution="r",
        project_dir="/proj/b")

    rows = await db.get_relevant_lessons(project_dir="/proj/a")
    titles = {r.title for r in rows}
    assert "global one" in titles
    assert "proj one" in titles
    assert "other proj" not in titles


@pytest.mark.asyncio
async def test_get_relevant_lessons_category_filter(db):
    await db.add_lesson(scope="global", category="command_failure",
        title="cmd", content="c", trigger="t", resolution="r")
    await db.add_lesson(scope="global", category="review_failure",
        title="rev", content="c", trigger="t2", resolution="r")

    rows = await db.get_relevant_lessons(categories=["review_failure"])
    assert len(rows) == 1
    assert rows[0].title == "rev"


@pytest.mark.asyncio
async def test_clear_lessons_by_project(db):
    await db.add_lesson(scope="global", category="command_failure",
        title="g", content="c", trigger="t", resolution="r")
    await db.add_lesson(scope="project", category="command_failure",
        title="p", content="c", trigger="t2", resolution="r",
        project_dir="/proj")
    count = await db.clear_lessons(project_dir="/proj")
    assert count == 1
    remaining = await db.list_all_lessons()
    assert len(remaining) == 1
    assert remaining[0].title == "g"


def test_format_lessons_block():
    lessons = [
        Lesson(id="1", scope="global", category="command_failure",
            title="t1", content="c", trigger="tr", resolution="r1"),
        Lesson(id="2", scope="global", category="review_failure",
            title="t2", content="c", trigger="tr", resolution="r2"),
    ]
    block = format_lessons_block(lessons)
    assert "Command Failures" in block
    assert "Review Patterns" in block
    assert "t1" in block


def test_format_lessons_block_empty():
    assert format_lessons_block([]) == ""
