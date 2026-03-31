"""Tests for individual gauntlet scenario functions."""

import pytest

from forge.gauntlet.fixtures import create_fixture_workspace
from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.scenarios.happy_path import run_happy_path
from forge.gauntlet.scenarios.integration_failure import run_integration_failure
from forge.gauntlet.scenarios.multi_repo_contracts import run_multi_repo_contracts
from forge.gauntlet.scenarios.resume_after_interrupt import run_resume_after_interrupt
from forge.gauntlet.scenarios.review_gate_failure import run_review_gate_failure


@pytest.fixture()
def workspace(tmp_path):
    """Create a fixture workspace and return (base_dir, repos)."""
    base = str(tmp_path)
    repos = create_fixture_workspace(base)
    return base, repos


class TestHappyPath:
    async def test_returns_passed(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_happy_path(pipeline, repos)
        assert result.passed is True
        assert result.name == "happy_path"

    async def test_all_stages_present(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_happy_path(pipeline, repos)
        stage_names = [s.name for s in result.stages]
        assert stage_names == ["preflight", "planning", "contracts", "execution", "review", "integration"]

    async def test_all_assertions_pass(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_happy_path(pipeline, repos)
        assert len(result.assertions) > 0
        for a in result.assertions:
            assert a.passed is True, f"Assertion {a.name} failed: {a.message}"

    async def test_artifacts_populated(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_happy_path(pipeline, repos)
        assert "workspace_dir" in result.artifacts
        assert "stage_count" in result.artifacts


class TestMultiRepoContracts:
    async def test_returns_passed(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_multi_repo_contracts(pipeline, repos)
        assert result.passed is True
        assert result.name == "multi_repo_contracts"

    async def test_validates_cross_repo_contract(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_multi_repo_contracts(pipeline, repos)
        assertion_names = {a.name for a in result.assertions}
        assert "contracts_contain_cross_repo" in assertion_names
        cross_repo = next(a for a in result.assertions if a.name == "contracts_contain_cross_repo")
        assert cross_repo.passed is True

    async def test_validates_type_contracts(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_multi_repo_contracts(pipeline, repos)
        type_check = next(a for a in result.assertions if a.name == "type_contracts_reference_shared_types")
        assert type_check.passed is True

    async def test_validates_task_repo_assignments(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_multi_repo_contracts(pipeline, repos)
        repo_check = next(a for a in result.assertions if a.name == "tasks_have_correct_repo_assignments")
        assert repo_check.passed is True


class TestResumeAfterInterrupt:
    async def test_returns_passed(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_resume_after_interrupt(pipeline, repos)
        assert result.passed is True
        assert result.name == "resume_after_interrupt"

    async def test_verifies_state_preservation(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_resume_after_interrupt(pipeline, repos)
        preserved = next(a for a in result.assertions if a.name == "completed_stages_preserved")
        assert preserved.passed is True

    async def test_all_six_stages_ran(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_resume_after_interrupt(pipeline, repos)
        assert len(result.stages) == 6

    async def test_records_interrupt_artifact(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_resume_after_interrupt(pipeline, repos)
        assert result.artifacts.get("interrupt_after_stage") == "execution"


class TestReviewGateFailure:
    async def test_scenario_passes(self, workspace):
        """Scenario passes when it correctly detects the review failure."""
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_review_gate_failure(pipeline, repos)
        assert result.passed is True
        assert result.name == "review_gate_failure"

    async def test_review_stage_detected_as_failed(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_review_gate_failure(pipeline, repos)
        review_check = next(a for a in result.assertions if a.name == "review_stage_fails")
        assert review_check.passed is True

    async def test_pipeline_stops_at_review(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_review_gate_failure(pipeline, repos)
        stop_check = next(a for a in result.assertions if a.name == "pipeline_stops_at_review")
        assert stop_check.passed is True

    async def test_failed_stage_artifact(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_review_gate_failure(pipeline, repos)
        assert result.artifacts.get("failed_stage") == "review"


class TestIntegrationFailure:
    async def test_scenario_passes(self, workspace):
        """Scenario passes when it correctly detects the integration failure."""
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_integration_failure(pipeline, repos)
        assert result.passed is True
        assert result.name == "integration_failure"

    async def test_integration_detected_as_failed(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_integration_failure(pipeline, repos)
        int_check = next(a for a in result.assertions if a.name == "integration_check_fails")
        assert int_check.passed is True

    async def test_all_six_stages_ran(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_integration_failure(pipeline, repos)
        assert len(result.stages) == 6

    async def test_pre_integration_stages_pass(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_integration_failure(pipeline, repos)
        pre_check = next(a for a in result.assertions if a.name == "all_stages_before_integration_pass")
        assert pre_check.passed is True

    async def test_failed_stage_artifact(self, workspace):
        base, repos = workspace
        pipeline = MockPipeline(workspace_dir=base, repos=repos)
        result = await run_integration_failure(pipeline, repos)
        assert result.artifacts.get("failed_stage") == "integration"
