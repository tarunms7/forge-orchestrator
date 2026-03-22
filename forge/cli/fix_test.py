"""Tests for forge fix CLI command."""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from forge.cli.fix import fix
from forge.issue import GitHubIssue


@pytest.fixture()
def runner():
    return CliRunner()


def _make_issue(number: int = 42, title: str = "Login Returns 500 on Expired Token") -> GitHubIssue:
    return GitHubIssue(
        number=number,
        title=title,
        body="The login endpoint returns 500 when the token is expired.",
    )


_GIT_REPO_OK = subprocess.CompletedProcess(
    args=["git", "rev-parse", "--is-inside-work-tree"],
    returncode=0,
    stdout="true\n",
    stderr="",
)
_GIT_BRANCH_OK = subprocess.CompletedProcess(
    args=["git", "checkout", "-b", "fix/42-login-returns-500-on-expired-token"],
    returncode=0,
    stdout="",
    stderr="",
)
_GH_PR_OK = subprocess.CompletedProcess(
    args=["gh", "pr", "create"],
    returncode=0,
    stdout="https://github.com/org/repo/pull/1\n",
    stderr="",
)

PROMPT_TEXT = "Fix GitHub Issue #42: Login Returns 500 on Expired Token"

# Patch targets — the wrapper functions in fix.py
_P = "forge.cli.fix"


def _subprocess_dispatch(cmd, **kwargs):
    """Default subprocess.run dispatcher for tests."""
    name = cmd[0] if cmd else ""
    if name == "git":
        if "rev-parse" in cmd:
            return _GIT_REPO_OK
        if "checkout" in cmd:
            return _GIT_BRANCH_OK
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    if name == "gh":
        return _GH_PR_OK
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


_DAEMON_P = "forge.core.daemon.ForgeDaemon"
_SETTINGS_P = "forge.config.settings.ForgeSettings"
_DB_P = "forge.storage.db.Database"


def _full_flow_patches(daemon_mock):
    """Apply all patches needed for a full successful flow."""
    return [
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", side_effect=_subprocess_dispatch),
        patch(f"{_P}._fetch", return_value=_make_issue()),
        patch(f"{_P}._compose", return_value=PROMPT_TEXT),
        patch(f"{_P}._slugify", return_value="login-returns-500-on-expired-token"),
        patch(_DAEMON_P, return_value=daemon_mock),
        patch(_SETTINGS_P, return_value=MagicMock(budget_limit_usd=10.0)),
    ]


def _make_daemon():
    mock_daemon = MagicMock()
    mock_daemon.run = AsyncMock()
    mock_daemon._project_dir = "/tmp"
    mock_daemon._strategy = "auto"
    mock_daemon._settings = MagicMock(budget_limit_usd=10.0)
    return mock_daemon


# ── Parse dispatch ───────────────────────────────────────────────────


def test_bare_issue_number(runner):
    """Bare number dispatches to parse_issue_ref correctly."""
    daemon = _make_daemon()
    patches = _full_flow_patches(daemon)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
    ):
        result = runner.invoke(fix, ["42", "--yes"])
    assert result.exit_code == 0


def test_url_issue_ref(runner):
    """Full GitHub URL dispatches via parse_issue_ref."""
    daemon = _make_daemon()
    with (
        patch(f"{_P}._parse_ref", return_value=(42, "org/repo")),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", side_effect=_subprocess_dispatch),
        patch(f"{_P}._fetch", return_value=_make_issue()),
        patch(f"{_P}._compose", return_value=PROMPT_TEXT),
        patch(f"{_P}._slugify", return_value="login-returns-500-on-expired-token"),
        patch(_DAEMON_P, return_value=daemon),
        patch(_SETTINGS_P, return_value=MagicMock(budget_limit_usd=10.0)),
    ):
        result = runner.invoke(fix, ["https://github.com/org/repo/issues/42", "--yes"])
    assert result.exit_code == 0


def test_invalid_issue_ref(runner):
    """Invalid ref exits with error."""
    with patch(f"{_P}._parse_ref", side_effect=ValueError("bad ref")):
        result = runner.invoke(fix, ["not-valid"])
    assert result.exit_code == 1
    assert "bad ref" in result.output


# ── Auth errors ──────────────────────────────────────────────────────


