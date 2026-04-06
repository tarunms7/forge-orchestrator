"""Tests for daemon_helpers — git diff utilities and context helpers."""

import asyncio
import logging
import subprocess
from unittest.mock import AsyncMock, call, patch

import pytest

from forge.core.daemon_helpers import (
    _extract_activity,
    _extract_implementation_summary,
    _extract_text,
    _filter_review_diff,
    _find_related_test_files,
    _get_changed_files_vs_main,
    _get_diff_stats,
    _get_diff_vs_main,
    _humanize_model_spec,
    _is_pytest_cmd,
    _is_review_excluded_path,
    _load_conventions_md,
    _parse_forge_learning,
    _parse_forge_question,
    _run_git,
    async_subprocess,
    compute_worktree_path,
    format_routing_summary,
)
from forge.providers.base import EventKind, ProviderEvent


def _make_proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Return a fake CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def _async_side_effects(procs: list[subprocess.CompletedProcess]) -> AsyncMock:
    """Create an AsyncMock with side_effect returning procs in order."""
    mock = AsyncMock(side_effect=procs)
    return mock


# ── async_subprocess tests ────────────────────────────────────────────


class TestAsyncSubprocess:
    """Tests for the async_subprocess() helper."""

    @pytest.mark.asyncio
    async def test_success_returns_completed_process(self):
        """Successful command returns a CompletedProcess with stdout/stderr."""
        result = await async_subprocess(
            ["echo", "hello"],
            cwd="/tmp",
        )
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.args == ["echo", "hello"]

    @pytest.mark.asyncio
    async def test_nonzero_exit_does_not_raise(self):
        """Non-zero exit code is returned, not raised."""
        result = await async_subprocess(
            ["git", "rev-parse", "--verify", "nonexistent-ref-abc123"],
            cwd="/tmp",
        )
        assert result.returncode != 0

    @pytest.mark.asyncio
    async def test_timeout_kills_and_raises(self):
        """A command exceeding the timeout is killed and TimeoutError raised."""
        with pytest.raises(asyncio.TimeoutError, match="timed out"):
            await async_subprocess(
                ["sleep", "10"],
                cwd="/tmp",
                timeout=0.1,
            )

    @pytest.mark.asyncio
    async def test_completed_process_shape(self):
        """Verify the returned CompletedProcess has the expected attributes."""
        result = await async_subprocess(
            ["echo", "test"],
            cwd="/tmp",
        )
        assert hasattr(result, "args")
        assert hasattr(result, "returncode")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)


# ── _get_diff_vs_main tests ──────────────────────────────────────────


class TestGetDiffVsMainBaseRef:
    """_get_diff_vs_main() with explicit base_ref skips the --not --remotes heuristic."""

    @pytest.mark.asyncio
    async def test_uses_base_ref_when_provided(self):
        """Should diff merge-base(base_ref, HEAD)..HEAD, no rev-list call."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        merge_base = _make_proc("mergebase123\n", returncode=0)
        diff_proc = _make_proc("diff --git a/foo.py b/foo.py\n+new line\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, merge_base, diff_proc],
        ) as mock_sub:
            result = await _get_diff_vs_main(
                "/repo/worktrees/task-1", base_ref="forge/pipeline-abc"
            )

        assert "new line" in result
        assert mock_sub.call_count == 3
        assert mock_sub.call_args_list[0] == call(
            ["git", "rev-parse", "--verify", "forge/pipeline-abc"],
            cwd="/repo/worktrees/task-1",
        )
        assert mock_sub.call_args_list[1] == call(
            ["git", "merge-base", "forge/pipeline-abc", "HEAD"],
            cwd="/repo/worktrees/task-1",
        )
        assert mock_sub.call_args_list[2] == call(
            ["git", "diff", "mergebase123", "HEAD"],
            cwd="/repo/worktrees/task-1",
        )

    @pytest.mark.asyncio
    async def test_falls_back_when_base_ref_not_found(self):
        """When base_ref can't be resolved, falls back to --not --remotes heuristic."""
        verify_fail = _make_proc("", returncode=128)
        count_proc = _make_proc("1\n")
        heuristic_verify = _make_proc("def456\n", returncode=0)
        diff_proc = _make_proc("diff --git a/bar.py b/bar.py\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_fail, count_proc, heuristic_verify, diff_proc],
        ):
            result = await _get_diff_vs_main("/repo", base_ref="forge/pipeline-missing")

        assert result == "diff --git a/bar.py b/bar.py\n"

    @pytest.mark.asyncio
    async def test_none_base_ref_uses_heuristic(self):
        """When base_ref is None, uses the commit-count heuristic directly."""
        count_proc = _make_proc("1\n")
        heuristic_verify = _make_proc("abc\n", returncode=0)
        diff_proc = _make_proc("some diff\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[count_proc, heuristic_verify, diff_proc],
        ):
            result = await _get_diff_vs_main("/repo", base_ref=None)

        assert result == "some diff\n"


