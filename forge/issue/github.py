"""GitHub CLI helpers for fetching issues and repo metadata."""

from __future__ import annotations

import json
import re
import subprocess

from forge.issue import GitHubIssue


def fetch_issue(number: int, repo: str | None = None) -> GitHubIssue:
    """Fetch a GitHub issue via the ``gh`` CLI.

    Parameters
    ----------
    number:
        GitHub issue number.
    repo:
        Optional repo in *name-with-owner* format (``org/repo``).
        When ``None``, ``gh`` uses the repo for the current directory.

    Returns
    -------
    GitHubIssue

    Raises
    ------
    FileNotFoundError
        ``gh`` binary not found on PATH.
    RuntimeError
        ``gh`` not authenticated **or** issue not found.
    ValueError
        ``gh`` output could not be parsed as JSON.
    """
    cmd = [
        "gh", "issue", "view", str(number),
        "--json", "title,body,comments,labels,assignees,milestone",
    ]
    if repo is not None:
        cmd += ["--repo", repo]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            "gh CLI is not installed or not found on PATH"
        )

    if result.returncode != 0:
        stderr = result.stderr.lower()
        if "auth" in stderr or "not authenticated" in stderr or "login" in stderr:
            raise RuntimeError(
                f"gh is not authenticated. Run `gh auth login`. stderr: {result.stderr.strip()}"
            )
        raise RuntimeError(
            f"Issue {number} not found or gh failed. stderr: {result.stderr.strip()}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Failed to parse gh JSON output: {exc}"
        ) from exc

    # Normalise labels from list-of-dicts to list-of-strings
    raw_labels = data.get("labels")
    labels: list[str] | None = None
    if raw_labels:
        labels = [lb["name"] if isinstance(lb, dict) else str(lb) for lb in raw_labels]

    # Normalise assignees from list-of-dicts to list-of-strings
    raw_assignees = data.get("assignees")
    assignees: list[str] | None = None
    if raw_assignees:
        assignees = [a["login"] if isinstance(a, dict) else str(a) for a in raw_assignees]

    # Normalise milestone
    raw_milestone = data.get("milestone")
    milestone: str | None = None
    if raw_milestone:
        milestone = raw_milestone.get("title") if isinstance(raw_milestone, dict) else str(raw_milestone)

    # Comments — keep as list[IssueComment] or None
    raw_comments = data.get("comments")
    comments = raw_comments if raw_comments else None

    return GitHubIssue(
        number=number,
        title=data.get("title", ""),
        body=data.get("body") or None,
        comments=comments,
        labels=labels or None,
        assignees=assignees or None,
        milestone=milestone,
        repo_url=None,
    )


def check_gh_auth() -> bool:
    """Check whether the ``gh`` CLI is authenticated.

    Returns ``True`` if authenticated, ``False`` otherwise.
    Raises ``FileNotFoundError`` if ``gh`` is not installed.
    """
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        raise FileNotFoundError("gh CLI is not installed or not found on PATH")
    return result.returncode == 0


def get_current_repo() -> str | None:
    """Get the current repository's *name-with-owner* via ``gh``.

    Returns ``'org/repo'`` on success, ``None`` on any failure.
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("nameWithOwner")
    except Exception:
        return None


def slugify_title(title: str, max_len: int = 50) -> str:
    """Convert an issue title to a branch-name-safe slug.

    - Lowercase
    - Replace non-alphanumeric characters with hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    - Truncate at word boundary (hyphen) respecting *max_len*

    Example::

        >>> slugify_title("Login Returns 500 on Expired Token")
        'login-returns-500-on-expired-token'
    """
    if not title:
        return ""

    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")

    if len(slug) <= max_len:
        return slug

    # Truncate at last hyphen within max_len
    truncated = slug[:max_len]
    last_hyphen = truncated.rfind("-")
    if last_hyphen > 0:
        return truncated[:last_hyphen]
    return truncated
