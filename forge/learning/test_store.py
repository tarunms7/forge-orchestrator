"""Tests for forge.learning.store — LessonStore and format_lessons_block."""

import pytest

from forge.learning.store import Lesson, LessonStore, format_lessons_block


def _make_lesson(
    *,
    id: str = "",
    scope: str = "global",
    category: str = "command_failure",
    title: str = "Test lesson",
    content: str = "Some content",
    trigger: str = "pytest",
    resolution: str = "Use python -m pytest instead",
    hit_count: int = 1,
    created_at: str = "",
    last_hit_at: str = "",
) -> Lesson:
    return Lesson(
        id=id,
        scope=scope,
        category=category,
        title=title,
        content=content,
        trigger=trigger,
        resolution=resolution,
        hit_count=hit_count,
        created_at=created_at,
        last_hit_at=last_hit_at,
    )


@pytest.mark.asyncio
async def test_add_and_retrieve(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()

    lesson = _make_lesson(title="Add and retrieve")
    lesson_id = await store.add_lesson(lesson)

    assert lesson_id
    all_lessons = await store.all_lessons()
    assert len(all_lessons) == 1
    assert all_lessons[0].title == "Add and retrieve"
    assert all_lessons[0].id == lesson_id


@pytest.mark.asyncio
async def test_find_matching_by_trigger(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()

    await store.add_lesson(_make_lesson(trigger="pytest"))
    result = await store.find_matching("python -m pytest foo")
    assert result is not None
    assert result.trigger == "pytest"


@pytest.mark.asyncio
async def test_find_matching_no_match(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()

    await store.add_lesson(_make_lesson(trigger="pytest"))
    result = await store.find_matching("cargo build")
    assert result is None


@pytest.mark.asyncio
async def test_bump_hit(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()

    lesson = _make_lesson(title="Bump me")
    lesson_id = await store.add_lesson(lesson)

    await store.bump_hit(lesson_id)
    all_lessons = await store.all_lessons()
    assert all_lessons[0].hit_count == 2

    await store.bump_hit(lesson_id)
    all_lessons = await store.all_lessons()
    assert all_lessons[0].hit_count == 3


@pytest.mark.asyncio
async def test_get_relevant_lessons_respects_scope(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()

    await store.add_lesson(_make_lesson(id="g1", scope="global", title="Global one"))
    await store.add_lesson(_make_lesson(id="p1", scope="project", title="Project one"))

    global_lessons = await store.get_relevant_lessons(scope="global")
    assert len(global_lessons) == 1
    assert global_lessons[0].scope == "global"

    project_lessons = await store.get_relevant_lessons(scope="project")
    assert len(project_lessons) == 1
    assert project_lessons[0].scope == "project"

    all_lessons = await store.get_relevant_lessons()
    assert len(all_lessons) == 2


@pytest.mark.asyncio
async def test_get_relevant_lessons_respects_categories(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()

    await store.add_lesson(_make_lesson(id="cf1", category="command_failure", title="CF"))
    await store.add_lesson(_make_lesson(id="rf1", category="review_failure", title="RF"))
    await store.add_lesson(_make_lesson(id="cp1", category="code_pattern", title="CP"))

    result = await store.get_relevant_lessons(categories=["command_failure"])
    assert len(result) == 1
    assert result[0].category == "command_failure"

    result = await store.get_relevant_lessons(categories=["command_failure", "code_pattern"])
    assert len(result) == 2
    cats = {l.category for l in result}
    assert cats == {"command_failure", "code_pattern"}


@pytest.mark.asyncio
async def test_get_relevant_lessons_respects_token_budget(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()

    # Each lesson has ~300 chars in title+content+resolution
    for i in range(20):
        await store.add_lesson(
            _make_lesson(
                id=f"lesson-{i}",
                title=f"Lesson {i} " + "x" * 80,
                content="y" * 100,
                resolution="z" * 100,
            )
        )

    # With a tiny budget, we should get fewer than 20
    result = await store.get_relevant_lessons(max_tokens=100)
    assert len(result) < 20
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_get_relevant_lessons_ranking(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()

    await store.add_lesson(_make_lesson(id="low", title="Low hits", hit_count=1))
    await store.add_lesson(_make_lesson(id="high", title="High hits", hit_count=100))

    result = await store.get_relevant_lessons()
    assert len(result) == 2
    # Higher hit_count should come first (both have same last_hit_at ~ now)
    assert result[0].title == "High hits"
    assert result[1].title == "Low hits"


def test_format_lessons_block():
    lessons = [
        _make_lesson(category="command_failure", title="CF1", resolution="Fix CF1"),
        _make_lesson(category="review_failure", title="RF1", resolution="Fix RF1"),
        _make_lesson(category="command_failure", title="CF2", resolution="Fix CF2"),
    ]
    block = format_lessons_block(lessons)
    assert "## Lessons Learned" in block
    assert "### Command Failures" in block
    assert "### Review Patterns" in block
    assert "**CF1**" in block
    assert "**RF1**" in block
    assert "Fix CF1" in block


def test_format_lessons_block_empty():
    assert format_lessons_block([]) == ""


@pytest.mark.asyncio
async def test_initialize_idempotent(tmp_path):
    store = LessonStore(str(tmp_path / "test.db"))
    await store.initialize()
    await store.initialize()  # Should not raise

    # Still works after double init
    await store.add_lesson(_make_lesson(id="idem", title="Idempotent"))
    all_lessons = await store.all_lessons()
    assert len(all_lessons) == 1