class TestReviewDiffFiltering:
    def test_filter_review_diff_keeps_gitignore_changes(self):
        diff = (
            "diff --git a/.gitignore b/.gitignore\n"
            "--- a/.gitignore\n"
            "+++ b/.gitignore\n"
            "@@ -1 +1,2 @@\n"
            " node_modules/\n"
            "+uv.lock\n"
            "diff --git a/web/src/stores/taskStore.ts b/web/src/stores/taskStore.ts\n"
            "--- a/web/src/stores/taskStore.ts\n"
            "+++ b/web/src/stores/taskStore.ts\n"
            "@@ -1 +1 @@\n"
            "+const x = 1;\n"
        )

        filtered = _filter_review_diff(diff)

        assert "diff --git a/.gitignore b/.gitignore" in filtered
        assert "+uv.lock" in filtered
        assert "taskStore.ts" in filtered

    def test_filter_review_diff_excludes_forge_managed_dirs_only(self):
        diff = (
            "diff --git a/.forge/forge.toml b/.forge/forge.toml\n"
            "--- a/.forge/forge.toml\n"
            "+++ b/.forge/forge.toml\n"
            "@@ -1 +1 @@\n"
            "+mode = 'full'\n"
            "diff --git a/.claude/state.json b/.claude/state.json\n"
            "--- a/.claude/state.json\n"
            "+++ b/.claude/state.json\n"
            "@@ -1 +1 @@\n"
            "+{}\n"
            "diff --git a/.gitignore b/.gitignore\n"
            "--- a/.gitignore\n"
            "+++ b/.gitignore\n"
            "@@ -1 +1,2 @@\n"
            "+uv.lock\n"
        )

        filtered = _filter_review_diff(diff)

        assert ".forge/forge.toml" not in filtered
        assert ".claude/state.json" not in filtered
        assert "diff --git a/.gitignore b/.gitignore" in filtered

    def test_review_excluded_path_keeps_repo_level_gitignore(self):
        assert _is_review_excluded_path(".forge/forge.toml") is True
        assert _is_review_excluded_path(".claude/worktrees/task/file.py") is True
        assert _is_review_excluded_path(".gitignore") is False


class TestGetChangedFilesVsMainBaseRef:
    """_get_changed_files_vs_main() with explicit base_ref."""

    @pytest.mark.asyncio
    async def test_uses_base_ref_when_provided(self):
        """Should use git diff --name-only merge-base(base_ref, HEAD) HEAD."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        merge_base = _make_proc("mergebase123\n", returncode=0)
        name_only_proc = _make_proc("foo.py\nbar.py\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, merge_base, name_only_proc],
        ) as mock_sub:
            result = await _get_changed_files_vs_main("/repo/wt", base_ref="forge/pipeline-abc")

        assert result == ["foo.py", "bar.py"]
        assert mock_sub.call_args_list[2] == call(
            ["git", "diff", "--name-only", "mergebase123", "HEAD"],
            cwd="/repo/wt",
        )

    @pytest.mark.asyncio
    async def test_falls_back_when_base_ref_not_found(self):
        """Falls back to heuristic when base_ref doesn't resolve."""
        verify_fail = _make_proc("", returncode=128)
        count_proc = _make_proc("1\n")
        heuristic_verify = _make_proc("abc\n", returncode=0)
        name_only_proc = _make_proc("baz.py\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_fail, count_proc, heuristic_verify, name_only_proc],
        ):
            result = await _get_changed_files_vs_main("/repo", base_ref="forge/missing")

        assert result == ["baz.py"]

    @pytest.mark.asyncio
    async def test_none_base_ref_uses_heuristic(self):
        """When base_ref is None, uses heuristic directly."""
        count_proc = _make_proc("2\n")
        heuristic_verify = _make_proc("abc\n", returncode=0)
        name_only_proc = _make_proc("x.py\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[count_proc, heuristic_verify, name_only_proc],
        ):
            result = await _get_changed_files_vs_main("/repo")

        assert result == ["x.py"]


class TestGetDiffStatsPipelineBranch:
    """_get_diff_stats() with a valid pipeline_branch uses git diff --shortstat."""

    @pytest.mark.asyncio
    async def test_uses_pipeline_branch_when_ref_resolves(self):
        """Should return per-task stats from `git diff --shortstat <merge-base> HEAD`."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        merge_base = _make_proc("mergebase123\n", returncode=0)
        shortstat = _make_proc(" 3 files changed, 42 insertions(+), 7 deletions(-)\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, merge_base, shortstat],
        ) as mock_sub:
            result = await _get_diff_stats(
                "/repo/worktrees/task-1", pipeline_branch="forge/pipeline-abc"
            )

        assert result == {"linesAdded": 42, "linesRemoved": 7, "filesChanged": 3}
        assert mock_sub.call_count == 3
        shortstat_call = mock_sub.call_args_list[2]
        assert shortstat_call == call(
            ["git", "diff", "--shortstat", "mergebase123", "HEAD"],
            cwd="/repo/worktrees/task-1",
        )

    @pytest.mark.asyncio
    async def test_insertions_only(self):
        """Handles a diff with insertions but no deletions."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        merge_base = _make_proc("mergebase123\n", returncode=0)
        shortstat = _make_proc(" 1 file changed, 10 insertions(+)\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, merge_base, shortstat],
        ):
            result = await _get_diff_stats("/repo", pipeline_branch="forge/pipeline-abc")

        assert result == {"linesAdded": 10, "linesRemoved": 0, "filesChanged": 1}

    @pytest.mark.asyncio
    async def test_deletions_only(self):
        """Handles a diff with deletions but no insertions."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        merge_base = _make_proc("mergebase123\n", returncode=0)
        shortstat = _make_proc(" 2 files changed, 5 deletions(-)\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, merge_base, shortstat],
        ):
            result = await _get_diff_stats("/repo", pipeline_branch="forge/pipeline-abc")

        assert result == {"linesAdded": 0, "linesRemoved": 5, "filesChanged": 2}

    @pytest.mark.asyncio
    async def test_empty_shortstat_returns_zeros(self):
        """When the diff is empty (no changes), returns zeros."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        merge_base = _make_proc("mergebase123\n", returncode=0)
        shortstat = _make_proc("")  # empty = no diff

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, merge_base, shortstat],
        ):
            result = await _get_diff_stats("/repo", pipeline_branch="forge/pipeline-abc")

        assert result == {"linesAdded": 0, "linesRemoved": 0, "filesChanged": 0}


