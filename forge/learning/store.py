"""Lesson storage — thin wrapper around the central Database.

All lessons live in the central forge.db. The `scope` and `project_dir`
columns distinguish global vs project-scoped lessons.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger("forge.learning")


@dataclass
class Lesson:
    """Lesson data transfer object."""
    id: str
    scope: str  # 'global' or 'project'
    category: str  # 'command_failure', 'review_failure', 'code_pattern'
    title: str
    content: str
    trigger: str
    resolution: str
    hit_count: int = 1
    created_at: str = ""
    last_hit_at: str = ""
    project_dir: str | None = None


def row_to_lesson(row) -> Lesson:
    """Convert a LessonRow (SQLAlchemy) to a Lesson dataclass."""
    return Lesson(
        id=row.id,
        scope=row.scope,
        category=row.category,
        title=row.title,
        content=row.content,
        trigger=row.trigger,
        resolution=row.resolution,
        hit_count=row.hit_count,
        created_at=getattr(row, "created_at", ""),
        last_hit_at=getattr(row, "last_hit_at", ""),
        project_dir=getattr(row, "project_dir", None),
    )


def format_lessons_block(lessons: list[Lesson]) -> str:
    """Format lessons into a prompt section for agent injection."""
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
