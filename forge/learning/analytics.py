"""Learning system analytics — surface which lessons are most impactful."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge.storage.db import Database

logger = logging.getLogger("forge.learning")


async def get_lesson_effectiveness(
    db: Database, project_dir: str | None = None
) -> list[dict]:
    """Return lessons sorted by hit_count descending with effectiveness fields.

    Each dict contains: id, title, category, hit_count, confidence, scope, last_hit_at.
    If project_dir is given, only lessons matching that project (or global) are returned.
    """
    rows = await db.list_all_lessons()  # already sorted by hit_count desc

    if project_dir is not None:
        rows = [
            r
            for r in rows
            if r.scope == "global" or getattr(r, "project_dir", None) == project_dir
        ]

    return [
        {
            "id": r.id,
            "title": r.title,
            "category": r.category,
            "hit_count": r.hit_count,
            "confidence": r.confidence,
            "scope": r.scope,
            "last_hit_at": getattr(r, "last_hit_at", ""),
        }
        for r in rows
    ]


async def get_retry_prevention_estimate(
    db: Database, project_dir: str | None = None
) -> dict:
    """Estimate retries prevented by the learning system.

    For each lesson with hit_count > 1 and confidence >= 0.6 (active lesson),
    estimated retries prevented = (hit_count - 1) * confidence.

    Returns RetryPreventionEstimate dict.
    """
    lessons = await get_lesson_effectiveness(db, project_dir=project_dir)
    total_lessons = len(lessons)

    active = [le for le in lessons if le["hit_count"] > 1 and le["confidence"] >= 0.6]
    active_lessons = len(active)

    estimated_retries_prevented = sum(
        (le["hit_count"] - 1) * le["confidence"] for le in active
    )

    # Build top lessons sorted by estimated retries prevented descending
    for le in active:
        le["estimated_prevented"] = (le["hit_count"] - 1) * le["confidence"]
    top_lessons = sorted(
        active, key=lambda x: x["estimated_prevented"], reverse=True
    )[:10]
    # Remove temp key
    for le in top_lessons:
        le.pop("estimated_prevented", None)

    return {
        "total_lessons": total_lessons,
        "active_lessons": active_lessons,
        "estimated_retries_prevented": estimated_retries_prevented,
        "top_lessons": top_lessons,
    }


async def get_lesson_category_breakdown(
    db: Database, project_dir: str | None = None
) -> dict[str, int]:
    """Count lessons per category. Only categories with count > 0 are included."""
    lessons = await get_lesson_effectiveness(db, project_dir=project_dir)

    breakdown: dict[str, int] = {}
    for le in lessons:
        cat = le["category"]
        breakdown[cat] = breakdown.get(cat, 0) + 1

    return breakdown


def format_lesson_stats(lessons_data: list[dict]) -> str:
    """Return Rich-compatible markup string showing lesson effectiveness table.

    Importable by stats CLI for --lessons flag rendering.
    """
    if not lessons_data:
        return "[dim]No lessons found.[/dim]"

    lines: list[str] = []
    lines.append("[bold]Lesson Effectiveness[/bold]")
    lines.append(
        "┌──────────────────────────────────┬──────────────────┬──────┬────────────┬──────────────────┐"
    )
    lines.append(
        "│ Title                            │ Category         │ Hits │ Confidence │ Est. Prevented   │"
    )
    lines.append(
        "├──────────────────────────────────┼──────────────────┼──────┼────────────┼──────────────────┤"
    )

    active_count = 0
    total_prevented = 0.0

    for lesson in lessons_data:
        hit_count = lesson["hit_count"]
        confidence = lesson["confidence"]

        if hit_count > 1 and confidence >= 0.6:
            prevented = (hit_count - 1) * confidence
            active_count += 1
            total_prevented += prevented
        else:
            prevented = 0.0

        title = lesson["title"][:32].ljust(32)
        category = lesson["category"][:16].ljust(16)
        hits = str(hit_count).rjust(4)
        conf = f"{confidence:.2f}".rjust(10)
        prev = f"{prevented:.1f}".rjust(16)

        lines.append(f"│ {title} │ {category} │ {hits} │ {conf} │ {prev} │")

    lines.append(
        "└──────────────────────────────────┴──────────────────┴──────┴────────────┴──────────────────┘"
    )

    lines.append(
        f"\nEstimated {total_prevented:.0f} retries prevented by {active_count} active lessons"
    )

    return "\n".join(lines)
