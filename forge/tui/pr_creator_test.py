from unittest.mock import AsyncMock, patch

import pytest

from forge.tui.pr_creator import (
    MultiRepoPrResult,
    _add_related_prs_comment,
    create_prs_multi_repo,
    generate_pr_body,
)


def test_generate_pr_body_includes_tasks():
    tasks = [{"title": "Auth", "added": 89, "removed": 4, "files": 3}]
    body = generate_pr_body(tasks=tasks, time="8m", cost=0.42, questions=[])
    assert "Auth" in body
    assert "+89/-4" in body
    assert "$0.42" in body


def test_generate_pr_body_includes_questions():
    questions = [{"question": "Which ORM?", "answer": "SQLAlchemy 2.0"}]
    body = generate_pr_body(tasks=[], time="5m", cost=0.10, questions=questions)
    assert "Which ORM?" in body
    assert "SQLAlchemy 2.0" in body


def test_pr_body_with_failed_tasks():
    body = generate_pr_body(
        tasks=[
            {"title": "Auth", "added": 100, "removed": 10, "files": 3},
            {"title": "Docs", "added": 50, "removed": 0, "files": 1},
        ],
        failed_tasks=[
            {"title": "API", "error": "timed out (5 attempts)"},
        ],
        time="12m 30s",
        cost=6.57,
        questions=[],
    )
    assert "Completed Tasks" in body
    assert "✅" in body
    assert "Failed Tasks" in body
    assert "❌" in body
    assert "API" in body
    assert "timed out" in body


def test_pr_body_with_details():
    """Tasks with description, implementation_summary, and file_list show details."""
    tasks = [{
        "title": "Add auth middleware",
        "description": "Implement JWT authentication middleware",
        "implementation_summary": "Added JWT validation to all API routes",
        "added": 120,
        "removed": 5,
        "files": 4,
        "file_list": ["forge/api/auth.py", "forge/api/middleware.py"],
    }]
    body = generate_pr_body(tasks=tasks, time="5m", cost=1.00, questions=[])
    assert "Add auth middleware" in body
    assert "+120/-5" in body
    assert "<details>" in body
    assert "JWT authentication middleware" in body
    assert "Added JWT validation" in body
    assert "`forge/api/auth.py`" in body
    assert "`forge/api/middleware.py`" in body


def test_pr_body_no_details_when_empty():
    """Tasks without description/summary/files don't show empty details block."""
    tasks = [{"title": "Quick fix", "added": 3, "removed": 1, "files": 1}]
    body = generate_pr_body(tasks=tasks, time="1m", cost=0.10, questions=[])
    assert "Quick fix" in body
    assert "<details>" not in body


def test_pr_body_zero_stats_no_stats_shown():
    """Tasks with 0 added/removed/files don't show +0/-0."""
    tasks = [{"title": "Config change", "added": 0, "removed": 0, "files": 0}]
    body = generate_pr_body(tasks=tasks, time="1m", cost=0.05, questions=[])
    assert "Config change" in body
    assert "+0/-0" not in body


# ---------- Chunk 2: generate_pr_body multi-repo tests ----------


class TestGeneratePrBodyMultiRepo:
    def test_generate_pr_body_multi_repo(self):
        """related_prs inserts a Related PRs section between Summary and Tasks."""
        body = generate_pr_body(
            tasks=[{"title": "Auth", "added": 10, "removed": 2, "files": 1}],
            time="5m",
            cost=1.00,
            questions=[],
            related_prs={"backend": "https://github.com/org/backend/pull/1"},
            repo_id="frontend",
        )
        assert "## Related PRs" in body
        assert "- **backend**: https://github.com/org/backend/pull/1" in body
        # Related PRs should appear between Summary and Tasks
        summary_idx = body.index("## Summary")
        related_idx = body.index("## Related PRs")
        tasks_idx = body.index("## Tasks")
        assert summary_idx < related_idx < tasks_idx
        # repo_id in summary line
        assert "[frontend]" in body

    def test_generate_pr_body_single_repo(self):
        """Without related_prs, output is identical to before (backward compat)."""
        body = generate_pr_body(
            tasks=[{"title": "Auth", "added": 10, "removed": 2, "files": 1}],
            time="5m",
            cost=1.00,
            questions=[],
        )
        assert "## Related PRs" not in body
        assert "[" not in body.split("\n")[1]  # no repo_id in summary


