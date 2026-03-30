"""Tests for forge/core/ci_watcher.py — CI auto-fix watcher and fix loop."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from forge.core.ci_watcher import (
    CICheck,
    CIFixConfig,
    _fetch_checks,
    check_pr_open,
    fetch_failure_logs,
    parse_pr_info,
    poll_ci_checks,
    run_ci_fix_loop,
)

# ── Helpers ───────────────────────────────────────────────────────────


class FakeResult:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ── parse_pr_info ─────────────────────────────────────────────────────


def test_parse_pr_info_valid():
    owner_repo, number = parse_pr_info("https://github.com/owner/repo/pull/42")
    assert owner_repo == "owner/repo"
    assert number == "42"


def test_parse_pr_info_valid_with_trailing_slash():
    owner_repo, number = parse_pr_info("https://github.com/owner/repo/pull/99/")
    assert owner_repo == "owner/repo"
    assert number == "99"


def test_parse_pr_info_invalid_url():
    owner_repo, number = parse_pr_info("https://gitlab.com/owner/repo/merge_requests/1")
    assert owner_repo == ""
    assert number == ""


def test_parse_pr_info_not_a_url():
    owner_repo, number = parse_pr_info("not a url at all")
    assert owner_repo == ""
    assert number == ""


def test_parse_pr_info_http():
    owner_repo, number = parse_pr_info("http://github.com/org/project/pull/7")
    assert owner_repo == "org/project"
    assert number == "7"


# ── CICheck properties ───────────────────────────────────────────────


def test_ci_check_is_terminal_completed():
    check = CICheck(name="build", status="completed", conclusion="success")
    assert check.is_terminal is True


def test_ci_check_is_terminal_in_progress():
    check = CICheck(name="build", status="in_progress", conclusion="")
    assert check.is_terminal is False


def test_ci_check_is_terminal_queued():
    check = CICheck(name="build", status="queued", conclusion="")
    assert check.is_terminal is False


def test_ci_check_is_failure_failure():
    check = CICheck(name="test", status="completed", conclusion="failure")
    assert check.is_failure is True


def test_ci_check_is_failure_cancelled():
    check = CICheck(name="test", status="completed", conclusion="cancelled")
    assert check.is_failure is True


def test_ci_check_is_failure_timed_out():
    check = CICheck(name="test", status="completed", conclusion="timed_out")
    assert check.is_failure is True


def test_ci_check_is_failure_success():
    check = CICheck(name="test", status="completed", conclusion="success")
    assert check.is_failure is False


def test_ci_check_is_failure_not_terminal():
    check = CICheck(name="test", status="in_progress", conclusion="failure")
    assert check.is_failure is False


def test_ci_check_is_success_success():
    check = CICheck(name="lint", status="completed", conclusion="success")
    assert check.is_success is True


def test_ci_check_is_success_neutral():
    check = CICheck(name="lint", status="completed", conclusion="neutral")
    assert check.is_success is True


def test_ci_check_is_success_skipped():
    check = CICheck(name="lint", status="completed", conclusion="skipped")
    assert check.is_success is True


def test_ci_check_is_success_failure():
    check = CICheck(name="lint", status="completed", conclusion="failure")
    assert check.is_success is False


def test_ci_check_is_success_not_terminal():
    check = CICheck(name="lint", status="in_progress", conclusion="success")
    assert check.is_success is False


# ── _fetch_checks ────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_fetch_checks_parses_json(mock_sub):
    gh_output = json.dumps(
        [
            {
                "name": "build",
                "state": "COMPLETED",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/o/r/actions/runs/12345/job/1",
            },
            {
                "name": "lint",
                "state": "COMPLETED",
                "conclusion": "FAILURE",
                "detailsUrl": "https://github.com/o/r/actions/runs/67890/job/2",
            },
        ]
    )
    mock_sub.return_value = FakeResult(returncode=0, stdout=gh_output.encode())

    checks = await _fetch_checks("o/r", "1", "/tmp")

    assert len(checks) == 2
    assert checks[0].name == "build"
    assert checks[0].status == "completed"
    assert checks[0].conclusion == "success"
    assert checks[0].run_id == "12345"
    assert checks[1].name == "lint"
    assert checks[1].conclusion == "failure"
    assert checks[1].run_id == "67890"


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_fetch_checks_non_zero_returncode(mock_sub):
    mock_sub.return_value = FakeResult(returncode=1, stderr=b"not found")
    checks = await _fetch_checks("o/r", "1", "/tmp")
    assert checks == []


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_fetch_checks_invalid_json(mock_sub):
    mock_sub.return_value = FakeResult(returncode=0, stdout=b"not json")
    checks = await _fetch_checks("o/r", "1", "/tmp")
    assert checks == []


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_fetch_checks_no_details_url(mock_sub):
    gh_output = json.dumps(
        [
            {"name": "check", "state": "COMPLETED", "conclusion": "SUCCESS"},
        ]
    )
    mock_sub.return_value = FakeResult(returncode=0, stdout=gh_output.encode())
    checks = await _fetch_checks("o/r", "1", "/tmp")
    assert len(checks) == 1
    assert checks[0].run_id == ""


# ── poll_ci_checks ───────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("forge.core.ci_watcher._fetch_checks")
async def test_poll_ci_checks_all_passing(mock_fetch):
    """All checks terminal on first poll -> returns immediately."""
    mock_fetch.return_value = [
        CICheck(name="build", status="completed", conclusion="success"),
        CICheck(name="test", status="completed", conclusion="success"),
    ]
    checks = await poll_ci_checks("o/r", "1", "/tmp", timeout=60, interval=0.01)
    assert len(checks) == 2
    assert all(c.is_success for c in checks)
    mock_fetch.assert_awaited_once()


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.asyncio.sleep", new_callable=AsyncMock)
@patch("forge.core.ci_watcher._fetch_checks")
async def test_poll_ci_checks_empty_returns_after_grace(mock_fetch, mock_sleep):
    """Empty checks exhaust grace polls -> returns empty list."""
    mock_fetch.return_value = []

    checks = await poll_ci_checks("o/r", "1", "/tmp", timeout=60, interval=0.01)
    assert checks == []
    # 3 grace polls + the initial = _fetch_checks called 3 times
    # (first call sets grace to 2, second to 1, third to 0 -> return)
    assert mock_fetch.await_count == 3


@pytest.mark.asyncio
@patch("forge.core.ci_watcher._fetch_checks")
async def test_poll_ci_checks_cancel_event(mock_fetch):
    """Cancel event set -> raises CancelledError."""
    cancel = asyncio.Event()
    cancel.set()

    with pytest.raises(asyncio.CancelledError):
        await poll_ci_checks("o/r", "1", "/tmp", timeout=60, interval=0.01, cancel_event=cancel)


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.asyncio.sleep", new_callable=AsyncMock)
@patch("forge.core.ci_watcher._fetch_checks")
async def test_poll_ci_checks_waits_for_terminal(mock_fetch, mock_sleep):
    """Non-terminal checks on first poll, terminal on second."""
    mock_fetch.side_effect = [
        [CICheck(name="build", status="in_progress", conclusion="")],
        [CICheck(name="build", status="completed", conclusion="success")],
    ]
    checks = await poll_ci_checks("o/r", "1", "/tmp", timeout=60, interval=0.01)
    assert len(checks) == 1
    assert checks[0].is_success
    assert mock_fetch.await_count == 2


# ── fetch_failure_logs ───────────────────────────────────────────────


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_fetch_failure_logs_success(mock_sub):
    mock_sub.return_value = FakeResult(returncode=0, stdout=b"Error on line 42\nTest failed")
    failed = [CICheck(name="test", status="completed", conclusion="failure", run_id="111")]

    logs = await fetch_failure_logs("o/r", failed, "/tmp")

    assert "test" in logs
    assert "Error on line 42" in logs["test"]


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_fetch_failure_logs_truncation(mock_sub):
    long_output = "x" * 5000
    mock_sub.return_value = FakeResult(returncode=0, stdout=long_output.encode())
    failed = [CICheck(name="build", status="completed", conclusion="failure", run_id="222")]

    logs = await fetch_failure_logs("o/r", failed, "/tmp")

    assert "build" in logs
    assert logs["build"].startswith("... (truncated)")
    # 3000 chars from end + the prefix
    assert len(logs["build"]) < 3100


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_fetch_failure_logs_gh_error(mock_sub):
    mock_sub.return_value = FakeResult(returncode=1, stdout=b"", stderr=b"run not found")
    failed = [CICheck(name="deploy", status="completed", conclusion="failure", run_id="333")]

    logs = await fetch_failure_logs("o/r", failed, "/tmp")

    assert "deploy" in logs
    assert "Failed to fetch logs" in logs["deploy"]


@pytest.mark.asyncio
async def test_fetch_failure_logs_no_run_id():
    """Check without run_id gets a generic message."""
    failed = [CICheck(name="status-check", status="completed", conclusion="failure", run_id="")]

    logs = await fetch_failure_logs("o/r", failed, "/tmp")

    assert "status-check" in logs
    assert "failed with conclusion" in logs["status-check"]


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_fetch_failure_logs_deduplicates_run_ids(mock_sub):
    """Two checks with the same run_id should only call gh once."""
    mock_sub.return_value = FakeResult(returncode=0, stdout=b"log output")
    failed = [
        CICheck(name="check-a", status="completed", conclusion="failure", run_id="444"),
        CICheck(name="check-b", status="completed", conclusion="failure", run_id="444"),
    ]

    logs = await fetch_failure_logs("o/r", failed, "/tmp")

    # First check uses the run_id, second gets generic message
    assert "log output" in logs["check-a"]
    assert "failed with conclusion" in logs["check-b"]
    mock_sub.assert_awaited_once()


# ── check_pr_open ────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_check_pr_open_true(mock_sub):
    mock_sub.return_value = FakeResult(returncode=0, stdout=json.dumps({"state": "OPEN"}).encode())
    assert await check_pr_open("o/r", "1", "/tmp") is True


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_check_pr_open_closed(mock_sub):
    mock_sub.return_value = FakeResult(
        returncode=0, stdout=json.dumps({"state": "CLOSED"}).encode()
    )
    assert await check_pr_open("o/r", "1", "/tmp") is False


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_check_pr_open_merged(mock_sub):
    mock_sub.return_value = FakeResult(
        returncode=0, stdout=json.dumps({"state": "MERGED"}).encode()
    )
    assert await check_pr_open("o/r", "1", "/tmp") is False


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_check_pr_open_gh_error(mock_sub):
    mock_sub.return_value = FakeResult(returncode=1, stderr=b"error")
    assert await check_pr_open("o/r", "1", "/tmp") is False


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.async_subprocess")
async def test_check_pr_open_bad_json(mock_sub):
    mock_sub.return_value = FakeResult(returncode=0, stdout=b"not json")
    assert await check_pr_open("o/r", "1", "/tmp") is False


# ── run_ci_fix_loop ──────────────────────────────────────────────────

_VALID_PR_URL = "https://github.com/owner/repo/pull/10"


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_all_pass_first_poll(mock_poll, mock_pr_open, mock_dispatch):
    """All CI passes on first poll -> returns 'passed' with no fix attempts."""
    mock_pr_open.return_value = True
    mock_poll.return_value = [
        CICheck(name="build", status="completed", conclusion="success"),
    ]

    config = CIFixConfig(max_retries=3, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
    )

    assert result.final_status == "passed"
    assert len(result.attempts) == 0
    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_no_checks(mock_poll, mock_pr_open, mock_dispatch):
    """No CI checks found -> returns 'passed'."""
    mock_pr_open.return_value = True
    mock_poll.return_value = []

    config = CIFixConfig(max_retries=3, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
    )

    assert result.final_status == "passed"
    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.asyncio.sleep", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.fetch_failure_logs", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_fail_then_pass(
    mock_poll, mock_pr_open, mock_dispatch, mock_logs, mock_sleep
):
    """CI fails first, fix dispatched, CI passes on second poll -> 'passed'."""
    mock_pr_open.return_value = True
    mock_poll.side_effect = [
        # Attempt 1: failure
        [CICheck(name="test", status="completed", conclusion="failure", run_id="100")],
        # Attempt 2: success
        [CICheck(name="test", status="completed", conclusion="success")],
    ]
    mock_logs.return_value = {"test": "some log"}

    # Mock the sdk_result returned by dispatch_fix_agent
    sdk_result = AsyncMock()
    sdk_result.cost_usd = 0.05
    sdk_result.result_text = "Fixed the test"
    mock_dispatch.return_value = sdk_result

    config = CIFixConfig(max_retries=3, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
    )

    assert result.final_status == "passed"
    assert len(result.attempts) == 1
    assert result.attempts[0].failed_checks == ["test"]
    assert result.total_cost_usd == 0.05
    mock_dispatch.assert_awaited_once()


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.asyncio.sleep", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.fetch_failure_logs", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_exhausts_retries(
    mock_poll, mock_pr_open, mock_dispatch, mock_logs, mock_sleep
):
    """CI keeps failing through all retries -> 'exhausted'."""
    mock_pr_open.return_value = True
    mock_poll.return_value = [
        CICheck(name="build", status="completed", conclusion="failure", run_id="200"),
    ]
    mock_logs.return_value = {"build": "compile error"}

    sdk_result = AsyncMock()
    sdk_result.cost_usd = 0.01
    sdk_result.result_text = "Attempted fix"
    mock_dispatch.return_value = sdk_result

    config = CIFixConfig(max_retries=2, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
    )

    assert result.final_status == "exhausted"
    assert len(result.attempts) == 2
    assert mock_dispatch.await_count == 2


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_cancel_event(mock_poll, mock_pr_open, mock_dispatch):
    """Cancel event set before loop starts -> 'cancelled'."""
    mock_pr_open.return_value = True
    cancel = asyncio.Event()
    cancel.set()

    config = CIFixConfig(max_retries=3, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
        cancel_event=cancel,
    )

    assert result.final_status == "cancelled"
    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_cancel_during_poll(mock_poll, mock_pr_open, mock_dispatch):
    """CancelledError during poll -> 'cancelled'."""
    mock_pr_open.return_value = True
    mock_poll.side_effect = asyncio.CancelledError("cancelled")

    config = CIFixConfig(max_retries=3, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
    )

    assert result.final_status == "cancelled"
    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_loop_invalid_pr_url():
    """Invalid PR URL -> returns 'error' immediately."""
    config = CIFixConfig(max_retries=3)
    result = await run_ci_fix_loop(
        config=config,
        pr_url="https://not-github.com/foo",
        project_dir="/tmp",
        branch="fix-branch",
    )
    assert result.final_status == "error"


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_pr_closed(mock_poll, mock_pr_open, mock_dispatch):
    """PR no longer open -> 'cancelled'."""
    mock_pr_open.return_value = False

    config = CIFixConfig(max_retries=3, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
    )

    assert result.final_status == "cancelled"
    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_timeout(mock_poll, mock_pr_open, mock_dispatch):
    """Poll raises TimeoutError -> 'timeout'."""
    mock_pr_open.return_value = True
    mock_poll.side_effect = TimeoutError("timed out")

    config = CIFixConfig(max_retries=3, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
    )

    assert result.final_status == "timeout"
    mock_dispatch.assert_not_awaited()


@pytest.mark.asyncio
@patch("forge.core.ci_watcher.asyncio.sleep", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.fetch_failure_logs", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.dispatch_fix_agent", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.check_pr_open", new_callable=AsyncMock)
@patch("forge.core.ci_watcher.poll_ci_checks", new_callable=AsyncMock)
async def test_fix_loop_agent_exception(
    mock_poll, mock_pr_open, mock_dispatch, mock_logs, mock_sleep
):
    """Fix agent raises exception -> records attempt, continues to next retry."""
    mock_pr_open.return_value = True
    # Fail on every poll so we keep retrying
    mock_poll.return_value = [
        CICheck(name="test", status="completed", conclusion="failure", run_id="500"),
    ]
    mock_logs.return_value = {"test": "error log"}
    mock_dispatch.side_effect = RuntimeError("SDK crashed")

    config = CIFixConfig(max_retries=2, poll_interval_seconds=0)
    result = await run_ci_fix_loop(
        config=config,
        pr_url=_VALID_PR_URL,
        project_dir="/tmp",
        branch="fix-branch",
    )

    assert result.final_status == "exhausted"
    assert len(result.attempts) == 2
    assert "Agent error" in result.attempts[0].fix_summary