class TestGetDiffStatsFallback:
    """_get_diff_stats() falls back to commit-count heuristic when pipeline branch is missing."""

    @pytest.mark.asyncio
    async def test_falls_back_when_pipeline_branch_not_found(self):
        """When git rev-parse --verify fails, falls back to HEAD~N approach."""
        verify_fail = _make_proc("", returncode=128)  # branch not found
        count_proc = _make_proc("2\n")  # 2 local commits
        base_verify = _make_proc("def456\n", returncode=0)
        shortstat = _make_proc(" 1 file changed, 15 insertions(+), 3 deletions(-)\n")

        side_effects = [verify_fail, count_proc, base_verify, shortstat]
        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=side_effects,
        ):
            result = await _get_diff_stats("/repo", pipeline_branch="forge/pipeline-missing")

        assert result == {"linesAdded": 15, "linesRemoved": 3, "filesChanged": 1}

    @pytest.mark.asyncio
    async def test_no_pipeline_branch_uses_commit_count(self):
        """When pipeline_branch is None, uses HEAD~N heuristic directly."""
        count_proc = _make_proc("1\n")
        base_verify = _make_proc("abc\n", returncode=0)
        shortstat = _make_proc(" 2 files changed, 100 insertions(+), 20 deletions(-)\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[count_proc, base_verify, shortstat],
        ):
            result = await _get_diff_stats("/repo", pipeline_branch=None)

        assert result == {"linesAdded": 100, "linesRemoved": 20, "filesChanged": 2}

    @pytest.mark.asyncio
    async def test_root_commit_uses_empty_tree(self):
        """Fallback handles root commits by diffing against the empty tree."""
        count_proc = _make_proc("1\n")
        base_verify = _make_proc("", returncode=128)  # HEAD~1 doesn't exist
        empty_tree_proc = _make_proc("4b825dc642cb6eb9a060e54bf8d69288fbee4904\n")
        shortstat = _make_proc(" 1 file changed, 50 insertions(+)\n")

        side_effects = [count_proc, base_verify, empty_tree_proc, shortstat]
        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=side_effects,
        ):
            result = await _get_diff_stats("/repo", pipeline_branch=None)

        assert result == {"linesAdded": 50, "linesRemoved": 0, "filesChanged": 1}

    @pytest.mark.asyncio
    async def test_invalid_commit_count_defaults_to_one(self):
        """When git rev-list returns non-integer output, defaults commit_count to 1."""
        count_proc = _make_proc("bad-output\n")
        base_verify = _make_proc("abc\n", returncode=0)
        shortstat = _make_proc(" 1 file changed, 5 insertions(+)\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[count_proc, base_verify, shortstat],
        ):
            result = await _get_diff_stats("/repo")

        assert result == {"linesAdded": 5, "linesRemoved": 0, "filesChanged": 1}

    @pytest.mark.asyncio
    async def test_zero_commit_count_defaults_to_one(self):
        """When rev-list returns 0, bumps commit_count to 1 to avoid HEAD~0 == HEAD."""
        count_proc = _make_proc("0\n")
        base_verify = _make_proc("abc\n", returncode=0)
        shortstat = _make_proc(" 1 file changed, 8 insertions(+), 2 deletions(-)\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[count_proc, base_verify, shortstat],
        ):
            result = await _get_diff_stats("/repo")

        assert result == {"linesAdded": 8, "linesRemoved": 2, "filesChanged": 1}


class TestLoadConventionsMd:
    """_load_conventions_md() reads .forge/conventions.md from project dir."""

    def test_file_exists_with_content(self, tmp_path):
        """Returns stripped content when file exists and has content."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        conventions_file = forge_dir / "conventions.md"
        conventions_file.write_text("## Styling\n\nUse Tailwind.\n")

        result = _load_conventions_md(str(tmp_path))

        assert result == "## Styling\n\nUse Tailwind."

    def test_file_missing(self, tmp_path):
        """Returns None when the file doesn't exist."""
        result = _load_conventions_md(str(tmp_path))

        assert result is None

    def test_file_empty(self, tmp_path):
        """Returns None when the file is empty or whitespace-only."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        conventions_file = forge_dir / "conventions.md"
        conventions_file.write_text("   \n  \n  ")

        result = _load_conventions_md(str(tmp_path))

        assert result is None


class TestExtractImplementationSummary:
    """_extract_implementation_summary() builds a short summary from git + agent."""

    @pytest.mark.asyncio
    async def test_with_commit_messages_and_agent_summary(self):
        """Combines commit messages with agent summary."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        log_proc = _make_proc("feat: add auth\nfix: handle edge case\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, log_proc],
        ):
            result = await _extract_implementation_summary(
                "/repo/wt",
                "Added authentication module",
                pipeline_branch="forge/pipeline-abc",
            )

        assert "feat: add auth; fix: handle edge case" in result
        assert "Added authentication module" in result
        assert len(result) <= 300

    @pytest.mark.asyncio
    async def test_with_commit_messages_only(self):
        """Uses commit messages when agent summary is generic 'Task completed'."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        log_proc = _make_proc("feat: add new endpoint\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, log_proc],
        ):
            result = await _extract_implementation_summary(
                "/repo/wt",
                "Task completed",
                pipeline_branch="forge/pipeline-abc",
            )

        assert "feat: add new endpoint" in result
        # Generic "Task completed" should be excluded
        assert "Task completed" not in result

    @pytest.mark.asyncio
    async def test_without_pipeline_branch_uses_fallback(self):
        """Falls back to --not --remotes when pipeline_branch is None."""
        log_proc = _make_proc("chore: initial setup\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[log_proc],
        ):
            result = await _extract_implementation_summary("/repo/wt", "Task completed")

        assert "chore: initial setup" in result

    @pytest.mark.asyncio
    async def test_with_pipeline_branch_that_resolves(self):
        """Uses pipeline_branch..HEAD when the ref resolves."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        log_proc = _make_proc("feat: add login\nfeat: add logout\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, log_proc],
        ):
            result = await _extract_implementation_summary(
                "/repo/wt",
                "Task completed",
                pipeline_branch="forge/pipeline-abc",
            )

        assert "feat: add login; feat: add logout" in result

    @pytest.mark.asyncio
    async def test_pipeline_branch_not_found_falls_back(self):
        """Falls back to --not --remotes when pipeline_branch can't be resolved."""
        verify_fail = _make_proc("", returncode=128)
        log_fallback = _make_proc("fix: something\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_fail, log_fallback],
        ):
            result = await _extract_implementation_summary(
                "/repo/wt",
                "Fixed the thing",
                pipeline_branch="forge/missing",
            )

        assert "fix: something" in result
        assert "Fixed the thing" in result

    @pytest.mark.asyncio
    async def test_no_commits_no_summary_returns_fallback(self):
        """Returns generic fallback when no commit messages and no agent summary."""
        log_proc = _make_proc("", returncode=0)

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[log_proc],
        ):
            result = await _extract_implementation_summary("/repo/wt", "Task completed")

        assert "no detailed summary" in result.lower()

    @pytest.mark.asyncio
    async def test_truncates_to_300_chars(self):
        """Summary is capped at 300 characters."""
        long_messages = "\n".join([f"feat: implement feature number {i}" for i in range(50)])
        verify_ok = _make_proc("abc123\n", returncode=0)
        log_proc = _make_proc(long_messages + "\n")

        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[verify_ok, log_proc],
        ):
            result = await _extract_implementation_summary(
                "/repo/wt",
                "A very detailed agent summary that goes on and on",
                pipeline_branch="forge/pipeline-abc",
            )

        assert len(result) <= 300


