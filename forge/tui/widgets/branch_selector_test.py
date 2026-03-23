"""Tests for BranchSelector and BranchInput widgets."""

from __future__ import annotations

import pytest

from forge.tui.widgets.branch_selector import (
    _branch_label,
    _truncate,
)


class TestHelpers:
    """Tests for helper functions."""

    def test_truncate_short_name(self):
        assert _truncate("main") == "main"

    def test_truncate_long_name(self):
        long = "a" * 60
        result = _truncate(long)
        assert len(result) == 50
        assert result.endswith("…")

    def test_truncate_exact_limit(self):
        exact = "a" * 50
        assert _truncate(exact) == exact

    def test_branch_label_current(self):
        label = _branch_label("main", current="main")
        plain = label.plain
        assert "●" in plain
        assert "main" in plain
        assert "(current)" in plain

    def test_branch_label_non_current(self):
        label = _branch_label("develop", current="main")
        plain = label.plain
        assert "●" not in plain
        assert "develop" in plain

    def test_branch_label_remote(self):
        label = _branch_label("origin/feat/api", current="main")
        plain = label.plain
        assert "(remote)" in plain

    def test_branch_label_long_name_truncated(self):
        long_name = "feat/" + "a" * 60
        label = _branch_label(long_name, current="main")
        assert "…" in label.plain


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
    subprocess.run(["git", "branch", "develop"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "branch", "feat/auth"], cwd=repo, capture_output=True, check=True)

    branches = await list_local_branches(str(repo))
    assert len(branches) >= 3
    assert "develop" in branches
    assert "feat/auth" in branches
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


@pytest.mark.asyncio
async def test_list_local_branches_return_current(tmp_path):
    """return_current=True returns (branches, current) tuple."""
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

    result = await list_local_branches(str(repo), return_current=True)
    assert isinstance(result, tuple)
    branches, current = result
    assert current == "main"
    assert "main" in branches


@pytest.mark.asyncio
async def test_list_local_branches_empty_repo(tmp_path):
    """Empty repo with no commits returns fallback."""
    import subprocess

    from forge.core.daemon_helpers import list_local_branches

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

    branches = await list_local_branches(str(repo))
    assert branches == ["main"]
