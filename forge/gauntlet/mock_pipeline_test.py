"""Tests for forge.gauntlet.mock_pipeline."""

import os

import pytest

from forge.gauntlet.fixtures import create_fixture_workspace
from forge.gauntlet.mock_pipeline import MockPipeline


@pytest.fixture
def workspace(tmp_path):
    return create_fixture_workspace(str(tmp_path))


@pytest.fixture
def pipeline(tmp_path, workspace):
    return MockPipeline(workspace_dir=str(tmp_path), repos=workspace)


@pytest.fixture
def failing_pipeline(tmp_path, workspace):
    """Factory for pipelines that fail at a specific stage."""

    def _make(fail_at: str) -> MockPipeline:
        return MockPipeline(
            workspace_dir=str(tmp_path), repos=workspace, fail_at=fail_at
        )

    return _make


class TestRunPreflight:
    @pytest.mark.asyncio
    async def test_passes_with_valid_repos(self, pipeline):
        result = await pipeline.run_preflight()
        assert result.passed is True
        assert result.name == "preflight"
        assert result.duration_s >= 0

    @pytest.mark.asyncio
    async def test_fails_with_missing_repo(self, tmp_path, workspace):
        workspace["backend"] = "/nonexistent/path"
        p = MockPipeline(workspace_dir=str(tmp_path), repos=workspace)
        result = await p.run_preflight()
        assert result.passed is False
        assert "Missing repos" in result.details

    @pytest.mark.asyncio
    async def test_fails_when_injected(self, failing_pipeline):
        p = failing_pipeline("preflight")
        result = await p.run_preflight()
        assert result.passed is False
        assert "Injected" in result.details


class TestRunPlanning:
    @pytest.mark.asyncio
    async def test_creates_task_graph(self, pipeline):
        result, graph = await pipeline.run_planning("Fix bugs")
        assert result.passed is True
        assert graph is not None
        assert len(graph.tasks) == 3

    @pytest.mark.asyncio
    async def test_task_ids(self, pipeline):
        _, graph = await pipeline.run_planning("Fix bugs")
        ids = [t.id for t in graph.tasks]
        assert "fix-backend-bug" in ids
        assert "fix-frontend-import" in ids
        assert "update-shared-types" in ids

    @pytest.mark.asyncio
    async def test_task_repos(self, pipeline):
        _, graph = await pipeline.run_planning("Fix bugs")
        repo_map = {t.id: t.repo for t in graph.tasks}
        assert repo_map["fix-backend-bug"] == "backend"
        assert repo_map["fix-frontend-import"] == "frontend"
        assert repo_map["update-shared-types"] == "shared-types"

    @pytest.mark.asyncio
    async def test_task_files(self, pipeline):
        _, graph = await pipeline.run_planning("Fix bugs")
        file_map = {t.id: t.files for t in graph.tasks}
        assert file_map["fix-backend-bug"] == ["app.py", "test_app.py"]
        assert file_map["fix-frontend-import"] == ["index.js"]
        assert file_map["update-shared-types"] == ["types.py"]

    @pytest.mark.asyncio
    async def test_fails_when_injected(self, failing_pipeline):
        p = failing_pipeline("planning")
        result, graph = await p.run_planning("Fix bugs")
        assert result.passed is False
        assert graph is None

    @pytest.mark.asyncio
    async def test_tracks_cost(self, pipeline):
        await pipeline.run_planning("Fix bugs")
        assert pipeline.cost_usd > 0


class TestRunContracts:
    @pytest.mark.asyncio
    async def test_generates_contracts(self, pipeline):
        _, graph = await pipeline.run_planning("Fix bugs")
        result = await pipeline.run_contracts(graph)
        assert result.passed is True
        assert result.name == "contracts"

    @pytest.mark.asyncio
    async def test_contract_set_structure(self, pipeline):
        _, graph = await pipeline.run_planning("Fix bugs")
        await pipeline.run_contracts(graph)
        cs = pipeline._contract_set
        assert len(cs.api_contracts) == 1
        assert cs.api_contracts[0].id == "contract-api-calculate"
        assert cs.api_contracts[0].method == "POST"
        assert cs.api_contracts[0].path == "/calculate"
        assert cs.api_contracts[0].producer_task_id == "fix-backend-bug"
        assert cs.api_contracts[0].consumer_task_ids == ["fix-frontend-import"]

    @pytest.mark.asyncio
    async def test_type_contracts(self, pipeline):
        _, graph = await pipeline.run_planning("Fix bugs")
        await pipeline.run_contracts(graph)
        cs = pipeline._contract_set
        assert len(cs.type_contracts) == 2
        names = [tc.name for tc in cs.type_contracts]
        assert "CalculationRequest" in names
        assert "CalculationResponse" in names

    @pytest.mark.asyncio
    async def test_fails_when_injected(self, failing_pipeline):
        p = failing_pipeline("contracts")
        _, graph = await p.run_planning("Fix bugs")
        result = await p.run_contracts(graph)
        assert result.passed is False