# ---------- Chunk 3: create_prs_multi_repo tests ----------


class TestCreatePrsMultiRepo:
    @pytest.mark.asyncio
    async def test_create_prs_multi_repo_groups_tasks(self):
        """Tasks are grouped by repo_id and each repo gets its own PR."""
        summaries = [
            {"title": "T1", "repo_id": "backend", "cost_usd": 1.0},
            {"title": "T2", "repo_id": "frontend", "cost_usd": 2.0},
        ]
        repos = {
            "backend": {"project_dir": "/tmp/backend", "base_branch": "main"},
            "frontend": {"project_dir": "/tmp/frontend", "base_branch": "main"},
        }
        branches = {"backend": "forge/backend", "frontend": "forge/frontend"}

        with (
            patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True) as mock_push,
            patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock) as mock_create,
            patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock) as mock_comment,
        ):
            mock_create.side_effect = [
                "https://github.com/org/backend/pull/1",
                "https://github.com/org/frontend/pull/2",
            ]
            result = await create_prs_multi_repo(
                task_summaries=summaries,
                repos=repos,
                pipeline_branches=branches,
                description="Multi-repo test",
                elapsed_str="5m",
                questions=[],
            )

        assert len(result.pr_urls) == 2
        assert "backend" in result.pr_urls
        assert "frontend" in result.pr_urls
        assert len(result.failures) == 0
        # Cross-link comments should have been called for each PR
        assert mock_comment.call_count == 2

    @pytest.mark.asyncio
    async def test_create_prs_partial_failure(self):
        """If push fails for one repo, others still get PRs."""
        summaries = [
            {"title": "T1", "repo_id": "backend", "cost_usd": 1.0},
            {"title": "T2", "repo_id": "frontend", "cost_usd": 2.0},
        ]
        repos = {
            "backend": {"project_dir": "/tmp/backend", "base_branch": "main"},
            "frontend": {"project_dir": "/tmp/frontend", "base_branch": "main"},
        }
        branches = {"backend": "forge/backend", "frontend": "forge/frontend"}

        with (
            patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock) as mock_push,
            patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock) as mock_create,
            patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock),
        ):
            # backend push fails, frontend succeeds
            mock_push.side_effect = [False, True]
            mock_create.return_value = "https://github.com/org/frontend/pull/2"
            result = await create_prs_multi_repo(
                task_summaries=summaries,
                repos=repos,
                pipeline_branches=branches,
                description="Partial failure",
                elapsed_str="5m",
                questions=[],
            )

        assert "backend" in result.failures
        assert "frontend" in result.pr_urls
        assert len(result.pr_urls) == 1

    @pytest.mark.asyncio
    async def test_pr_title_includes_repo_id(self):
        """Multi-repo PRs include [repo_id] in title."""
        summaries = [
            {"title": "T1", "repo_id": "backend", "cost_usd": 0},
            {"title": "T2", "repo_id": "frontend", "cost_usd": 0},
        ]
        repos = {
            "backend": {"project_dir": "/tmp/b"},
            "frontend": {"project_dir": "/tmp/f"},
        }
        branches = {"backend": "b1", "frontend": "b2"}

        with (
            patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True),
            patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock, return_value="https://github.com/x/pull/1") as mock_create,
            patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock),
        ):
            await create_prs_multi_repo(
                task_summaries=summaries,
                repos=repos,
                pipeline_branches=branches,
                description="Test",
                elapsed_str="1m",
                questions=[],
            )

        titles = [call.args[1] for call in mock_create.call_args_list]
        assert any("[backend]" in t for t in titles)
        assert any("[frontend]" in t for t in titles)

    @pytest.mark.asyncio
    async def test_pr_title_single_repo_no_suffix(self):
        """Single-repo PR title has no [repo_id] suffix."""
        summaries = [{"title": "T1", "repo_id": "default", "cost_usd": 0}]
        repos = {"default": {"project_dir": "/tmp/d"}}
        branches = {"default": "b1"}

        with (
            patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True),
            patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock, return_value="https://github.com/x/pull/1") as mock_create,
            patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock),
        ):
            await create_prs_multi_repo(
                task_summaries=summaries,
                repos=repos,
                pipeline_branches=branches,
                description="Solo",
                elapsed_str="1m",
                questions=[],
            )

        title = mock_create.call_args.args[1]
        assert title == "Forge: Solo"
        assert "[" not in title

    @pytest.mark.asyncio
    async def test_push_per_repo(self):
        """push_branch is called with correct project_dir and branch per repo."""
        summaries = [
            {"title": "T1", "repo_id": "a", "cost_usd": 0},
            {"title": "T2", "repo_id": "b", "cost_usd": 0},
        ]
        repos = {
            "a": {"project_dir": "/tmp/a"},
            "b": {"project_dir": "/tmp/b"},
        }
        branches = {"a": "branch-a", "b": "branch-b"}

        with (
            patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True) as mock_push,
            patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock, return_value="https://github.com/x/pull/1"),
            patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock),
        ):
            await create_prs_multi_repo(
                task_summaries=summaries,
                repos=repos,
                pipeline_branches=branches,
                description="Push test",
                elapsed_str="1m",
                questions=[],
            )

        push_calls = [(c.args[0], c.args[1]) for c in mock_push.call_args_list]
        assert ("/tmp/a", "branch-a") in push_calls
        assert ("/tmp/b", "branch-b") in push_calls


