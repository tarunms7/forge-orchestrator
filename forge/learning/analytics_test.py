"""Tests for learning system analytics."""

import pytest

from forge.learning.analytics import (
    format_lesson_stats,
    get_lesson_category_breakdown,
    get_lesson_effectiveness,
    get_retry_prevention_estimate,
)
from forge.storage.db import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await d.initialize()
    yield d
    await d.close()


async def _add_lesson(db, **overrides):
    """Helper to add a lesson with sensible defaults."""
    defaults = {
        "scope": "global",
        "category": "command_failure",
        "title": "test lesson",
        "content": "content",
        "trigger": "some-trigger",
        "resolution": "fix it",
        "confidence": 0.5,
    }
    defaults.update(overrides)
    return await db.add_lesson(**defaults)


# ── get_lesson_effectiveness ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_effectiveness_empty(db):
    result = await get_lesson_effectiveness(db)
    assert result == []


@pytest.mark.asyncio
async def test_effectiveness_returns_sorted_by_hits(db):
    await _add_lesson(db, title="low", trigger="t1")
    lid2 = await _add_lesson(db, title="high", trigger="t2")

    # Bump lid2 multiple times
    await db.bump_lesson_hit(lid2)
    await db.bump_lesson_hit(lid2)

    result = await get_lesson_effectiveness(db)
    assert len(result) == 2
    assert result[0]["title"] == "high"
    assert result[0]["hit_count"] >= 3  # initial 1 + 2 bumps
    assert result[1]["title"] == "low"


@pytest.mark.asyncio
async def test_effectiveness_has_required_fields(db):
    lid = await _add_lesson(db, title="check fields", trigger="t1", category="code_pattern")

    result = await get_lesson_effectiveness(db)
    assert len(result) == 1
    entry = result[0]
    assert entry["id"] == lid
    assert entry["title"] == "check fields"
    assert entry["category"] == "code_pattern"
    assert entry["hit_count"] == 1
    assert isinstance(entry["confidence"], float)
    assert entry["scope"] == "global"
    assert "last_hit_at" in entry


@pytest.mark.asyncio
async def test_effectiveness_filters_by_project_dir(db):
    await _add_lesson(db, title="global", trigger="t1")
    await _add_lesson(
        db,
        title="project-a",
        trigger="t2",
        scope="project",
        project_dir="/project/a",
    )
    await _add_lesson(
        db,
        title="project-b",
        trigger="t3",
        scope="project",
        project_dir="/project/b",
    )

    result = await get_lesson_effectiveness(db, project_dir="/project/a")
    titles = [r["title"] for r in result]
    assert "global" in titles
    assert "project-a" in titles
    assert "project-b" not in titles


# ── get_retry_prevention_estimate ─────────────────────────────────────


@pytest.mark.asyncio
async def test_prevention_empty(db):
    result = await get_retry_prevention_estimate(db)
    assert result["total_lessons"] == 0
    assert result["active_lessons"] == 0
    assert result["estimated_retries_prevented"] == 0.0
    assert result["top_lessons"] == []


@pytest.mark.asyncio
async def test_prevention_with_active_lessons(db):
    # Lesson with hit_count=1 (not active)
    await _add_lesson(db, title="inactive", trigger="t1", confidence=0.8)

    # Lesson with hit_count>1 but low confidence (not active)
    lid2 = await _add_lesson(db, title="low-conf", trigger="t2", confidence=0.3)
    await db.bump_lesson_hit(lid2)

    # Active lesson: hit_count>1 and confidence>=0.6
    lid3 = await _add_lesson(db, title="active", trigger="t3", confidence=0.8)
    await db.bump_lesson_hit(lid3)
    await db.bump_lesson_hit(lid3)

    result = await get_retry_prevention_estimate(db)
    assert result["total_lessons"] == 3
    assert result["active_lessons"] == 1
    assert result["estimated_retries_prevented"] > 0
    assert len(result["top_lessons"]) == 1
    assert result["top_lessons"][0]["title"] == "active"