def test_gh_not_authenticated(runner):
    """gh auth failure exits with error."""
    with (
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", return_value=False),
    ):
        result = runner.invoke(fix, ["42"])
    assert result.exit_code == 1
    assert "not authenticated" in result.output


def test_gh_not_installed(runner):
    """gh binary missing exits with error."""
    with (
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", side_effect=FileNotFoundError("no gh")),
    ):
        result = runner.invoke(fix, ["42"])
    assert result.exit_code == 1
    assert "not installed" in result.output


# ── Not a git repo ───────────────────────────────────────────────────


def test_not_in_git_repo(runner):
    """Not in a git repo exits with error."""
    git_fail = subprocess.CompletedProcess(
        args=["git", "rev-parse"],
        returncode=128,
        stdout="",
        stderr="fatal",
    )
    with (
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", return_value=git_fail),
    ):
        result = runner.invoke(fix, ["42"])
    assert result.exit_code == 1
    assert "not inside a git repository" in result.output


# ── Issue not found ──────────────────────────────────────────────────


def test_issue_not_found(runner):
    """Issue not found exits with error."""
    with (
        patch(f"{_P}._parse_ref", return_value=(999, None)),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", side_effect=_subprocess_dispatch),
        patch(f"{_P}._fetch", side_effect=RuntimeError("Issue 999 not found")),
    ):
        result = runner.invoke(fix, ["999"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ── Branch creation failure ───────────────────────────────────────────


def test_branch_creation_failure(runner):
    """Failed git checkout -b exits with error and does not run daemon."""
    daemon = _make_daemon()

    def _branch_fail_dispatch(cmd, **kwargs):
        name = cmd[0] if cmd else ""
        if name == "git" and "checkout" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=128,
                stdout="",
                stderr="fatal: a branch named 'fix/42-login' already exists",
            )
        return _subprocess_dispatch(cmd, **kwargs)

    with (
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", side_effect=_branch_fail_dispatch),
        patch(f"{_P}._fetch", return_value=_make_issue()),
        patch(f"{_P}._compose", return_value=PROMPT_TEXT),
        patch(f"{_P}._slugify", return_value="login-returns-500-on-expired-token"),
        patch(_DAEMON_P, return_value=daemon),
        patch(_SETTINGS_P, return_value=MagicMock(budget_limit_usd=10.0)),
    ):
        result = runner.invoke(fix, ["42", "--yes"])

    assert result.exit_code == 1
    assert "failed to create branch" in result.output
    daemon.run.assert_not_called()


# ── Dry-run ──────────────────────────────────────────────────────────


def test_dry_run_calls_plan_not_execute(runner):
    """--dry-run calls plan() but NOT daemon.run()."""
    daemon = _make_daemon()
    daemon.plan = AsyncMock(return_value="TaskGraph(tasks=[])")

    mock_db = MagicMock()
    mock_db.initialize = AsyncMock()
    mock_db.create_pipeline = AsyncMock()
    mock_db.close = AsyncMock()

    with (
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", side_effect=_subprocess_dispatch),
        patch(f"{_P}._fetch", return_value=_make_issue()),
        patch(f"{_P}._compose", return_value=PROMPT_TEXT),
        patch(f"{_P}._slugify", return_value="login-returns-500-on-expired-token"),
        patch(_DAEMON_P, return_value=daemon),
        patch(_SETTINGS_P, return_value=MagicMock(budget_limit_usd=10.0)),
        patch(_DB_P, return_value=mock_db),
    ):
        result = runner.invoke(fix, ["42", "--dry-run"])

    assert result.exit_code == 0
    daemon.plan.assert_called_once()
    daemon.run.assert_not_called()


# ── Pipeline failure shows orphan branch note ────────────────────────


def test_pipeline_failure_shows_orphan_branch_note(runner):
    """When daemon.run() fails, user sees note about orphan branch."""
    daemon = _make_daemon()
    daemon.run = AsyncMock(side_effect=RuntimeError("agent crashed"))

    patches = _full_flow_patches(daemon)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
    ):
        result = runner.invoke(fix, ["42", "--yes"])

    assert result.exit_code == 1
    assert "Forge failed: agent crashed" in result.output
    assert "orphan branch" in result.output
    assert "git checkout" in result.output


# ── --yes skips confirmation ─────────────────────────────────────────


