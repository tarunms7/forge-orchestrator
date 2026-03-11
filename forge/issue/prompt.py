from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IssueComment:
    """Shape of each comment in GitHubIssue.comments."""
    author: dict  # {"login": "username"}
    body: str


@dataclass
class GitHubIssue:
    """GitHub issue data model."""
    number: int
    title: str
    body: str | None = None
    comments: list[dict] | None = None  # list of {"author": {"login": ...}, "body": ...}
    labels: list[str] | None = None
    assignees: list[str] | None = None
    milestone: str | None = None
    repo_url: str | None = None


def compose_prompt(issue: GitHubIssue) -> str:
    """Build a structured Forge task prompt from a GitHubIssue."""
    lines: list[str] = []

    # Title line
    lines.append(f"Fix GitHub Issue #{issue.number}: {issue.title}")
    lines.append("")

    # Issue Description
    lines.append("## Issue Description")
    lines.append(issue.body if issue.body else "No description provided.")
    lines.append("")

    # Comments
    lines.append("## Comments")
    if issue.comments:
        for comment in issue.comments:
            login = comment["author"]["login"]
            body = comment["body"]
            lines.append(f"**@{login}**: {body}")
    else:
        lines.append("No comments.")
    lines.append("")

    # Labels
    lines.append("## Labels")
    if issue.labels:
        lines.append(", ".join(issue.labels))
    else:
        lines.append("None")
    lines.append("")

    # Acceptance Criteria
    lines.append("## Acceptance Criteria")
    lines.append("- The fix should address the issue described above")
    lines.append("- All existing tests must pass")
    lines.append("- Add or update tests to cover the fix")

    return "\n".join(lines)