class TestIsPytestCmd:
    """_is_pytest_cmd() detects pytest-based test commands."""

    def test_plain_pytest(self):
        assert _is_pytest_cmd("pytest") is True

    def test_python_m_pytest(self):
        assert _is_pytest_cmd("python -m pytest") is True

    def test_pytest_with_args(self):
        assert _is_pytest_cmd("pytest -v --tb=short") is True

    def test_non_pytest(self):
        assert _is_pytest_cmd("npm test") is False

    def test_make_test(self):
        assert _is_pytest_cmd("make test") is False


class TestFindRelatedTestFiles:
    """_find_related_test_files() discovers test files for changed source files."""

    @pytest.mark.asyncio
    async def test_co_located_test_found(self, tmp_path):
        """foo.py → foo_test.py (same directory)."""
        (tmp_path / "forge" / "core").mkdir(parents=True)
        (tmp_path / "forge" / "core" / "foo.py").touch()
        (tmp_path / "forge" / "core" / "foo_test.py").touch()

        result = await _find_related_test_files(
            str(tmp_path),
            ["forge/core/foo.py"],
        )
        assert result == ["forge/core/foo_test.py"]

    @pytest.mark.asyncio
    async def test_test_dir_convention(self, tmp_path):
        """src/foo.py → src/tests/test_foo.py."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").touch()
        (tmp_path / "src" / "tests").mkdir()
        (tmp_path / "src" / "tests" / "test_foo.py").touch()

        result = await _find_related_test_files(
            str(tmp_path),
            ["src/foo.py"],
        )
        assert result == ["src/tests/test_foo.py"]

    @pytest.mark.asyncio
    async def test_root_tests_convention(self, tmp_path):
        """src/foo.py → tests/test_foo.py (root-level tests dir)."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_foo.py").touch()

        result = await _find_related_test_files(
            str(tmp_path),
            ["src/foo.py"],
        )
        assert result == ["tests/test_foo.py"]

    @pytest.mark.asyncio
    async def test_changed_file_is_test(self, tmp_path):
        """Test files themselves are included directly."""
        (tmp_path / "forge" / "core").mkdir(parents=True)
        (tmp_path / "forge" / "core" / "foo_test.py").touch()

        result = await _find_related_test_files(
            str(tmp_path),
            ["forge/core/foo_test.py"],
        )
        assert result == ["forge/core/foo_test.py"]

    @pytest.mark.asyncio
    async def test_changed_file_test_prefix(self, tmp_path):
        """test_foo.py style test files are included directly."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_bar.py").touch()

        result = await _find_related_test_files(
            str(tmp_path),
            ["tests/test_bar.py"],
        )
        assert result == ["tests/test_bar.py"]

    @pytest.mark.asyncio
    async def test_no_test_files_found(self, tmp_path):
        """Returns empty list when no test files exist for the changed files."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").touch()
        # No test files anywhere

        result = await _find_related_test_files(
            str(tmp_path),
            ["src/foo.py"],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_non_python_files_ignored(self, tmp_path):
        """Non-.py files are skipped."""
        result = await _find_related_test_files(
            str(tmp_path),
            ["README.md", "package.json"],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_changed_files(self, tmp_path):
        """Multiple changed files accumulate their test files."""
        (tmp_path / "forge" / "core").mkdir(parents=True)
        (tmp_path / "forge" / "core" / "foo.py").touch()
        (tmp_path / "forge" / "core" / "foo_test.py").touch()
        (tmp_path / "forge" / "core" / "bar.py").touch()
        (tmp_path / "forge" / "core" / "bar_test.py").touch()

        result = await _find_related_test_files(
            str(tmp_path),
            ["forge/core/foo.py", "forge/core/bar.py"],
        )
        assert result == ["forge/core/bar_test.py", "forge/core/foo_test.py"]

    @pytest.mark.asyncio
    async def test_deduplicates_test_files(self, tmp_path):
        """Same test file found via different paths is only included once."""
        (tmp_path / "forge" / "core").mkdir(parents=True)
        (tmp_path / "forge" / "core" / "foo.py").touch()
        (tmp_path / "forge" / "core" / "foo_test.py").touch()

        result = await _find_related_test_files(
            str(tmp_path),
            ["forge/core/foo.py", "forge/core/foo_test.py"],
        )
        assert result == ["forge/core/foo_test.py"]


class TestParseForgeQuestion:
    """_parse_forge_question() extracts structured question data from agent output."""

    def test_valid_question_at_end(self):
        text = 'I analyzed the code.\n\nFORGE_QUESTION:\n{"question": "Which pattern?", "suggestions": ["A", "B"], "impact": "high"}'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "Which pattern?"
        assert result["suggestions"] == ["A", "B"]
        assert result["impact"] == "high"

    def test_valid_question_with_context(self):
        text = 'Analyzed.\n\nFORGE_QUESTION:\n{"question": "Which?", "context": "Found 2", "suggestions": ["A", "B"]}'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["context"] == "Found 2"

    def test_question_in_markdown_fence(self):
        text = (
            'Done.\n\nFORGE_QUESTION:\n```json\n{"question": "Which?", "suggestions": ["A"]}\n```'
        )
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "Which?"

    def test_no_question_returns_none(self):
        text = "I wrote the code and committed it."
        result = _parse_forge_question(text)
        assert result is None

    def test_missing_question_field_returns_none(self):
        text = 'FORGE_QUESTION:\n{"suggestions": ["A", "B"]}'
        result = _parse_forge_question(text)
        assert result is None

    def test_malformed_json_returns_none(self):
        text = "FORGE_QUESTION:\n{not valid json}"
        result = _parse_forge_question(text)
        assert result is None

    def test_question_mid_output_with_trailing_text_accepted(self):
        """Trailing text after valid question JSON is now accepted (marker + valid JSON = question)."""
        text = 'FORGE_QUESTION:\n{"question": "?", "suggestions": ["A"]}\n\nThen I continued working and wrote code.'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "?"

    def test_empty_text_returns_none(self):
        result = _parse_forge_question("")
        assert result is None

    def test_none_text_returns_none(self):
        result = _parse_forge_question(None)
        assert result is None

    def test_question_with_long_trailing_text_now_accepted(self):
        """Trailing text after valid JSON should NOT cause the question to be dropped."""
        text = (
            'FORGE_QUESTION:\n{"question": "Which pattern?", "suggestions": ["A", "B"]}\n\n'
            "I'll pause here and wait for your guidance on this. "
            "Meanwhile I've set up the basic structure so we can proceed quickly once you decide."
        )
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "Which pattern?"

    def test_question_with_no_suggestions_accepted(self):
        """Questions without suggestions should be accepted (no restriction on content)."""
        text = 'FORGE_QUESTION:\n{"question": "What should the TTL be?"}'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "What should the TTL be?"

    def test_question_with_many_suggestions_accepted(self):
        """No limit on number of suggestions."""
        suggestions = [f"Option {i}" for i in range(10)]
        import json

        text = (
            f"FORGE_QUESTION:\n{json.dumps({'question': 'Pick one', 'suggestions': suggestions})}"
        )
        result = _parse_forge_question(text)
        assert result is not None
        assert len(result["suggestions"]) == 10

    def test_question_with_extra_keys_accepted(self):
        """Extra keys beyond question/suggestions should be preserved (forward-compatible)."""
        text = 'FORGE_QUESTION:\n{"question": "?", "suggestions": ["A"], "impact": "high", "custom_field": 42}'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["custom_field"] == 42

    def test_malformed_json_logs_warning(self, caplog):
        """Malformed JSON after marker should log a warning."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forge"):
            result = _parse_forge_question("FORGE_QUESTION:\n{not valid json}")
        assert result is None
        assert "FORGE_QUESTION marker found but JSON parse failed" in caplog.text

    def test_missing_question_key_logs_warning(self, caplog):
        """Valid JSON without 'question' key should log a warning."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forge"):
            result = _parse_forge_question('FORGE_QUESTION:\n{"suggestions": ["A"]}')
        assert result is None
        assert "missing 'question' key" in caplog.text

    def test_brace_matching_failure_logs_warning(self, caplog):
        """Unmatched braces should log a warning."""
        import logging

        with caplog.at_level(logging.WARNING, logger="forge"):
            result = _parse_forge_question("FORGE_QUESTION:\n{unclosed")
        assert result is None
        assert "brace matching failed" in caplog.text

    def test_braces_inside_json_strings_ignored(self):
        """Braces inside JSON string values should not confuse the brace counter."""
        text = (
            "Some output.\n\nFORGE_QUESTION:\n"
            '{"question": "How to handle {braces} in strings?", '
            '"suggestions": ["Option {A}", "Option {B}"], "impact": "high"}'
        )
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "How to handle {braces} in strings?"

    def test_escaped_quotes_in_json_strings(self):
        """Escaped quotes inside JSON strings should not break parsing."""
        text = 'FORGE_QUESTION:\n{"question": "Use \\"quoted\\" pattern?", "suggestions": ["A"]}'
        result = _parse_forge_question(text)
        assert result is not None
        assert "quoted" in result["question"]

    def test_plaintext_question_line_is_recovered(self):
        text = (
            "I found the blocked detail panel pattern in the TUI.\n"
            "Let me ask a clarifying question before I implement it.\n"
            "**Question 1:** Should the blocked detail replace the normal output view?\n"
        )
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "Should the blocked detail replace the normal output view?"
        assert "clarifying question" in (result["context"] or "")
        assert result["source"] == "plaintext_fallback"

    def test_plaintext_question_collects_following_suggestions(self):
        text = (
            "I need your input before I continue.\n"
            "Question: Which layout should I use?\n"
            "- Replace the output view entirely\n"
            "- Show the detail above recent logs\n"
        )
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "Which layout should I use?"
        assert result["suggestions"] == [
            "Replace the output view entirely",
            "Show the detail above recent logs",
        ]


class TestRunGit:
    """_run_git() wraps async_subprocess with logging and error handling."""

    @pytest.mark.asyncio
    async def test_success_returns_result(self):
        """On exit 0, returns the CompletedProcess without raising."""
        proc = _make_proc("abc123\n", returncode=0)
        with patch(
            "forge.core.daemon_helpers.async_subprocess", new_callable=AsyncMock, return_value=proc
        ) as mock_sub:
            result = await _run_git(["rev-parse", "HEAD"], cwd="/repo")

        assert result is proc
        mock_sub.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            cwd="/repo",
        )

    @pytest.mark.asyncio
    async def test_check_true_raises_on_failure(self):
        """With check=True (default), non-zero exit raises CalledProcessError."""
        proc = subprocess.CompletedProcess(
            args=["git", "rev-parse", "HEAD"],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository",
        )
        with patch(
            "forge.core.daemon_helpers.async_subprocess", new_callable=AsyncMock, return_value=proc
        ):
            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                await _run_git(["rev-parse", "HEAD"], cwd="/bad")

        assert exc_info.value.returncode == 128

    @pytest.mark.asyncio
    async def test_check_false_returns_result_on_failure(self, caplog):
        """With check=False, non-zero exit returns result and logs warning."""
        proc = subprocess.CompletedProcess(
            args=["git", "status"],
            returncode=1,
            stdout="",
            stderr="error: something went wrong",
        )
        with patch(
            "forge.core.daemon_helpers.async_subprocess", new_callable=AsyncMock, return_value=proc
        ):
            with caplog.at_level(logging.WARNING, logger="forge"):
                result = await _run_git(["status"], cwd="/repo", check=False)

        assert result is proc
        assert "returned 1" in caplog.text


