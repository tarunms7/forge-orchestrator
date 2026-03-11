from __future__ import annotations


from forge.issue import GitHubIssue
from forge.issue.prompt import compose_prompt


def _make_comment(login: str, body: str) -> dict:
    return {"author": {"login": login}, "body": body}


def test_full_prompt_all_fields():
    issue = GitHubIssue(
        number=42,
        title="Fix the bug",
        body="This is a bug description.",
        comments=[_make_comment("alice", "Can reproduce."), _make_comment("bob", "Working on it.")],
        labels=["bug", "priority-high"],
        assignees=["alice"],
        milestone="v1.0",
        repo_url="https://github.com/org/repo",
    )
    result = compose_prompt(issue)

    assert result.startswith("Fix GitHub Issue #42: Fix the bug")
    assert "## Issue Description" in result
    assert "This is a bug description." in result
    assert "## Comments" in result
    assert "**@alice**: Can reproduce." in result
    assert "**@bob**: Working on it." in result
    assert "## Labels" in result
    assert "bug, priority-high" in result
    assert "## Acceptance Criteria" in result
    assert "- The fix should address the issue described above" in result
    assert "- All existing tests must pass" in result
    assert "- Add or update tests to cover the fix" in result


def test_minimal_prompt_title_only():
    issue = GitHubIssue(
        number=1,
        title="Minimal issue",
        body=None,
        comments=None,
        labels=None,
        assignees=None,
        milestone=None,
        repo_url=None,
    )
    result = compose_prompt(issue)

    assert result.startswith("Fix GitHub Issue #1: Minimal issue")
    assert "No description provided." in result
    assert "No comments." in result
    assert "## Labels" in result
    assert "None" in result
    assert "## Acceptance Criteria" in result


def test_prompt_with_comments_no_body():
    issue = GitHubIssue(
        number=7,
        title="No body but has comments",
        body=None,
        comments=[_make_comment("carol", "Here is info.")],
        labels=None,
        assignees=None,
        milestone=None,
        repo_url=None,
    )
    result = compose_prompt(issue)

    assert "No description provided." in result
    assert "**@carol**: Here is info." in result
    assert "No comments." not in result


def test_prompt_with_labels_no_comments():
    issue = GitHubIssue(
        number=10,
        title="Has labels no comments",
        body="Some body.",
        comments=None,
        labels=["enhancement"],
        assignees=None,
        milestone=None,
        repo_url=None,
    )
    result = compose_prompt(issue)

    assert "No comments." in result
    assert "enhancement" in result


def test_acceptance_criteria_always_present():
    for body, comments, labels in [
        (None, None, None),
        ("body text", [_make_comment("x", "y")], ["bug"]),
        ("body", [], []),
    ]:
        issue = GitHubIssue(
            number=99,
            title="Test AC",
            body=body,
            comments=comments,
            labels=labels,
            assignees=None,
            milestone=None,
            repo_url=None,
        )
        result = compose_prompt(issue)
        assert "## Acceptance Criteria" in result
        assert "- The fix should address the issue described above" in result
        assert "- All existing tests must pass" in result
        assert "- Add or update tests to cover the fix" in result


def test_comment_formatting_multiple():
    comments = [
        _make_comment("user1", "First comment."),
        _make_comment("user2", "Second comment."),
        _make_comment("user3", "Third comment."),
    ]
    issue = GitHubIssue(
        number=5,
        title="Multi-comment issue",
        body=None,
        comments=comments,
        labels=None,
        assignees=None,
        milestone=None,
        repo_url=None,
    )
    result = compose_prompt(issue)

    assert "**@user1**: First comment." in result
    assert "**@user2**: Second comment." in result
    assert "**@user3**: Third comment." in result


def test_empty_comments_list_shows_no_comments():
    issue = GitHubIssue(
        number=3,
        title="Empty comments list",
        body="Some body.",
        comments=[],
        labels=None,
        assignees=None,
        milestone=None,
        repo_url=None,
    )
    result = compose_prompt(issue)
    assert "No comments." in result


def test_empty_labels_list_shows_none():
    issue = GitHubIssue(
        number=4,
        title="Empty labels list",
        body=None,
        comments=None,
        labels=[],
        assignees=None,
        milestone=None,
        repo_url=None,
    )
    result = compose_prompt(issue)
    assert "## Labels" in result
    assert "None" in result
