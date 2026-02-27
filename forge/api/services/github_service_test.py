"""Tests for the GitHub service: PR description builder and PR creation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from forge.api.services.github_service import build_pr_description, create_pr


class TestBuildPrDescription:
    """Tests for build_pr_description."""

    def test_includes_task_description(self):
        """Description section should contain the task description."""
        result = build_pr_description(
            task={"description": "Implement dark mode"},
            subtasks=[],
        )
        assert "## Description" in result
        assert "Implement dark mode" in result

    def test_includes_subtask_table(self):
        """Subtask table should include each subtask."""
        subtasks = [
            {"id": "1", "description": "Add toggle", "status": "complete"},
            {"id": "2", "description": "Style theme", "status": "pending"},
        ]
        result = build_pr_description(
            task={"description": "Dark mode"},
            subtasks=subtasks,
        )
        assert "## Subtasks" in result
        assert "Add toggle" in result
        assert "Style theme" in result
        assert "| # | Description | Status |" in result

    def test_includes_review_results_passed(self):
        """Review gate section should show 'Passed' when review passed."""
        result = build_pr_description(
            task={"description": "Feature X"},
            subtasks=[],
            review_results={"passed": True, "summary": "All checks green"},
        )
        assert "## Review Gate" in result
        assert "Passed" in result
        assert "All checks green" in result

    def test_includes_review_results_failed(self):
        """Review gate section should show 'Failed' when review failed."""
        result = build_pr_description(
            task={"description": "Feature X"},
            subtasks=[],
            review_results={"passed": False, "summary": "Lint errors found"},
        )
        assert "Failed" in result
        assert "Lint errors found" in result

    def test_includes_footer(self):
        """Description should include the auto-generated footer."""
        result = build_pr_description(
            task={"description": "Test"},
            subtasks=[],
        )
        assert "Forge Orchestrator" in result

    def test_no_review_section_when_none(self):
        """Review gate section should be absent when no review results."""
        result = build_pr_description(
            task={"description": "Test"},
            subtasks=[],
            review_results=None,
        )
        assert "## Review Gate" not in result


class TestCreatePr:
    """Tests for create_pr with mocked subprocess."""

    @pytest.mark.asyncio
    async def test_create_pr_success(self):
        """create_pr should return URL and number on success."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (
            b"https://github.com/owner/repo/pull/42\n",
            b"",
        )

        with patch("forge.api.services.github_service.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await create_pr(
                repo_path="/tmp/repo",
                branch="feat/dark-mode",
                title="Add dark mode",
                body="## Description\nDark mode support",
            )

        assert result["url"] == "https://github.com/owner/repo/pull/42"
        assert result["number"] == 42

    @pytest.mark.asyncio
    async def test_create_pr_failure_raises(self):
        """create_pr should raise RuntimeError on gh failure."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (
            b"",
            b"error: not a git repository",
        )

        with patch("forge.api.services.github_service.asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="gh pr create failed"):
                await create_pr(
                    repo_path="/tmp/repo",
                    branch="feat/broken",
                    title="Bad PR",
                    body="This will fail",
                )
