"""Tests for forge/core/integration.py — post-merge integration health checks."""

from __future__ import annotations

import os
import subprocess

import pytest

from forge.config.project_config import IntegrationCheckConfig
from forge.core.integration import (
    _temp_health_worktree,
    capture_baseline,
    effective_enabled,
    run_final_gate,
    run_health_check,
    run_post_merge_check,
)

# ── effective_enabled ────────────────────────────────────────────────


def test_effective_enabled_true():
    cfg = IntegrationCheckConfig(enabled=True, cmd="make test")
    assert effective_enabled(cfg) is True


def test_effective_enabled_no_cmd():
    cfg = IntegrationCheckConfig(enabled=True, cmd=None)
    assert effective_enabled(cfg) is False


def test_effective_enabled_empty_cmd():
    cfg = IntegrationCheckConfig(enabled=True, cmd="")
    assert effective_enabled(cfg) is False


def test_effective_enabled_whitespace_cmd():
    cfg = IntegrationCheckConfig(enabled=True, cmd="   ")
    assert effective_enabled(cfg) is False


def test_effective_enabled_disabled():
    cfg = IntegrationCheckConfig(enabled=False, cmd="make test")
    assert effective_enabled(cfg) is False


# ── run_health_check ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_health_check_success():
    result = await run_health_check("exit 0", cwd="/tmp", timeout_seconds=10)
    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.is_regression is False


@pytest.mark.asyncio
async def test_run_health_check_failure():
    result = await run_health_check("exit 1", cwd="/tmp", timeout_seconds=10)
    assert result.status == "failed"
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_run_health_check_failure_exit_2():
    result = await run_health_check("exit 2", cwd="/tmp", timeout_seconds=10)
    assert result.status == "failed"
    assert result.exit_code == 2


@pytest.mark.asyncio
async def test_run_health_check_timeout():
    result = await run_health_check("sleep 30", cwd="/tmp", timeout_seconds=1)
    assert result.status == "timeout"
    assert result.exit_code is None
    assert "timed out" in result.stderr.lower()


@pytest.mark.asyncio
async def test_run_health_check_infra_error():
    result = await run_health_check(
        "__nonexistent_binary_xyz_123__",
        cwd="/tmp",
        timeout_seconds=10,
    )
    # Shell will report "command not found" with exit code 127
    # This comes through as "failed" since the shell itself ran fine
    assert result.status in ("failed", "infra_error")


@pytest.mark.asyncio
async def test_run_health_check_captures_stdout():
    result = await run_health_check("echo hello_world", cwd="/tmp", timeout_seconds=10)
    assert result.status == "passed"
    assert "hello_world" in result.stdout


@pytest.mark.asyncio
async def test_run_health_check_captures_stderr():
    result = await run_health_check("echo error_msg >&2; exit 1", cwd="/tmp", timeout_seconds=10)
    assert result.status == "failed"
    assert "error_msg" in result.stderr


@pytest.mark.asyncio
async def test_run_health_check_shell_chaining():
    """Verify shell mode supports && chaining (venv activation pattern)."""
    result = await run_health_check("echo step1 && echo step2", cwd="/tmp", timeout_seconds=10)
    assert result.status == "passed"
    assert "step1" in result.stdout
    assert "step2" in result.stdout


# ── capture_baseline ─────────────────────────────────────────────────


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with a commit."""
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    # Create a file and commit
    (tmp_path / "README.md").write_text("# test")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True
    )
    return tmp_path


@pytest.mark.asyncio
async def test_capture_baseline_disabled():
    cfg = IntegrationCheckConfig(enabled=False, cmd="exit 0")
    result = await capture_baseline(cfg, "/tmp", "main")
    assert result is None


@pytest.mark.asyncio
async def test_capture_baseline_no_cmd():
    cfg = IntegrationCheckConfig(enabled=True, cmd=None)
    result = await capture_baseline(cfg, "/tmp", "main")
    assert result is None


@pytest.mark.asyncio
async def test_capture_baseline_green(git_repo):
    cfg = IntegrationCheckConfig(enabled=True, cmd="exit 0", timeout_seconds=10)
    result = await capture_baseline(cfg, str(git_repo), "main")
    assert result == 0


@pytest.mark.asyncio
async def test_capture_baseline_red(git_repo):
    cfg = IntegrationCheckConfig(enabled=True, cmd="exit 1", timeout_seconds=10)
    result = await capture_baseline(cfg, str(git_repo), "main")
    assert result == 1


# ── run_post_merge_check ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_merge_regression(git_repo):
    """baseline=0, check fails → is_regression=True."""
    cfg = IntegrationCheckConfig(enabled=True, cmd="exit 1", timeout_seconds=10)
    result = await run_post_merge_check(cfg, str(git_repo), "main", 0, "task-1")
    assert result.status == "failed"
    assert result.is_regression is True


@pytest.mark.asyncio
async def test_post_merge_non_regression(git_repo):
    """baseline!=0, check fails → is_regression=False."""
    cfg = IntegrationCheckConfig(enabled=True, cmd="exit 1", timeout_seconds=10)
    result = await run_post_merge_check(cfg, str(git_repo), "main", 1, "task-1")
    assert result.status == "failed"
    assert result.is_regression is False


@pytest.mark.asyncio
async def test_post_merge_skipped():
    """disabled config → skipped result."""
    cfg = IntegrationCheckConfig(enabled=False, cmd="exit 0")
    result = await run_post_merge_check(cfg, "/tmp", "main", 0, "task-1")
    assert result.status == "skipped"


@pytest.mark.asyncio
async def test_post_merge_passes(git_repo):
    cfg = IntegrationCheckConfig(enabled=True, cmd="exit 0", timeout_seconds=10)
    result = await run_post_merge_check(cfg, str(git_repo), "main", 0, "task-1")
    assert result.status == "passed"
    assert result.is_regression is False


# ── run_final_gate ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_final_gate_passes(git_repo):
    cfg = IntegrationCheckConfig(enabled=True, cmd="exit 0", timeout_seconds=10)
    result = await run_final_gate(cfg, str(git_repo), "main")
    assert result.status == "passed"


@pytest.mark.asyncio
async def test_final_gate_fails(git_repo):
    cfg = IntegrationCheckConfig(enabled=True, cmd="exit 1", timeout_seconds=10)
    result = await run_final_gate(cfg, str(git_repo), "main")
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_final_gate_skipped():
    cfg = IntegrationCheckConfig(enabled=False)
    result = await run_final_gate(cfg, "/tmp", "main")
    assert result.status == "skipped"


# ── _temp_health_worktree ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_temp_worktree_cleanup(git_repo):
    """Worktree is removed even on exception."""
    wt_path = None
    with pytest.raises(ValueError):
        async with _temp_health_worktree(str(git_repo), "main") as p:
            wt_path = p
            assert os.path.isdir(wt_path)
            raise ValueError("intentional")
    # After exception, worktree should be cleaned up
    assert wt_path is not None
    assert not os.path.isdir(wt_path)


@pytest.mark.asyncio
async def test_temp_worktree_creates_and_cleans(git_repo):
    """Worktree is created and removed on normal exit."""
    async with _temp_health_worktree(str(git_repo), "main") as wt_path:
        assert os.path.isdir(wt_path)
        assert os.path.isfile(os.path.join(wt_path, "README.md"))
    assert not os.path.isdir(wt_path)