# ---------- Chunk 4: _add_related_prs_comment tests ----------


class TestAddRelatedPrsComment:
    @pytest.mark.asyncio
    async def test_add_related_prs_comment(self):
        """Runs gh pr comment with correct PR number and body."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await _add_related_prs_comment(
                pr_url="https://github.com/org/repo/pull/42",
                related_prs={"backend": "https://github.com/org/backend/pull/1"},
                project_dir="/tmp/repo",
            )

        mock_exec.assert_called_once()
        args = mock_exec.call_args
        # Should include pr number
        assert "42" in args.args
        # Should include gh pr comment
        assert "gh" in args.args
        assert "comment" in args.args
        # Body should contain related PR info
        body_arg = args.args[args.args.index("--body") + 1]
        assert "## Related Forge PRs" in body_arg
        assert "**backend**" in body_arg
        assert args.kwargs["cwd"] == "/tmp/repo"

    @pytest.mark.asyncio
    async def test_create_prs_cross_link_failure_non_fatal(self):
        """If cross-link comment fails, PRs are still returned successfully."""
        summaries = [
            {"title": "T1", "repo_id": "a", "cost_usd": 0},
            {"title": "T2", "repo_id": "b", "cost_usd": 0},
        ]
        repos = {
            "a": {"project_dir": "/tmp/a"},
            "b": {"project_dir": "/tmp/b"},
        }
        branches = {"a": "b1", "b": "b2"}

        with (
            patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True),
            patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock) as mock_create,
            patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock, side_effect=Exception("network error")),
        ):
            mock_create.side_effect = [
                "https://github.com/org/a/pull/1",
                "https://github.com/org/b/pull/2",
            ]
            # Should not raise despite comment failure
            result = await create_prs_multi_repo(
                task_summaries=summaries,
                repos=repos,
                pipeline_branches=branches,
                description="Cross-link test",
                elapsed_str="1m",
                questions=[],
            )

        assert len(result.pr_urls) == 2


# ---------- TestReposJsonPrUrls ----------


class TestReposJsonPrUrls:
    @pytest.mark.asyncio
    async def test_repos_json_updated_with_pr_urls(self):
        """MultiRepoPrResult correctly tracks per-repo PR URLs."""
        result = MultiRepoPrResult()
        result.pr_urls["backend"] = "https://github.com/org/backend/pull/1"
        result.pr_urls["frontend"] = "https://github.com/org/frontend/pull/2"
        assert result.pr_urls["backend"] == "https://github.com/org/backend/pull/1"
        assert result.pr_urls["frontend"] == "https://github.com/org/frontend/pull/2"
        assert len(result.failures) == 0