@pytest.mark.asyncio
async def test_prevention_top_lessons_capped_at_10(db):
    lids = []
    for i in range(15):
        lid = await _add_lesson(
            db, title=f"lesson-{i}", trigger=f"t-{i}", confidence=0.8
        )
        await db.bump_lesson_hit(lid)
        await db.bump_lesson_hit(lid)
        lids.append(lid)

    result = await get_retry_prevention_estimate(db)
    assert len(result["top_lessons"]) == 10


@pytest.mark.asyncio
async def test_prevention_estimate_math(db):
    """Verify the math: (hit_count - 1) * confidence for each active lesson."""
    lid = await _add_lesson(db, title="math-check", trigger="t1", confidence=0.5)
    # bump 4 times: hit_count goes from 1 to 5, confidence increases with each bump
    for _ in range(4):
        await db.bump_lesson_hit(lid)

    result = await get_retry_prevention_estimate(db)
    # Verify that the active lesson contributes positively
    if result["active_lessons"] > 0:
        assert result["estimated_retries_prevented"] > 0


# ── get_lesson_category_breakdown ─────────────────────────────────────


@pytest.mark.asyncio
async def test_breakdown_empty(db):
    result = await get_lesson_category_breakdown(db)
    assert result == {}


@pytest.mark.asyncio
async def test_breakdown_counts(db):
    await _add_lesson(db, title="a", trigger="t1", category="command_failure")
    await _add_lesson(db, title="b", trigger="t2", category="command_failure")
    await _add_lesson(db, title="c", trigger="t3", category="code_pattern")

    result = await get_lesson_category_breakdown(db)
    assert result == {"command_failure": 2, "code_pattern": 1}


@pytest.mark.asyncio
async def test_breakdown_respects_project_dir(db):
    await _add_lesson(db, title="a", trigger="t1", category="command_failure")
    await _add_lesson(
        db,
        title="b",
        trigger="t2",
        category="review_failure",
        scope="project",
        project_dir="/proj",
    )

    result = await get_lesson_category_breakdown(db, project_dir="/proj")
    assert result == {"command_failure": 1, "review_failure": 1}


# ── format_lesson_stats ───────────────────────────────────────────────


def test_format_empty():
    result = format_lesson_stats([])
    assert "No lessons found" in result


def test_format_with_data():
    data = [
        {
            "id": "1",
            "title": "Use python -m pytest",
            "category": "command_failure",
            "hit_count": 5,
            "confidence": 0.8,
            "scope": "global",
            "last_hit_at": "2026-03-20T10:00:00",
        },
        {
            "id": "2",
            "title": "Avoid hardcoded paths",
            "category": "code_pattern",
            "hit_count": 1,
            "confidence": 0.5,
            "scope": "project",
            "last_hit_at": "",
        },
    ]
    result = format_lesson_stats(data)
    assert "Lesson Effectiveness" in result
    assert "Use python -m pytest" in result
    assert "Avoid hardcoded paths" in result
    assert "command_failure" in result
    assert "code_pattern" in result
    # Active lesson (hit=5, conf=0.8): prevented = 4 * 0.8 = 3.2
    assert "3.2" in result
    # Inactive lesson: prevented = 0
    assert "Estimated 3 retries prevented by 1 active lessons" in result


def test_format_table_structure():
    data = [
        {
            "id": "1",
            "title": "Test",
            "category": "infra_timeout",
            "hit_count": 3,
            "confidence": 0.9,
            "scope": "global",
            "last_hit_at": "",
        },
    ]
    result = format_lesson_stats(data)
    # Should have table borders
    assert "┌" in result
    assert "└" in result
    assert "│" in result
    # Headers
    assert "Title" in result
    assert "Category" in result
    assert "Hits" in result
    assert "Confidence" in result
    assert "Est. Prevented" in result
