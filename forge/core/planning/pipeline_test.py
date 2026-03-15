# forge/core/planning/pipeline_test.py
import pytest
from forge.core.models import TaskGraph
from forge.core.planning.models import CodebaseMap, ValidationResult
from forge.core.planning.pipeline import PlanningPipeline, PlanningResult
from forge.core.planning.scout import ScoutResult
from forge.core.planning.architect import ArchitectResult
from forge.core.planning.detailer import DetailerResult


class MockScout:
    async def run(self, **kwargs):
        return ScoutResult(codebase_map=CodebaseMap(architecture_summary="Test", key_modules=[]), cost_usd=0.05, input_tokens=500, output_tokens=300)


class MockArchitect:
    async def run(self, **kwargs):
        graph = TaskGraph(tasks=[
            {"id": "t1", "title": "T1", "description": "Detailed description for task one with test info", "files": ["a.py"]},
            {"id": "t2", "title": "T2", "description": "Detailed description for task two with test info", "files": ["b.py"], "depends_on": ["t1"]},
        ])
        return ArchitectResult(task_graph=graph, cost_usd=0.10, input_tokens=1000, output_tokens=500)


class MockDetailerFactory:
    async def run_all(self, *, tasks, **kwargs):
        return [DetailerResult(task_id=t.id, enriched_description=f"Enriched: {t.description}", cost_usd=0.02, input_tokens=200, output_tokens=100, success=True) for t in tasks]


@pytest.mark.asyncio
async def test_pipeline_produces_task_graph():
    pipeline = PlanningPipeline(scout=MockScout(), architect=MockArchitect(), detailer_factory=MockDetailerFactory())
    result = await pipeline.run(user_input="Build X", spec_text="Spec", snapshot_text="Snapshot")
    assert isinstance(result.task_graph, TaskGraph)
    assert len(result.task_graph.tasks) == 2
    assert "Enriched:" in result.task_graph.tasks[0].description
    assert result.total_cost_usd > 0


@pytest.mark.asyncio
async def test_pipeline_falls_back_when_scout_fails():
    class FailingScout:
        async def run(self, **kwargs):
            return ScoutResult(codebase_map=None, cost_usd=0.05, input_tokens=500, output_tokens=300)
    pipeline = PlanningPipeline(scout=FailingScout(), architect=MockArchitect(), detailer_factory=MockDetailerFactory())
    result = await pipeline.run(user_input="Build X", spec_text="Spec", snapshot_text="Snapshot")
    assert result.task_graph is not None


@pytest.mark.asyncio
async def test_pipeline_reports_cost_breakdown():
    pipeline = PlanningPipeline(scout=MockScout(), architect=MockArchitect(), detailer_factory=MockDetailerFactory())
    result = await pipeline.run(user_input="x", spec_text="x", snapshot_text="x")
    assert result.cost_breakdown["scout"] > 0
    assert result.cost_breakdown["architect"] > 0
    assert result.cost_breakdown["detailers"] > 0