def test_yes_skips_confirmation(runner):
    """--yes flag skips the confirmation prompt."""
    daemon = _make_daemon()
    patches = _full_flow_patches(daemon)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
    ):
        result = runner.invoke(fix, ["42", "--yes"])
    assert result.exit_code == 0
    assert "Aborted" not in result.output


# ── --no-pr skips PR creation ────────────────────────────────────────


def test_no_pr_skips_pr_creation(runner):
    """--no-pr flag skips gh pr create."""
    daemon = _make_daemon()
    calls = []

    def _tracking_dispatch(cmd, **kwargs):
        calls.append(cmd)
        return _subprocess_dispatch(cmd, **kwargs)

    with (
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", side_effect=_tracking_dispatch),
        patch(f"{_P}._fetch", return_value=_make_issue()),
        patch(f"{_P}._compose", return_value=PROMPT_TEXT),
        patch(f"{_P}._slugify", return_value="login-returns-500-on-expired-token"),
        patch(_DAEMON_P, return_value=daemon),
        patch(_SETTINGS_P, return_value=MagicMock(budget_limit_usd=10.0)),
    ):
        result = runner.invoke(fix, ["42", "--yes", "--no-pr"])

    assert result.exit_code == 0
    gh_pr_calls = [c for c in calls if c[0] == "gh" and "pr" in c]
    assert len(gh_pr_calls) == 0


# ── --branch override ────────────────────────────────────────────────


def test_branch_override(runner):
    """--branch overrides the default branch name."""
    daemon = _make_daemon()
    calls = []

    def _tracking_dispatch(cmd, **kwargs):
        calls.append(cmd)
        return _subprocess_dispatch(cmd, **kwargs)

    with (
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", side_effect=_tracking_dispatch),
        patch(f"{_P}._fetch", return_value=_make_issue()),
        patch(f"{_P}._compose", return_value=PROMPT_TEXT),
        patch(f"{_P}._slugify", return_value="login-returns-500-on-expired-token"),
        patch(_DAEMON_P, return_value=daemon),
        patch(_SETTINGS_P, return_value=MagicMock(budget_limit_usd=10.0)),
    ):
        result = runner.invoke(fix, ["42", "--yes", "--branch", "my-custom-branch"])

    assert result.exit_code == 0
    checkout_calls = [c for c in calls if "checkout" in c]
    assert any("my-custom-branch" in c for c in checkout_calls)


# ── Full flow ────────────────────────────────────────────────────────


def test_full_flow_plan_and_execute(runner):
    """Full flow calls daemon.run() with composed prompt."""
    daemon = _make_daemon()
    patches = _full_flow_patches(daemon)
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6],
        patches[7],
    ):
        result = runner.invoke(fix, ["42", "--yes"])

    assert result.exit_code == 0
    daemon.run.assert_called_once_with(PROMPT_TEXT)


# ── PR creation ──────────────────────────────────────────────────────


def test_pr_creation_command(runner):
    """Verify gh pr create is called with correct args."""
    daemon = _make_daemon()
    calls = []

    def _tracking_dispatch(cmd, **kwargs):
        calls.append(cmd)
        return _subprocess_dispatch(cmd, **kwargs)

    with (
        patch(f"{_P}._parse_ref", return_value=(42, None)),
        patch(f"{_P}._check_auth", return_value=True),
        patch(f"{_P}.subprocess.run", side_effect=_tracking_dispatch),
        patch(f"{_P}._fetch", return_value=_make_issue()),
        patch(f"{_P}._compose", return_value=PROMPT_TEXT),
        patch(f"{_P}._slugify", return_value="login-returns-500-on-expired-token"),
        patch(_DAEMON_P, return_value=daemon),
        patch(_SETTINGS_P, return_value=MagicMock(budget_limit_usd=10.0)),
    ):
        result = runner.invoke(fix, ["42", "--yes"])

    assert result.exit_code == 0
    pr_calls = [c for c in calls if c[0] == "gh" and "pr" in c]
    assert len(pr_calls) == 1
    pr_cmd = pr_calls[0]
    assert "--title" in pr_cmd
    title_idx = pr_cmd.index("--title") + 1
    assert "Fix #42" in pr_cmd[title_idx]
    assert "--body" in pr_cmd
    body_idx = pr_cmd.index("--body") + 1
    assert "Fixes #42" in pr_cmd[body_idx]