class TestRunExecution:
    @pytest.mark.asyncio
    async def test_creates_branches(self, pipeline, workspace):
        _, graph = await pipeline.run_planning("Fix bugs")
        result = await pipeline.run_execution(graph)
        assert result.passed is True

        # Verify branches were created in backend
        import subprocess

        out = subprocess.run(
            ["git", "branch"],
            cwd=workspace["backend"],
            capture_output=True,
            text=True,
        )
        assert "forge/fix-backend-bug" in out.stdout

    @pytest.mark.asyncio
    async def test_commits_changes(self, pipeline, workspace):
        _, graph = await pipeline.run_planning("Fix bugs")
        await pipeline.run_execution(graph)

        import subprocess

        out = subprocess.run(
            ["git", "log", "--all", "--oneline"],
            cwd=workspace["backend"],
            capture_output=True,
            text=True,
        )
        assert "fix:" in out.stdout

    @pytest.mark.asyncio
    async def test_fails_when_injected(self, failing_pipeline):
        p = failing_pipeline("execution")
        _, graph = await p.run_planning("Fix bugs")
        result = await p.run_execution(graph)
        assert result.passed is False


class TestRunReview:
    @pytest.mark.asyncio
    async def test_passes(self, pipeline):
        _, graph = await pipeline.run_planning("Fix bugs")
        result = await pipeline.run_review(graph)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_fails_when_injected(self, failing_pipeline):
        p = failing_pipeline("review")
        _, graph = await p.run_planning("Fix bugs")
        result = await p.run_review(graph)
        assert result.passed is False


class TestRunIntegration:
    @pytest.mark.asyncio
    async def test_passes(self, pipeline):
        result = await pipeline.run_integration()
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_fails_when_injected(self, failing_pipeline):
        p = failing_pipeline("integration")
        result = await p.run_integration()
        assert result.passed is False


class TestRunFull:
    @pytest.mark.asyncio
    async def test_happy_path(self, pipeline):
        results = await pipeline.run_full("Fix all bugs")
        assert len(results) == 6
        assert all(r.passed for r in results)
        stage_names = [r.name for r in results]
        assert stage_names == [
            "preflight",
            "planning",
            "contracts",
            "execution",
            "review",
            "integration",
        ]

    @pytest.mark.asyncio
    async def test_stops_on_failure(self, failing_pipeline):
        p = failing_pipeline("contracts")
        results = await p.run_full("Fix all bugs")
        assert len(results) == 3  # preflight, planning, contracts
        assert results[-1].passed is False
        assert results[-1].name == "contracts"

    @pytest.mark.asyncio
    async def test_preflight_failure_stops_early(self, failing_pipeline):
        p = failing_pipeline("preflight")
        results = await p.run_full("Fix all bugs")
        assert len(results) == 1
        assert results[0].name == "preflight"
        assert results[0].passed is False

    @pytest.mark.asyncio
    async def test_cost_accumulated(self, pipeline):
        await pipeline.run_full("Fix all bugs")
        assert pipeline.cost_usd > 0


class TestChaosMode:
    @pytest.mark.asyncio
    async def test_chaos_runs_without_error(self, tmp_path):
        workspace = create_fixture_workspace(str(tmp_path / "ws"))
        p = MockPipeline(
            workspace_dir=str(tmp_path / "ws"),
            repos=workspace,
            chaos=True,
        )
        results = await p.run_full("Fix all bugs")
        # Chaos adds delays but shouldn't change pass/fail
        assert all(r.passed for r in results)