class TestFindRelatedTestFilesScoped:
    """Tests for _find_related_test_files with allowed_files filtering."""

    @pytest.mark.asyncio
    async def test_in_scope_test_included(self, tmp_path):
        """Test file in allowed_files is included."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        in_scope, out_of_scope = await _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=["src/auth.py", "tests/test_auth.py"],
        )
        assert "tests/test_auth.py" in in_scope
        assert len(out_of_scope) == 0

    @pytest.mark.asyncio
    async def test_out_of_scope_test_excluded(self, tmp_path):
        """Test file NOT in allowed_files is excluded."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        in_scope, out_of_scope = await _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=["src/auth.py"],  # test_auth.py NOT listed
        )
        assert "tests/test_auth.py" not in in_scope
        assert "tests/test_auth.py" in out_of_scope

    @pytest.mark.asyncio
    async def test_no_allowed_files_returns_all(self, tmp_path):
        """When allowed_files is None, all discovered tests are in-scope."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        result = await _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=None,
        )
        # Backward compat: returns flat list when allowed_files is None
        assert "tests/test_auth.py" in result

    @pytest.mark.asyncio
    async def test_newly_created_test_is_in_scope(self, tmp_path):
        """A test file created by the agent (not on base branch) is in-scope."""
        # Set up a git repo to simulate new file detection
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True
        )

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_new.py").write_text("# new test")
        (tmp_path / "new.py").write_text("# new module")

        # Stage and commit the new test on a branch
        subprocess.run(["git", "checkout", "-b", "work"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add new"], cwd=tmp_path, capture_output=True)

        in_scope, out_of_scope = await _find_related_test_files(
            str(tmp_path),
            changed_files=["new.py"],
            allowed_files=["new.py"],  # test_new.py NOT in allowed list
            base_ref="main",
        )
        # test_new.py was created by agent (not on main), so it's in-scope
        assert "tests/test_new.py" in in_scope


class TestResolveRef:
    """_resolve_ref() resolves a git ref to its commit SHA."""

    @pytest.mark.asyncio
    async def test_resolves_valid_ref(self):
        """Returns commit SHA for a valid ref."""
        proc = _make_proc("abc123def456\n", returncode=0)
        with patch(
            "forge.core.daemon_helpers.async_subprocess", new_callable=AsyncMock, return_value=proc
        ):
            from forge.core.daemon_helpers import _resolve_ref

            result = await _resolve_ref("/repo", "main")
        assert result == "abc123def456"

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_ref(self):
        """Returns None when ref doesn't resolve."""
        proc = _make_proc("", returncode=128)
        with patch(
            "forge.core.daemon_helpers.async_subprocess", new_callable=AsyncMock, return_value=proc
        ):
            from forge.core.daemon_helpers import _resolve_ref

            result = await _resolve_ref("/repo", "nonexistent")
        assert result is None


