"""Input validation and text sanitization utilities.

Provides:
- Path traversal / injection prevention for task and repo IDs.
- JSON extraction from mixed text (LLM responses with markdown fences, prose, etc.).
"""

from __future__ import annotations

import re

_TASK_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_REPO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class UnsafeInputError(ValueError):
    """Raised when a task_id or repo_id fails validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def validate_task_id(task_id: str) -> str:
    """Validate and return a task_id string.

    Raises:
        UnsafeInputError: if *task_id* is empty, contains path traversal
            sequences, has forbidden characters, or doesn't match _TASK_ID_RE.
    """
    if not task_id:
        raise UnsafeInputError("task_id must not be empty")
    if ".." in task_id or "/" in task_id or "\\" in task_id:
        raise UnsafeInputError(f"task_id contains path traversal sequence: {task_id!r}")
    if not _TASK_ID_RE.match(task_id):
        raise UnsafeInputError(
            f"task_id contains invalid characters or is too long (max 64): {task_id!r}"
        )
    return task_id


def validate_repo_id(repo_id: str) -> str:
    """Validate and return a repo_id string.

    Raises:
        UnsafeInputError: if *repo_id* is empty, contains path traversal
            sequences, has forbidden characters, or doesn't match _REPO_ID_RE.
    """
    if not repo_id:
        raise UnsafeInputError("repo_id must not be empty")
    if ".." in repo_id or "/" in repo_id or "\\" in repo_id:
        raise UnsafeInputError(f"repo_id contains path traversal sequence: {repo_id!r}")
    if not _REPO_ID_RE.match(repo_id):
        raise UnsafeInputError(f"repo_id contains invalid characters or uppercase: {repo_id!r}")
    return repo_id


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def extract_json_block(text: str) -> str | None:
    """Extract the outermost JSON object from *text*.

    Handles three common LLM output patterns:
    1. JSON inside markdown fences (```json ... ```)
    2. Bare JSON with surrounding prose
    3. No JSON at all (returns ``None``)

    Uses a string-aware brace counter so nested braces inside JSON strings
    don't break extraction.
    """
    text = text.strip()
    if not text:
        return None

    # 1) Try markdown fences first
    match = _FENCED_JSON_RE.search(text)
    if match:
        return match.group(1)

    # 2) Find first '{' then use brace-counter to find matching '}'
    start = text.find("{")
    if start == -1:
        return None

    brace_depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                return text[start : i + 1]

    # Fallback: unbalanced braces — use rfind
    end = text.rfind("}")
    if end != -1:
        return text[start : end + 1]
    return None
