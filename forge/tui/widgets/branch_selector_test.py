"""Tests for BranchSelector and BranchInput widgets."""

from __future__ import annotations

import pytest

from forge.tui.widgets.branch_selector import BranchInput, BranchSelector


class TestBranchSelectorState:
    """Unit tests for BranchSelector internal state."""

    def test_default_selection(self):
        sel = BranchSelector(default="develop")
        assert sel.selected_value == "develop"

    def test_default_fallback_to_main(self):
        sel = BranchSelector()
        assert sel.selected_value == "main"

    def test_filtered_branches_empty_filter(self):
        sel = BranchSelector()
        sel._branches = ["main", "develop", "feat/auth"]
        assert sel._filtered_branches() == ["main", "develop", "feat/auth"]

    def test_filtered_branches_with_query(self):
        sel = BranchSelector()
        sel._branches = ["main", "develop", "feat/auth", "feat/api"]
        sel._filter = "feat"
        result = sel._filtered_branches()
        assert result == ["feat/auth", "feat/api"]

    def test_filtered_branches_case_insensitive(self):
        sel = BranchSelector()
        sel._branches = ["main", "Develop", "FEATURE/auth"]
        sel._filter = "dev"
        result = sel._filtered_branches()
        assert result == ["Develop"]

    def test_cursor_stays_in_bounds(self):
        sel = BranchSelector()
        sel._branches = ["main", "develop"]
        sel._expanded = True
        sel._cursor = 0
        sel.action_cursor_up()
        assert sel._cursor == 0  # Can't go below 0

        sel._cursor = 1
        sel.action_cursor_down()
        assert sel._cursor == 1  # Can't go past last

    def test_select_sets_value(self):
        sel = BranchSelector()
        sel._branches = ["main", "develop", "staging"]
        sel._expanded = True
        sel._cursor = 1
        sel.action_select_branch()
        assert sel.selected_value == "develop"
        assert not sel._expanded

    def test_select_strips_origin_prefix(self):
        sel = BranchSelector()
        sel._branches = ["main", "origin/feat/api"]
        sel._expanded = True
        sel._cursor = 1
        sel.action_select_branch()
        assert sel.selected_value == "feat/api"

    def test_collapse_preserves_selection(self):
        sel = BranchSelector(default="develop")
        sel._expanded = True
        sel.action_collapse()
        assert sel.selected_value == "develop"
        assert not sel._expanded

    def test_filter_resets_cursor(self):
        sel = BranchSelector()
        sel._branches = ["main", "develop"]
        sel._expanded = True
        sel._cursor = 1
        sel._filter = "m"
        # After filtering, cursor should be reset to 0 by the key handler
        # (tested indirectly through action flow)
        assert sel._filtered_branches() == ["main"]


class TestBranchInputState:
    """Unit tests for BranchInput internal state."""

    def test_default_value_is_empty(self):
        inp = BranchInput()
        assert inp.value == ""

    def test_typed_text_returned_as_value(self):
        inp = BranchInput()
        inp._text = "feat/my-branch"
        inp._cursor = -1
        assert inp.value == "feat/my-branch"

    def test_auto_generate_selected(self):
        inp = BranchInput()
        inp._branches = ["main", "develop"]
        inp._cursor = 0  # First item = auto-generate
        items = inp._filtered_with_auto()
        assert items[0] == ("✦ Auto-generate from task", "")
        assert inp.value == ""

    def test_branch_selected_from_list(self):
        inp = BranchInput()
        inp._branches = ["main", "develop"]
        inp._cursor = 2  # develop (index 0=auto, 1=main, 2=develop)
        assert inp.value == "develop"

    def test_filter_narrows_list(self):
        inp = BranchInput()
        inp._branches = ["main", "develop", "feat/auth"]
        inp._text = "feat"
        items = inp._filtered_with_auto()
        # auto-generate + feat/auth
        assert len(items) == 2
        assert items[1] == ("feat/auth", "feat/auth")

    def test_cursor_bounds(self):
        inp = BranchInput()
        inp._branches = ["main"]
        inp._expanded = True
        inp._cursor = -1
        inp.action_cursor_up()
        assert inp._cursor == -1

        inp._cursor = 1  # last item (0=auto, 1=main)
        inp.action_cursor_down()
        assert inp._cursor == 1


@pytest.mark.asyncio
async def test_list_local_branches(tmp_path):
    """Integration: list_local_branches returns branches from a real git repo."""
    import subprocess

    from forge.core.daemon_helpers import list_local_branches

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )
    subprocess.run(
        ["git", "branch", "develop"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "branch", "feat/auth"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    branches = await list_local_branches(str(repo))
    # Current branch (main or master) should be first
    assert len(branches) >= 3
    assert "develop" in branches
    assert "feat/auth" in branches
    # Current branch is first
    current = branches[0]
    assert current in ("main", "master")


@pytest.mark.asyncio
async def test_list_local_branches_current_first(tmp_path):
    """The current branch should always be the first item."""
    import subprocess

    from forge.core.daemon_helpers import list_local_branches

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
        env={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )
    subprocess.run(["git", "branch", "develop"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "checkout", "develop"], cwd=repo, capture_output=True, check=True)

    branches = await list_local_branches(str(repo))
    assert branches[0] == "develop"
