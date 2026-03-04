"""Tests for daemon_helpers — git diff utilities."""

from unittest.mock import MagicMock, call, patch

from forge.core.daemon_helpers import (
    _get_changed_files_vs_main,
    _get_diff_stats,
    _get_diff_vs_main,
)


def _make_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Return a fake CompletedProcess-like mock."""
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


class TestGetDiffVsMainBaseRef:
    """_get_diff_vs_main() with explicit base_ref skips the --not --remotes heuristic."""

    def test_uses_base_ref_when_provided(self):
        """Should diff base_ref..HEAD directly, no rev-list call."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        diff_proc = _make_proc("diff --git a/foo.py b/foo.py\n+new line\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, diff_proc]) as mock_run:
            result = _get_diff_vs_main("/repo/worktrees/task-1", base_ref="forge/pipeline-abc")

        assert "new line" in result
        assert mock_run.call_count == 2
        # First call: verify ref exists
        assert mock_run.call_args_list[0] == call(
            ["git", "rev-parse", "--verify", "forge/pipeline-abc"],
            cwd="/repo/worktrees/task-1",
            capture_output=True, text=True,
        )
        # Second call: git diff base_ref HEAD
        assert mock_run.call_args_list[1] == call(
            ["git", "diff", "forge/pipeline-abc", "HEAD"],
            cwd="/repo/worktrees/task-1",
            capture_output=True, text=True,
        )

    def test_falls_back_when_base_ref_not_found(self):
        """When base_ref can't be resolved, falls back to --not --remotes heuristic."""
        verify_fail = _make_proc("", returncode=128)
        count_proc = _make_proc("1\n")
        heuristic_verify = _make_proc("def456\n", returncode=0)
        diff_proc = _make_proc("diff --git a/bar.py b/bar.py\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_fail, count_proc, heuristic_verify, diff_proc]):
            result = _get_diff_vs_main("/repo", base_ref="forge/pipeline-missing")

        assert result == "diff --git a/bar.py b/bar.py\n"

    def test_none_base_ref_uses_heuristic(self):
        """When base_ref is None, uses the commit-count heuristic directly."""
        count_proc = _make_proc("1\n")
        heuristic_verify = _make_proc("abc\n", returncode=0)
        diff_proc = _make_proc("some diff\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[count_proc, heuristic_verify, diff_proc]):
            result = _get_diff_vs_main("/repo", base_ref=None)

        assert result == "some diff\n"


class TestGetChangedFilesVsMainBaseRef:
    """_get_changed_files_vs_main() with explicit base_ref."""

    def test_uses_base_ref_when_provided(self):
        """Should use git diff --name-only base_ref HEAD."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        name_only_proc = _make_proc("foo.py\nbar.py\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, name_only_proc]) as mock_run:
            result = _get_changed_files_vs_main("/repo/wt", base_ref="forge/pipeline-abc")

        assert result == ["foo.py", "bar.py"]
        assert mock_run.call_args_list[1] == call(
            ["git", "diff", "--name-only", "forge/pipeline-abc", "HEAD"],
            cwd="/repo/wt",
            capture_output=True, text=True,
        )

    def test_falls_back_when_base_ref_not_found(self):
        """Falls back to heuristic when base_ref doesn't resolve."""
        verify_fail = _make_proc("", returncode=128)
        count_proc = _make_proc("1\n")
        heuristic_verify = _make_proc("abc\n", returncode=0)
        name_only_proc = _make_proc("baz.py\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_fail, count_proc, heuristic_verify, name_only_proc]):
            result = _get_changed_files_vs_main("/repo", base_ref="forge/missing")

        assert result == ["baz.py"]

    def test_none_base_ref_uses_heuristic(self):
        """When base_ref is None, uses heuristic directly."""
        count_proc = _make_proc("2\n")
        heuristic_verify = _make_proc("abc\n", returncode=0)
        name_only_proc = _make_proc("x.py\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[count_proc, heuristic_verify, name_only_proc]):
            result = _get_changed_files_vs_main("/repo")

        assert result == ["x.py"]


class TestGetDiffStatsPipelineBranch:
    """_get_diff_stats() with a valid pipeline_branch uses git diff --shortstat."""

    def test_uses_pipeline_branch_when_ref_resolves(self):
        """Should return per-task stats from `git diff --shortstat <branch> HEAD`."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        shortstat = _make_proc(" 3 files changed, 42 insertions(+), 7 deletions(-)\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, shortstat]) as mock_run:
            result = _get_diff_stats("/repo/worktrees/task-1", pipeline_branch="forge/pipeline-abc")

        assert result == {"linesAdded": 42, "linesRemoved": 7}
        # First call: verify ref; second call: git diff --shortstat
        assert mock_run.call_count == 2
        shortstat_call = mock_run.call_args_list[1]
        assert shortstat_call == call(
            ["git", "diff", "--shortstat", "forge/pipeline-abc", "HEAD"],
            cwd="/repo/worktrees/task-1",
            capture_output=True,
            text=True,
        )

    def test_insertions_only(self):
        """Handles a diff with insertions but no deletions."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        shortstat = _make_proc(" 1 file changed, 10 insertions(+)\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, shortstat]):
            result = _get_diff_stats("/repo", pipeline_branch="forge/pipeline-abc")

        assert result == {"linesAdded": 10, "linesRemoved": 0}

    def test_deletions_only(self):
        """Handles a diff with deletions but no insertions."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        shortstat = _make_proc(" 2 files changed, 5 deletions(-)\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, shortstat]):
            result = _get_diff_stats("/repo", pipeline_branch="forge/pipeline-abc")

        assert result == {"linesAdded": 0, "linesRemoved": 5}

    def test_empty_shortstat_returns_zeros(self):
        """When the diff is empty (no changes), returns zeros."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        shortstat = _make_proc("")  # empty = no diff

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, shortstat]):
            result = _get_diff_stats("/repo", pipeline_branch="forge/pipeline-abc")

        assert result == {"linesAdded": 0, "linesRemoved": 0}


