"""Tests for forge.issue package — models, parsing, and GitHub helpers."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from forge.issue import GitHubIssue, parse_issue_ref
from forge.issue.github import (
    check_gh_auth,
    fetch_issue,
    get_current_repo,
    slugify_title,
)

# ---------------------------------------------------------------------------
# parse_issue_ref
# ---------------------------------------------------------------------------


class TestParseIssueRef:
    def test_bare_number(self):
        assert parse_issue_ref("42") == (42, None)

    def test_bare_number_with_whitespace(self):
        assert parse_issue_ref("  7  ") == (7, None)

    def test_full_url(self):
        url = "https://github.com/org/repo/issues/42"
        assert parse_issue_ref(url) == (42, "org/repo")

    def test_url_trailing_slash(self):
        url = "https://github.com/org/repo/issues/42/"
        assert parse_issue_ref(url) == (42, "org/repo")

    def test_url_different_org_repo(self):
        url = "https://github.com/my-org/my-repo/issues/123"
        assert parse_issue_ref(url) == (123, "my-org/my-repo")

    def test_invalid_string(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_issue_ref("not-an-issue")

    def test_invalid_url_missing_issues(self):
        with pytest.raises(ValueError):
            parse_issue_ref("https://github.com/org/repo/pull/42")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_issue_ref("")


# ---------------------------------------------------------------------------
# slugify_title
# ---------------------------------------------------------------------------


class TestSlugifyTitle:
    def test_normal_title(self):
        assert (
            slugify_title("Login Returns 500 on Expired Token")
            == "login-returns-500-on-expired-token"
        )

    def test_special_chars(self):
        assert slugify_title("Fix: user's email (validation)!") == "fix-user-s-email-validation"

    def test_long_title_truncation(self):
        slug = slugify_title(
            "this is a very long title that should be truncated somewhere", max_len=30
        )
        assert len(slug) <= 30
        assert not slug.endswith("-")

    def test_empty_string(self):
        assert slugify_title("") == ""

    def test_max_len_exact(self):
        slug = slugify_title("ab cd", max_len=5)
        assert slug == "ab-cd"

    def test_collapses_hyphens(self):
        assert slugify_title("a --- b") == "a-b"


# ---------------------------------------------------------------------------
# fetch_issue (mocked subprocess)
# ---------------------------------------------------------------------------

_ISSUE_JSON = {
    "title": "Login broken",
    "body": "Steps to reproduce...",
    "comments": [{"author": {"login": "alice"}, "body": "I can repro this."}],
    "labels": [{"name": "bug"}],
    "assignees": [{"login": "bob"}],
    "milestone": {"title": "v1.0"},
}


def _ok_result(data: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["gh"],
        returncode=0,
        stdout=json.dumps(data or _ISSUE_JSON),
        stderr="",
    )


def _err_result(stderr: str = "", code: int = 1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["gh"],
        returncode=code,
        stdout="",
        stderr=stderr,
    )


class TestFetchIssue:
    @patch("forge.issue.github.subprocess.run", return_value=_ok_result())
    def test_success(self, mock_run):
        issue = fetch_issue(42)
        assert isinstance(issue, GitHubIssue)
        assert issue.number == 42
        assert issue.title == "Login broken"
        assert issue.body == "Steps to reproduce..."
        assert issue.labels == ["bug"]
        assert issue.assignees == ["bob"]
        assert issue.milestone == "v1.0"
        assert issue.comments is not None
        assert len(issue.comments) == 1
        assert issue.comments[0]["author"]["login"] == "alice"

    @patch("forge.issue.github.subprocess.run", return_value=_ok_result())
    def test_passes_repo_flag(self, mock_run):
        fetch_issue(10, repo="org/repo")
        cmd = mock_run.call_args[0][0]
        assert "--repo" in cmd
        assert "org/repo" in cmd

    @patch("forge.issue.github.subprocess.run", return_value=_err_result("issue 999 not found"))
    def test_not_found(self, mock_run):
        with pytest.raises(RuntimeError, match="not found"):
            fetch_issue(999)

    @patch("forge.issue.github.subprocess.run", return_value=_err_result("not authenticated"))
    def test_not_authenticated(self, mock_run):
        with pytest.raises(RuntimeError, match="not authenticated"):
            fetch_issue(1)

    @patch("forge.issue.github.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_installed(self, mock_run):
        with pytest.raises(FileNotFoundError):
            fetch_issue(1)

    @patch(
        "forge.issue.github.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30),
    )
    def test_timeout(self, mock_run):
        with pytest.raises(subprocess.TimeoutExpired):
            fetch_issue(1)

    @patch(
        "forge.issue.github.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout="NOT JSON", stderr=""
        ),
    )
    def test_bad_json(self, mock_run):
        with pytest.raises(ValueError, match="parse"):
            fetch_issue(1)


# ---------------------------------------------------------------------------
# check_gh_auth (mocked subprocess)
# ---------------------------------------------------------------------------


class TestCheckGhAuth:
    @patch(
        "forge.issue.github.subprocess.run",
        return_value=subprocess.CompletedProcess(args=["gh"], returncode=0, stdout="", stderr=""),
    )
    def test_authenticated(self, mock_run):
        assert check_gh_auth() is True

    @patch(
        "forge.issue.github.subprocess.run",
        return_value=subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr=""),
    )
    def test_not_authenticated(self, mock_run):
        assert check_gh_auth() is False

    @patch("forge.issue.github.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_installed(self, mock_run):
        with pytest.raises(FileNotFoundError):
            check_gh_auth()


# ---------------------------------------------------------------------------
# get_current_repo (mocked subprocess)
# ---------------------------------------------------------------------------


class TestGetCurrentRepo:
    @patch(
        "forge.issue.github.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout=json.dumps({"nameWithOwner": "org/repo"}), stderr=""
        ),
    )
    def test_success(self, mock_run):
        assert get_current_repo() == "org/repo"

    @patch(
        "forge.issue.github.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="not a repo"
        ),
    )
    def test_not_in_repo(self, mock_run):
        assert get_current_repo() is None

    @patch("forge.issue.github.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_not_installed(self, mock_run):
        assert get_current_repo() is None
