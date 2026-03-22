"""Forge learning system — self-evolving lessons from agent failures."""

from forge.learning.extractor import (
    classify_scope,
    extract_from_command_failures,
    extract_from_review_feedback,
)
from forge.learning.guard import FailureRecord, GuardTriggered, RuntimeGuard
from forge.learning.store import Lesson, format_lessons_block, row_to_lesson

__all__ = [
    "Lesson",
    "format_lessons_block",
    "row_to_lesson",
    "RuntimeGuard",
    "GuardTriggered",
    "FailureRecord",
    "extract_from_command_failures",
    "extract_from_review_feedback",
    "classify_scope",
]