class TestGetCurrentBranch:
    """_get_current_branch() returns the current branch name."""

    @pytest.mark.asyncio
    async def test_returns_branch_name(self):
        """Returns branch name from git rev-parse."""
        proc = _make_proc("feature-branch\n", returncode=0)
        with patch(
            "forge.core.daemon_helpers.async_subprocess", new_callable=AsyncMock, return_value=proc
        ):
            from forge.core.daemon_helpers import _get_current_branch

            result = await _get_current_branch("/repo")
        assert result == "feature-branch"

    @pytest.mark.asyncio
    async def test_detached_head_falls_back_to_symbolic_ref(self):
        """When rev-parse returns HEAD, falls back to symbolic-ref."""
        head_proc = _make_proc("HEAD\n", returncode=0)
        sym_proc = _make_proc("main\n", returncode=0)
        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[head_proc, sym_proc],
        ):
            from forge.core.daemon_helpers import _get_current_branch

            result = await _get_current_branch("/repo")
        assert result == "main"

    @pytest.mark.asyncio
    async def test_empty_repo_returns_main(self):
        """When both commands fail, returns 'main'."""
        fail_proc = _make_proc("", returncode=128)
        sym_fail = _make_proc("", returncode=128)
        with patch(
            "forge.core.daemon_helpers.async_subprocess",
            new_callable=AsyncMock,
            side_effect=[fail_proc, sym_fail],
        ):
            from forge.core.daemon_helpers import _get_current_branch

            result = await _get_current_branch("/repo")
        assert result == "main"


