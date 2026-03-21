"""Persistent lesson storage — SQLite via aiosqlite."""

import aiosqlite
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("forge.learning")

@dataclass
class Lesson:
    id: str
    scope: str  # 'global' or 'project'
    category: str  # 'command_failure', 'review_failure', 'code_pattern'
    title: str
    content: str
    trigger: str  # what triggers this lesson (command pattern, error pattern)
    resolution: str  # what to do instead
    hit_count: int = 1
    created_at: str = ""
    last_hit_at: str = ""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL,
    category    TEXT NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    trigger     TEXT NOT NULL,
    resolution  TEXT NOT NULL,
    hit_count   INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL,
    last_hit_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lessons_scope ON lessons(scope);
CREATE INDEX IF NOT EXISTS idx_lessons_category ON lessons(category);
CREATE INDEX IF NOT EXISTS idx_lessons_trigger ON lessons(trigger);
"""

class LessonStore:
    """Async SQLite store for lessons learned."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        if self._initialized:
            return
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.executescript(_SCHEMA)
            await conn.commit()
        self._initialized = True

    async def add_lesson(self, lesson: Lesson) -> str:
        """Add a new lesson. Returns the lesson ID."""
        await self.initialize()
        if not lesson.id:
            lesson.id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        if not lesson.created_at:
            lesson.created_at = now
        if not lesson.last_hit_at:
            lesson.last_hit_at = now
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "INSERT INTO lessons (id, scope, category, title, content, trigger, resolution, hit_count, created_at, last_hit_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lesson.id, lesson.scope, lesson.category, lesson.title, lesson.content, lesson.trigger, lesson.resolution, lesson.hit_count, lesson.created_at, lesson.last_hit_at),
            )
            await conn.commit()
        return lesson.id

    async def find_matching(self, trigger: str) -> Lesson | None:
        """Find a lesson with a matching trigger pattern.

        Uses substring matching — if the stored trigger appears in the given trigger
        string, or vice versa, it's a match.
        """
        await self.initialize()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            # Check both directions: stored trigger in query, or query in stored trigger
            cursor = await conn.execute(
                "SELECT * FROM lessons WHERE ? LIKE '%' || trigger || '%' OR trigger LIKE '%' || ? || '%' ORDER BY hit_count DESC LIMIT 1",
                (trigger, trigger),
            )
            row = await cursor.fetchone()
            if row:
                return _row_to_lesson(row)
        return None

    async def bump_hit(self, lesson_id: str) -> None:
        """Increment hit_count and update last_hit_at."""
        await self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE lessons SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
                (now, lesson_id),
            )
            await conn.commit()

    async def get_relevant_lessons(
        self,
        scope: str | None = None,
        categories: list[str] | None = None,
        max_count: int = 20,
        max_tokens: int = 2000,
    ) -> list[Lesson]:
        """Get the most relevant lessons, ranked by recency-weighted hit count.

        Score = hit_count / (1 + days_since_last_hit / 30)

        Args:
            scope: Filter by 'global' or 'project'. None = both.
            categories: Filter by category list. None = all.
            max_count: Maximum number of lessons to return.
            max_tokens: Approximate token budget (rough: 1 token ~ 4 chars).
        """
        await self.initialize()
        where_clauses = []
        params: list = []
        if scope:
            where_clauses.append("scope = ?")
            params.append(scope)
        if categories:
            placeholders = ",".join("?" for _ in categories)
            where_clauses.append(f"category IN ({placeholders})")
            params.extend(categories)

        where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # SQLite can compute the score directly
        query = f"""
            SELECT *,
                   hit_count * 1.0 / (1.0 + (julianday('now') - julianday(last_hit_at)) / 30.0) AS score
            FROM lessons
            {where}
            ORDER BY score DESC
            LIMIT ?
        """
        params.append(max_count)

        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()

        lessons = []
        total_chars = 0
        char_budget = max_tokens * 4  # rough token estimate
        for row in rows:
            lesson = _row_to_lesson(row)
            lesson_chars = len(lesson.title) + len(lesson.content) + len(lesson.resolution)
            if total_chars + lesson_chars > char_budget:
                break
            lessons.append(lesson)
            total_chars += lesson_chars

        return lessons

    async def all_lessons(self) -> list[Lesson]:
        """Return all lessons (for debugging/admin)."""
        await self.initialize()
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT * FROM lessons ORDER BY hit_count DESC")
            rows = await cursor.fetchall()
        return [_row_to_lesson(row) for row in rows]


def _row_to_lesson(row) -> Lesson:
    return Lesson(
        id=row["id"],
        scope=row["scope"],
        category=row["category"],
        title=row["title"],
        content=row["content"],
        trigger=row["trigger"],
        resolution=row["resolution"],
        hit_count=row["hit_count"],
        created_at=row["created_at"],
        last_hit_at=row["last_hit_at"],
    )


def format_lessons_block(lessons: list[Lesson]) -> str:
    """Format lessons into a prompt section for agent injection.

    Groups by category, max ~2000 tokens.
    """
    if not lessons:
        return ""

    by_category: dict[str, list[Lesson]] = {}
    for lesson in lessons:
        by_category.setdefault(lesson.category, []).append(lesson)

    category_titles = {
        "command_failure": "Command Failures",
        "review_failure": "Review Patterns",
        "code_pattern": "Code Patterns",
    }

    lines = ["## Lessons Learned (DO NOT repeat these mistakes)\n"]
    for cat, cat_lessons in by_category.items():
        title = category_titles.get(cat, cat.replace("_", " ").title())
        lines.append(f"### {title}")
        for lesson in cat_lessons:
            lines.append(f"- **{lesson.title}**: {lesson.resolution}")
        lines.append("")

    return "\n".join(lines)