class TestGetDiffStatsFallback:
    """_get_diff_stats() falls back to commit-count heuristic when pipeline branch is missing."""

    def test_falls_back_when_pipeline_branch_not_found(self):
        """When git rev-parse --verify fails, falls back to HEAD~N approach."""
        verify_fail = _make_proc("", returncode=128)   # branch not found
        count_proc = _make_proc("2\n")                  # 2 local commits
        base_verify = _make_proc("def456\n", returncode=0)
        shortstat = _make_proc(" 1 file changed, 15 insertions(+), 3 deletions(-)\n")

        side_effects = [verify_fail, count_proc, base_verify, shortstat]
        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=side_effects):
            result = _get_diff_stats("/repo", pipeline_branch="forge/pipeline-missing")

        assert result == {"linesAdded": 15, "linesRemoved": 3}

    def test_no_pipeline_branch_uses_commit_count(self):
        """When pipeline_branch is None, uses HEAD~N heuristic directly."""
        count_proc = _make_proc("1\n")
        base_verify = _make_proc("abc\n", returncode=0)
        shortstat = _make_proc(" 2 files changed, 100 insertions(+), 20 deletions(-)\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[count_proc, base_verify, shortstat]):
            result = _get_diff_stats("/repo", pipeline_branch=None)

        assert result == {"linesAdded": 100, "linesRemoved": 20}

    def test_root_commit_uses_empty_tree(self):
        """Fallback handles root commits by diffing against the empty tree."""
        count_proc = _make_proc("1\n")
        base_verify = _make_proc("", returncode=128)          # HEAD~1 doesn't exist
        empty_tree_proc = _make_proc("4b825dc642cb6eb9a060e54bf8d69288fbee4904\n")
        shortstat = _make_proc(" 1 file changed, 50 insertions(+)\n")

        side_effects = [count_proc, base_verify, empty_tree_proc, shortstat]
        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=side_effects):
            result = _get_diff_stats("/repo", pipeline_branch=None)

        assert result == {"linesAdded": 50, "linesRemoved": 0}

    def test_invalid_commit_count_defaults_to_one(self):
        """When git rev-list returns non-integer output, defaults commit_count to 1."""
        count_proc = _make_proc("bad-output\n")
        base_verify = _make_proc("abc\n", returncode=0)
        shortstat = _make_proc(" 1 file changed, 5 insertions(+)\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[count_proc, base_verify, shortstat]):
            result = _get_diff_stats("/repo")

        assert result == {"linesAdded": 5, "linesRemoved": 0}

    def test_zero_commit_count_defaults_to_one(self):
        """When rev-list returns 0, bumps commit_count to 1 to avoid HEAD~0 == HEAD."""
        count_proc = _make_proc("0\n")
        base_verify = _make_proc("abc\n", returncode=0)
        shortstat = _make_proc(" 1 file changed, 8 insertions(+), 2 deletions(-)\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[count_proc, base_verify, shortstat]):
            result = _get_diff_stats("/repo")

        assert result == {"linesAdded": 8, "linesRemoved": 2}