class TestComputeWorktreePath:
    """compute_worktree_path() returns correct paths for single and multi-repo setups."""

    def test_compute_worktree_path_single_repo(self):
        """Single default repo (repo_count=1, repo_id='default') returns flat path."""
        result = compute_worktree_path("/Users/dev/myproject", "default", "task-1")
        assert result == "/Users/dev/myproject/.forge/worktrees/task-1"

    def test_compute_worktree_path_multi_repo(self):
        """Multi-repo (repo_count > 1) returns nested path with repo_id."""
        result = compute_worktree_path(
            "/Users/dev/myproject",
            "backend",
            "task-1",
            repo_count=2,
        )
        assert result == "/Users/dev/myproject/.forge/worktrees/backend/task-1"

    def test_compute_worktree_path_default_with_high_count(self):
        """repo_id='default' but repo_count > 1 still produces a nested path."""
        result = compute_worktree_path(
            "/Users/dev/myproject",
            "default",
            "task-1",
            repo_count=3,
        )
        assert result == "/Users/dev/myproject/.forge/worktrees/default/task-1"

    def test_compute_worktree_path_explicit_single_repo(self):
        """repo_count=1 with non-default repo_id nests the path."""
        result = compute_worktree_path(
            "/Users/dev/myproject",
            "frontend",
            "task-2",
            repo_count=1,
        )
        assert result == "/Users/dev/myproject/.forge/worktrees/frontend/task-2"

    def test_compute_worktree_path_default_repo_count_is_one(self):
        """Default repo_count value of 1 with 'default' repo_id returns flat path."""
        result = compute_worktree_path("/workspace", "default", "6c42538b-task-1")
        assert result == "/workspace/.forge/worktrees/6c42538b-task-1"


class TestParseForgeLearning:
    """Tests for _parse_forge_learning parser."""

    def test_valid_json(self):
        text = 'Some output\nFORGE_LEARNING:\n{"trigger": "bad import", "resolution": "fixed import path", "files": ["a.py"]}'
        result = _parse_forge_learning(text)
        assert result is not None
        assert result["trigger"] == "bad import"

    def test_no_marker(self):
        assert _parse_forge_learning("just normal output") is None

    def test_none_input(self):
        assert _parse_forge_learning(None) is None

    def test_malformed_json(self):
        text = "FORGE_LEARNING:\n{not valid json}"
        assert _parse_forge_learning(text) is None

    def test_missing_required_field(self):
        text = 'FORGE_LEARNING:\n{"trigger": "something"}'
        assert _parse_forge_learning(text) is None

    def test_missing_files(self):
        text = 'FORGE_LEARNING:\n{"trigger": "bad import", "resolution": "fixed it"}'
        assert _parse_forge_learning(text) is None

    def test_empty_files_list(self):
        text = 'FORGE_LEARNING:\n{"trigger": "bad import", "resolution": "fixed it", "files": []}'
        assert _parse_forge_learning(text) is None

    def test_with_trailing_text(self):
        """Agent text after the learning block should not prevent parsing."""
        text = (
            "Some output\nFORGE_LEARNING:\n"
            '{"trigger": "bad import", "resolution": "fixed import path", "files": ["a.py"]}\n'
            "And then the agent kept talking about other stuff here."
        )
        result = _parse_forge_learning(text)
        assert result is not None
        assert result["trigger"] == "bad import"

    def test_with_markdown_fences(self):
        text = 'FORGE_LEARNING:\n```json\n{"trigger": "bad import", "resolution": "fixed it", "files": ["a.py"]}\n```'
        result = _parse_forge_learning(text)
        assert result is not None
        assert result["trigger"] == "bad import"


# ── ProviderEvent-based helper tests ──────────────────────────────────


class TestExtractTextProviderEvent:
    """Test _extract_text with ProviderEvent input."""

    def test_text_event_returns_text(self):
        event = ProviderEvent(kind=EventKind.TEXT, text="Hello world")
        assert _extract_text(event) == "Hello world"

    def test_text_event_skips_json(self):
        event = ProviderEvent(kind=EventKind.TEXT, text='{"key": "value"}')
        assert _extract_text(event) is None

    def test_text_event_skips_empty(self):
        event = ProviderEvent(kind=EventKind.TEXT, text="   ")
        assert _extract_text(event) is None

    def test_tool_use_event_returns_none(self):
        event = ProviderEvent(kind=EventKind.TOOL_USE, tool_name="Read")
        assert _extract_text(event) is None

    def test_error_event_returns_none(self):
        event = ProviderEvent(kind=EventKind.ERROR, text="something broke")
        assert _extract_text(event) is None


