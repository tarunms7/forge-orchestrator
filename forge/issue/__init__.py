"""Forge issue package — GitHub issue data models and parsing utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TypedDict


class IssueComment(TypedDict):
    """Shape of each comment dict. Matches gh CLI JSON output."""

    author: dict  # {"login": "username"}
    body: str


@dataclass
class GitHubIssue:
    """GitHub issue data model used across the issue pipeline."""

    number: int
    title: str
    body: str | None = None
    comments: list[IssueComment] | None = None
    labels: list[str] | None = None
    assignees: list[str] | None = None
    milestone: str | None = None
    repo_url: str | None = None


_GITHUB_ISSUE_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+/[^/]+)/issues/(\d+)/?$"
)


def parse_issue_ref(ref: str) -> tuple[int, str | None]:
    """Parse an issue reference string into (number, repo_nwo_or_None).

    Accepts:
      - Bare issue number as string: '42'
      - Full GitHub URL: 'https://github.com/org/repo/issues/42'

    Returns:
      (issue_number, repo_name_with_owner_or_None)

    Raises:
      ValueError: If the input cannot be parsed.
    """
    ref = ref.strip()

    # Try bare integer first
    if ref.isdigit():
        num = int(ref)
        if num <= 0:
            raise ValueError(f"Issue number must be positive, got {num}")
        return (num, None)

    # Try GitHub URL
    m = _GITHUB_ISSUE_URL_RE.match(ref)
    if m:
        repo_nwo = m.group(1)
        num = int(m.group(2))
        return (num, repo_nwo)

    raise ValueError(
        f"Cannot parse issue reference: {ref!r}. "
        "Expected a bare issue number or https://github.com/org/repo/issues/N"
    )
