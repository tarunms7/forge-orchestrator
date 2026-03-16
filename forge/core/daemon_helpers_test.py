"""Tests for daemon_helpers — git diff utilities and context helpers."""

import logging
import subprocess

from unittest.mock import MagicMock, call, patch

import pytest

from forge.core.daemon_helpers import (
    _extract_implementation_summary,
    _find_related_test_files,
    _get_changed_files_vs_main,
    _get_diff_stats,
    _get_diff_vs_main,
    _is_pytest_cmd,
    _load_conventions_md,
    _parse_forge_question,
    _run_git,
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

    def test_with_commit_messages_and_agent_summary(self):
        """Combines commit messages with agent summary."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        log_proc = _make_proc("feat: add auth\nfix: handle edge case\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, log_proc]):
            result = _extract_implementation_summary(
                "/repo/wt", "Added authentication module", pipeline_branch="forge/pipeline-abc",
            )

        assert "feat: add auth; fix: handle edge case" in result
        assert "Added authentication module" in result
        assert len(result) <= 300

    def test_with_commit_messages_only(self):
        """Uses commit messages when agent summary is generic 'Task completed'."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        log_proc = _make_proc("feat: add new endpoint\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, log_proc]):
            result = _extract_implementation_summary(
                "/repo/wt", "Task completed", pipeline_branch="forge/pipeline-abc",
            )

        assert "feat: add new endpoint" in result
        # Generic "Task completed" should be excluded
        assert "Task completed" not in result

    def test_without_pipeline_branch_uses_fallback(self):
        """Falls back to --not --remotes when pipeline_branch is None."""
        log_proc = _make_proc("chore: initial setup\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[log_proc]):
            result = _extract_implementation_summary("/repo/wt", "Task completed")

        assert "chore: initial setup" in result

    def test_with_pipeline_branch_that_resolves(self):
        """Uses pipeline_branch..HEAD when the ref resolves."""
        verify_ok = _make_proc("abc123\n", returncode=0)
        log_proc = _make_proc("feat: add login\nfeat: add logout\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, log_proc]):
            result = _extract_implementation_summary(
                "/repo/wt", "Task completed", pipeline_branch="forge/pipeline-abc",
            )

        assert "feat: add login; feat: add logout" in result

    def test_pipeline_branch_not_found_falls_back(self):
        """Falls back to --not --remotes when pipeline_branch can't be resolved."""
        verify_fail = _make_proc("", returncode=128)
        log_fallback = _make_proc("fix: something\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_fail, log_fallback]):
            result = _extract_implementation_summary(
                "/repo/wt", "Fixed the thing", pipeline_branch="forge/missing",
            )

        assert "fix: something" in result
        assert "Fixed the thing" in result

    def test_no_commits_no_summary_returns_fallback(self):
        """Returns generic fallback when no commit messages and no agent summary."""
        log_proc = _make_proc("", returncode=0)

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[log_proc]):
            result = _extract_implementation_summary("/repo/wt", "Task completed")

        assert "no detailed summary" in result.lower()

    def test_truncates_to_300_chars(self):
        """Summary is capped at 300 characters."""
        long_messages = "\n".join([f"feat: implement feature number {i}" for i in range(50)])
        verify_ok = _make_proc("abc123\n", returncode=0)
        log_proc = _make_proc(long_messages + "\n")

        with patch("forge.core.daemon_helpers.subprocess.run", side_effect=[verify_ok, log_proc]):
            result = _extract_implementation_summary(
                "/repo/wt", "A very detailed agent summary that goes on and on",
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

    def test_co_located_test_found(self, tmp_path):
        """foo.py → foo_test.py (same directory)."""
        (tmp_path / "forge" / "core").mkdir(parents=True)
        (tmp_path / "forge" / "core" / "foo.py").touch()
        (tmp_path / "forge" / "core" / "foo_test.py").touch()

        result = _find_related_test_files(
            str(tmp_path), ["forge/core/foo.py"],
        )
        assert result == ["forge/core/foo_test.py"]

    def test_test_dir_convention(self, tmp_path):
        """src/foo.py → src/tests/test_foo.py."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").touch()
        (tmp_path / "src" / "tests").mkdir()
        (tmp_path / "src" / "tests" / "test_foo.py").touch()

        result = _find_related_test_files(
            str(tmp_path), ["src/foo.py"],
        )
        assert result == ["src/tests/test_foo.py"]

    def test_root_tests_convention(self, tmp_path):
        """src/foo.py → tests/test_foo.py (root-level tests dir)."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_foo.py").touch()

        result = _find_related_test_files(
            str(tmp_path), ["src/foo.py"],
        )
        assert result == ["tests/test_foo.py"]

    def test_changed_file_is_test(self, tmp_path):
        """Test files themselves are included directly."""
        (tmp_path / "forge" / "core").mkdir(parents=True)
        (tmp_path / "forge" / "core" / "foo_test.py").touch()

        result = _find_related_test_files(
            str(tmp_path), ["forge/core/foo_test.py"],
        )
        assert result == ["forge/core/foo_test.py"]

    def test_changed_file_test_prefix(self, tmp_path):
        """test_foo.py style test files are included directly."""
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_bar.py").touch()

        result = _find_related_test_files(
            str(tmp_path), ["tests/test_bar.py"],
        )
        assert result == ["tests/test_bar.py"]

    def test_no_test_files_found(self, tmp_path):
        """Returns empty list when no test files exist for the changed files."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").touch()
        # No test files anywhere

        result = _find_related_test_files(
            str(tmp_path), ["src/foo.py"],
        )
        assert result == []

    def test_non_python_files_ignored(self, tmp_path):
        """Non-.py files are skipped."""
        result = _find_related_test_files(
            str(tmp_path), ["README.md", "package.json"],
        )
        assert result == []

    def test_multiple_changed_files(self, tmp_path):
        """Multiple changed files accumulate their test files."""
        (tmp_path / "forge" / "core").mkdir(parents=True)
        (tmp_path / "forge" / "core" / "foo.py").touch()
        (tmp_path / "forge" / "core" / "foo_test.py").touch()
        (tmp_path / "forge" / "core" / "bar.py").touch()
        (tmp_path / "forge" / "core" / "bar_test.py").touch()

        result = _find_related_test_files(
            str(tmp_path), ["forge/core/foo.py", "forge/core/bar.py"],
        )
        assert result == ["forge/core/bar_test.py", "forge/core/foo_test.py"]

    def test_deduplicates_test_files(self, tmp_path):
        """Same test file found via different paths is only included once."""
        (tmp_path / "forge" / "core").mkdir(parents=True)
        (tmp_path / "forge" / "core" / "foo.py").touch()
        (tmp_path / "forge" / "core" / "foo_test.py").touch()

        result = _find_related_test_files(
            str(tmp_path),
            ["forge/core/foo.py", "forge/core/foo_test.py"],
        )
        assert result == ["forge/core/foo_test.py"]


class TestParseForgeQuestion:
    """_parse_forge_question() extracts structured question data from agent output."""

    def test_valid_question_at_end(self):
        text = "I analyzed the code.\n\nFORGE_QUESTION:\n{\"question\": \"Which pattern?\", \"suggestions\": [\"A\", \"B\"], \"impact\": \"high\"}"
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
        text = "Done.\n\nFORGE_QUESTION:\n```json\n{\"question\": \"Which?\", \"suggestions\": [\"A\"]}\n```"
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

    def test_question_mid_output_ignored(self):
        text = 'FORGE_QUESTION:\n{"question": "?", "suggestions": ["A"]}\n\nThen I continued working and wrote code.'
        result = _parse_forge_question(text)
        assert result is None

    def test_empty_text_returns_none(self):
        result = _parse_forge_question("")
        assert result is None

    def test_none_text_returns_none(self):
        result = _parse_forge_question(None)
        assert result is None


class TestRunGit:
    """_run_git() wraps subprocess with logging and error handling."""

    def test_success_returns_result(self):
        """On exit 0, returns the CompletedProcess without raising."""
        proc = _make_proc("abc123\n", returncode=0)
        with patch("forge.core.daemon_helpers.subprocess.run", return_value=proc) as mock_run:
            result = _run_git(["rev-parse", "HEAD"], cwd="/repo")

        assert result is proc
        mock_run.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            cwd="/repo",
            capture_output=True,
            text=True,
        )

    def test_check_true_raises_on_failure(self):
        """With check=True (default), non-zero exit raises CalledProcessError."""
        proc = _make_proc("", returncode=128)
        proc.stderr = "fatal: not a git repository"
        with patch("forge.core.daemon_helpers.subprocess.run", return_value=proc):
            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                _run_git(["rev-parse", "HEAD"], cwd="/bad")

        assert exc_info.value.returncode == 128

    def test_check_false_returns_result_on_failure(self, caplog):
        """With check=False, non-zero exit returns result and logs warning."""
        proc = _make_proc("", returncode=1)
        proc.stderr = "error: something went wrong"
        with patch("forge.core.daemon_helpers.subprocess.run", return_value=proc):
            with caplog.at_level(logging.WARNING, logger="forge"):
                result = _run_git(["status"], cwd="/repo", check=False)

        assert result is proc
        assert "returned 1" in caplog.text


class TestFindRelatedTestFilesScoped:
    """Tests for _find_related_test_files with allowed_files filtering."""

    def test_in_scope_test_included(self, tmp_path):
        """Test file in allowed_files is included."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        in_scope, out_of_scope = _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=["src/auth.py", "tests/test_auth.py"],
        )
        assert "tests/test_auth.py" in in_scope
        assert len(out_of_scope) == 0

    def test_out_of_scope_test_excluded(self, tmp_path):
        """Test file NOT in allowed_files is excluded."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        in_scope, out_of_scope = _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=["src/auth.py"],  # test_auth.py NOT listed
        )
        assert "tests/test_auth.py" not in in_scope
        assert "tests/test_auth.py" in out_of_scope

    def test_no_allowed_files_returns_all(self, tmp_path):
        """When allowed_files is None, all discovered tests are in-scope."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        result = _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=None,
        )
        # Backward compat: returns flat list when allowed_files is None
        assert "tests/test_auth.py" in result

    def test_newly_created_test_is_in_scope(self, tmp_path):
        """A test file created by the agent (not on base branch) is in-scope."""
        # Set up a git repo to simulate new file detection
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True)

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_new.py").write_text("# new test")
        (tmp_path / "new.py").write_text("# new module")

        # Stage and commit the new test on a branch
        subprocess.run(["git", "checkout", "-b", "work"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add new"], cwd=tmp_path, capture_output=True)

        in_scope, out_of_scope = _find_related_test_files(
            str(tmp_path),
            changed_files=["new.py"],
            allowed_files=["new.py"],  # test_new.py NOT in allowed list
            base_ref="main",
        )
        # test_new.py was created by agent (not on main), so it's in-scope
        assert "tests/test_new.py" in in_scope
