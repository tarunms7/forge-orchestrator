"""Planning pipeline orchestrator: Scout → Architect → Detailers → Validator."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from forge.core.models import TaskGraph
from forge.core.planning.architect import Architect, ArchitectResult
from forge.core.planning.detailer import DetailerFactory
from forge.core.planning.models import CodebaseMap, PlanFeedback, ValidationResult
from forge.core.planning.scout import Scout, ScoutResult
from forge.core.planning.validator import validate_plan

logger = logging.getLogger("forge.planning.pipeline")

_MAX_VALIDATOR_ITERATIONS = 3


@dataclass
class PlanningResult:
    task_graph: TaskGraph | None
    codebase_map: CodebaseMap | None
    cost_breakdown: dict[str, float] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    validation_result: ValidationResult | None = None


class PlanningPipeline:
    def __init__(self, scout, architect, detailer_factory, on_message: Callable | None = None, on_question: Callable | None = None) -> None:
        self._scout = scout
        self._architect = architect
        self._detailer_factory = detailer_factory
        self._on_message = on_message
        self._on_question = on_question

    async def run(self, *, user_input: str, spec_text: str, snapshot_text: str, conventions: str = "") -> PlanningResult:
        cost_breakdown: dict[str, float] = {}

        # Stage 1: Scout
        scout_result = await self._scout.run(user_input=user_input, spec_text=spec_text, snapshot_text=snapshot_text)
        cost_breakdown["scout"] = scout_result.cost_usd
        codebase_map = scout_result.codebase_map
        if codebase_map is None:
            codebase_map = CodebaseMap(architecture_summary="(Scout failed — no deep analysis available)", key_modules=[])

        # Stage 2: Architect
        architect_result = await self._architect.run(user_input=user_input, spec_text=spec_text, codebase_map=codebase_map, conventions=conventions, on_question=self._on_question)
        cost_breakdown["architect"] = architect_result.cost_usd
        if architect_result.task_graph is None:
            return PlanningResult(task_graph=None, codebase_map=codebase_map, cost_breakdown=cost_breakdown, total_cost_usd=sum(cost_breakdown.values()))

        task_graph = architect_result.task_graph

        # Stage 3: Detailers
        detailer_results = await self._detailer_factory.run_all(tasks=task_graph.tasks, codebase_map=codebase_map, conventions=conventions)
        cost_breakdown["detailers"] = sum(r.cost_usd for r in detailer_results)

        # Apply enriched descriptions
        enriched_map = {r.task_id: r.enriched_description for r in detailer_results}
        for task in task_graph.tasks:
            if task.id in enriched_map:
                task.description = enriched_map[task.id]

        # Stage 4: Validator (with feedback loop)
        validation_result = validate_plan(task_graph, codebase_map, spec_text)
        cost_breakdown["validator"] = 0.0

        iteration = 1
        prev_major_ids: set[str] = set()

        while validation_result.status == "fail" and iteration < _MAX_VALIDATOR_ITERATIONS:
            major_issues = [i for i in validation_result.issues if i.severity == "major"]
            fatal_issues = [i for i in validation_result.issues if i.severity == "fatal"]
            if fatal_issues:
                break
            if not major_issues:
                task_graph = self._apply_minor_fixes(task_graph, validation_result)
                validation_result = validate_plan(task_graph, codebase_map, spec_text)
                break

            current_major_ids = {f"{i.category}:{','.join(sorted(i.affected_tasks))}" for i in major_issues}
            new_majors = current_major_ids - prev_major_ids
            if iteration > 1 and new_majors:
                break
            prev_major_ids = current_major_ids

            affected_task_ids = set()
            for issue in major_issues:
                affected_task_ids.update(issue.affected_tasks)
            preserved = [t.id for t in task_graph.tasks if t.id not in affected_task_ids]
            plan_feedback = PlanFeedback(
                iteration=iteration + 1, issues=major_issues, preserved_tasks=preserved,
                replan_scope=f"Replan tasks: {', '.join(sorted(affected_task_ids))}."
            )

            architect_result = await self._architect.run(
                user_input=user_input, spec_text=spec_text, codebase_map=codebase_map,
                conventions=conventions, feedback=plan_feedback, on_question=self._on_question,
            )
            cost_breakdown["architect"] += architect_result.cost_usd
            if architect_result.task_graph is None:
                break
            task_graph = architect_result.task_graph

            changed_tasks = [t for t in task_graph.tasks if t.id in affected_task_ids]
            if changed_tasks:
                new_details = await self._detailer_factory.run_all(tasks=changed_tasks, codebase_map=codebase_map, conventions=conventions)
                cost_breakdown["detailers"] += sum(r.cost_usd for r in new_details)
                new_enriched = {r.task_id: r.enriched_description for r in new_details}
                for task in task_graph.tasks:
                    if task.id in new_enriched:
                        task.description = new_enriched[task.id]

            validation_result = validate_plan(task_graph, codebase_map, spec_text)
            iteration += 1

        return PlanningResult(
            task_graph=task_graph, codebase_map=codebase_map, cost_breakdown=cost_breakdown,
            total_cost_usd=sum(cost_breakdown.values()), validation_result=validation_result,
        )

    def _apply_minor_fixes(self, graph: TaskGraph, result: ValidationResult) -> TaskGraph:
        task_map = {t.id: t for t in graph.tasks}
        for fix in result.minor_fixes:
            task = task_map.get(fix.task_id)
            if not task:
                continue
            if fix.field == "description" and isinstance(fix.fixed_value, str):
                task.description = fix.fixed_value
            elif fix.field == "files" and isinstance(fix.fixed_value, list):
                task.files = fix.fixed_value
        return graph