class TestExtractActivityProviderEvent:
    """Test _extract_activity with ProviderEvent input."""

    def test_text_event_returns_text(self):
        event = ProviderEvent(kind=EventKind.TEXT, text="Analyzing code")
        assert _extract_activity(event) == "Analyzing code"

    def test_text_event_skips_json(self):
        event = ProviderEvent(kind=EventKind.TEXT, text='[{"items": []}]')
        assert _extract_activity(event) is None

    def test_tool_use_returns_formatted_activity(self):
        import json

        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="Read",
            tool_input=json.dumps({"file_path": "/src/models/user.py"}),
        )
        result = _extract_activity(event)
        assert result is not None
        assert "Reading" in result
        assert "user.py" in result

    def test_tool_use_bash_returns_activity(self):
        import json

        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="Bash",
            tool_input=json.dumps({"command": "pytest tests/ -x"}),
        )
        result = _extract_activity(event)
        assert result is not None
        assert "pytest" in result

    def test_tool_use_grep_returns_activity(self):
        import json

        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="Grep",
            tool_input=json.dumps({"pattern": "def main"}),
        )
        result = _extract_activity(event)
        assert result is not None
        assert "def main" in result

    def test_status_event_is_transient_not_persistent_activity(self):
        event = ProviderEvent(kind=EventKind.STATUS, status="thinking")
        assert _extract_activity(event) is None

    def test_tool_use_accepts_normalized_lowercase_names(self):
        import json

        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="read",
            tool_input=json.dumps({"file_path": "/src/models/user.py"}),
        )
        result = _extract_activity(event)
        assert result is not None
        assert "Reading" in result
        assert "user.py" in result

    def test_tool_use_accepts_raw_command_strings(self):
        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="bash",
            tool_input="pytest forge/core/daemon_helpers_test.py -q",
        )
        result = _extract_activity(event)
        assert result is not None
        assert "pytest" in result

    def test_tool_use_extracts_file_path_from_change_list(self):
        import json

        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="edit",
            tool_input=json.dumps([{"path": "/workspace/backend/src/app.py", "kind": "replace"}]),
        )
        result = _extract_activity(event)
        assert result == "✏️ Editing src/app.py"

    def test_tool_use_write_uses_writing_label(self):
        import json

        event = ProviderEvent(
            kind=EventKind.TOOL_USE,
            tool_name="write",
            tool_input=json.dumps({"path": "/workspace/backend/src/new_file.py"}),
        )
        result = _extract_activity(event)
        assert result == "✏️ Writing src/new_file.py"


class TestHumanizeModelSpec:
    def test_claude_model_is_humanized(self):
        assert _humanize_model_spec("claude:sonnet") == "Claude Sonnet"

    def test_openai_model_is_humanized(self):
        assert _humanize_model_spec("openai:gpt-5.4-mini") == "GPT-5.4 Mini"

    def test_claude_opus(self):
        assert _humanize_model_spec("claude:opus") == "Claude Opus"

    def test_claude_haiku(self):
        assert _humanize_model_spec("claude:haiku") == "Claude Haiku"

    def test_openai_gpt54(self):
        assert _humanize_model_spec("openai:gpt-5.4") == "GPT-5.4"

    def test_openai_codex(self):
        assert _humanize_model_spec("openai:gpt-5.3-codex") == "GPT-5.3 Codex"

    def test_openai_o3(self):
        assert _humanize_model_spec("openai:o3") == "o3"

    def test_bare_alias_sonnet(self):
        # bare 'sonnet' is parsed via ModelSpec.parse() into claude:sonnet
        assert _humanize_model_spec("sonnet") == "Claude Sonnet"

    def test_empty_string(self):
        assert _humanize_model_spec("") == ""

    def test_model_spec_object(self):
        # accepts ModelSpec directly
        from forge.providers.base import ModelSpec

        spec = ModelSpec(provider="claude", model="opus")
        assert _humanize_model_spec(spec) == "Claude Opus"


class TestFormatRoutingSummary:
    def test_all_claude_default(self):
        # full Claude routing string
        result = format_routing_summary(
            "claude:opus", "claude:haiku", "claude:sonnet", "claude:opus", "claude:sonnet"
        )
        expected = "Routing: Planner Claude Opus | Agent (L/M/H) Claude Haiku/Claude Sonnet/Claude Opus | Review Claude Sonnet"
        assert result == expected

    def test_mixed_providers(self):
        # Claude planner + OpenAI reviewer
        result = format_routing_summary(
            "claude:opus", "claude:haiku", "claude:sonnet", "claude:opus", "openai:gpt-5.4"
        )
        expected = "Routing: Planner Claude Opus | Agent (L/M/H) Claude Haiku/Claude Sonnet/Claude Opus | Review GPT-5.4"
        assert result == expected

    def test_with_reasoning_effort(self):
        # appends '(high reasoning)'
        result = format_routing_summary(
            "claude:opus",
            "claude:haiku",
            "claude:sonnet",
            "claude:opus",
            "claude:sonnet",
            reviewer_effort="high",
        )
        expected = "Routing: Planner Claude Opus | Agent (L/M/H) Claude Haiku/Claude Sonnet/Claude Opus | Review Claude Sonnet (high reasoning)"
        assert result == expected

    def test_without_reasoning_effort(self):
        # no suffix
        result = format_routing_summary(
            "claude:opus",
            "claude:haiku",
            "claude:sonnet",
            "claude:opus",
            "claude:sonnet",
            reviewer_effort=None,
        )
        expected = "Routing: Planner Claude Opus | Agent (L/M/H) Claude Haiku/Claude Sonnet/Claude Opus | Review Claude Sonnet"
        assert result == expected
