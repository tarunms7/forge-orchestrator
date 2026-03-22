"""Input validation for task and repo identifiers used in path/branch construction.

Prevents path traversal and injection attacks by validating IDs against strict
regex patterns before they are used to build filesystem paths or git branch names.
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
