"""Unified Planner: single-agent planning with full codebase tool access.

Replaces the 4-stage pipeline (Scout → Architect → Detailer → Validator)
with a single Opus agent that can Read/Glob/Grep the codebase directly,
eliminating the information-loss problem of working from a CodebaseMap summary.

The agent:
  - Explores the codebase (what Scout did)
  - Decomposes into tasks with detailed descriptions (what Architect + Detailer did)
  - Asks clarifying questions when uncertain (via FORGE_QUESTION protocol)

Structural validation (cycles, file conflicts, dependency validity, granularity)
runs locally in Python after the agent finishes — no LLM call, milliseconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from claude_code_sdk import ClaudeCodeOptions
from pydantic import ValidationError as PydanticValidationError

from forge.agents.adapter import _build_question_protocol
from forge.core import sdk_helpers
from forge.core.daemon_helpers import _parse_forge_question
from forge.core.models import TaskGraph
from forge.core.planning.models import ValidationResult
from forge.core.planning.validator import validate_plan
from forge.core.sanitize import extract_json_block
from forge.providers import (
    ExecutionMode,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    ResumeState,
    WorkspaceRoots,
)
from forge.providers.restrictions import PLANNER_TOOL_POLICY

if TYPE_CHECKING:
    from forge.core.cost_registry import CostRegistry
    from forge.providers.registry import ProviderRegistry

logger = logging.getLogger("forge.planning.unified")
sdk_query = sdk_helpers.sdk_query  # Backward-compat alias for legacy tests/mocks.

@dataclass
class UnifiedPlannerResult:
    """Output of the unified planning stage."""

    task_graph: TaskGraph | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    validation_result: ValidationResult | None = None
    cost_breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def total_cost_usd(self) -> float:
        return self.cost_usd


class UnifiedPlanner:
    """Single-agent planner with full codebase tool access.

    Replaces Scout + Architect + Detailer + Validator with one Opus call.
    The agent reads the actual codebase (Read/Glob/Grep), decomposes
    tasks, writes detailed descriptions, and asks questions — all in
    one session with no information loss.
    """

    def __init__(
        self,
        model: str | ModelSpec = "opus",
        cwd: str | None = None,
        max_retries: int = 3,
        max_turns: int = 30,
        autonomy: str = "balanced",
        question_limit: int = 5,
        repo_ids: set[str] | None = None,
        registry: ProviderRegistry | None = None,
        cost_registry: CostRegistry | None = None,
    ) -> None:
        self._model_spec = ModelSpec.parse(model) if isinstance(model, str) else model
        self._cwd = cwd
        self._max_retries = max_retries
        self._max_turns = max_turns
        self._autonomy = autonomy
        self._question_limit = question_limit
        self._repo_ids = repo_ids
        self._registry = registry
        self._cost_registry = cost_registry

    async def run(
        self,
        *,
        user_input: str,
        spec_text: str,
        snapshot_text: str,
        conventions: str = "",
        lessons_block: str = "",
        on_message: Callable | None = None,
        on_question: Callable | None = None,
    ) -> UnifiedPlannerResult:
        """Run the unified planning agent.

        Args:
            user_input: The user's task description.
            spec_text: Optional spec document content.
            snapshot_text: Project snapshot (directory tree, file list).
            conventions: Optional project conventions / prompt modifier.
            on_message: Streaming callback for progress updates.
            on_question: Async callback for human questions.

        Returns:
            UnifiedPlannerResult with TaskGraph and validation.
        """
        total_cost = 0.0
        total_input = 0
        total_output = 0
        questions_asked = 0
        resume_state: ResumeState | None = None
        feedback: str | None = None

        provider = None
        catalog_entry = None
        workspace = WorkspaceRoots(primary_cwd=self._cwd or ".")
        if self._registry is not None:
            provider = self._registry.get_for_model(self._model_spec)
            catalog_entry = self._registry.get_catalog_entry(self._model_spec)

        # Bridge async on_message callback from sync on_event
        def _on_event(event: ProviderEvent) -> None:
            if on_message is not None:
                asyncio.ensure_future(on_message(event))

        for attempt in range(self._max_retries):
            logger.info("UnifiedPlanner attempt %d/%d", attempt + 1, self._max_retries)

            if resume_state:
                prompt = f"User answered: {feedback}\n\nNow produce the TaskGraph JSON."
            else:
                prompt = self._build_prompt(
                    user_input, spec_text, snapshot_text, conventions, feedback
                )

            question_protocol = _build_question_protocol(
                autonomy=self._autonomy,
                remaining=self._question_limit - questions_asked,
            )
            system_prompt = _build_unified_system_prompt(
                question_protocol,
                lessons_block=lessons_block,
                repo_ids=self._repo_ids,
            )
            provider_result = None

            try:
                if provider is None or catalog_entry is None:
                    sdk_result = await sdk_query(
                        prompt=prompt,
                        options=ClaudeCodeOptions(
                            system_prompt=system_prompt,
                            cwd=self._cwd or ".",
                            model=self._model_spec.model,
                            max_turns=self._max_turns,
                            disallowed_tools=["Edit", "Write"],
                        ),
                        on_message=on_message,
                    )
                    raw = (
                        getattr(sdk_result, "result", None)
                        or getattr(sdk_result, "result_text", None)
                        or ""
                    )
                    total_cost += float(getattr(sdk_result, "cost_usd", 0.0) or 0.0)
                    total_input += int(getattr(sdk_result, "input_tokens", 0) or 0)
                    total_output += int(getattr(sdk_result, "output_tokens", 0) or 0)
                else:
                    handle = provider.start(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        catalog_entry=catalog_entry,
                        execution_mode=ExecutionMode.INTELLIGENCE,
                        tool_policy=PLANNER_TOOL_POLICY,
                        output_contract=OutputContract(format="forge_question_capable"),
                        workspace=workspace,
                        max_turns=self._max_turns,
                        reasoning_effort=self._registry.settings.resolve_reasoning_effort(
                            "planner",
                            "high",
                        ),
                        resume_state=resume_state,
                        on_event=_on_event,
                    )
                    provider_result = await handle.result()
                    # Calculate cost from provider result
                    attempt_cost = 0.0
                    if provider_result.provider_reported_cost_usd is not None:
                        attempt_cost = provider_result.provider_reported_cost_usd
                    total_cost += attempt_cost
                    total_input += provider_result.input_tokens
                    total_output += provider_result.output_tokens
                    raw = provider_result.text or ""
            except Exception as e:
                logger.warning("UnifiedPlanner provider error on attempt %d: %s", attempt + 1, e)
                feedback = f"Provider error: {e}"
                resume_state = None
                continue

            if (
                provider_result is not None
                and provider_result.is_error
                and not provider_result.text
            ):
                continue

            # Check for FORGE_QUESTION — agent wants human input
            q_data = _parse_forge_question(raw)
            if q_data and on_question and questions_asked < self._question_limit:
                questions_asked += 1
                answer = await on_question(q_data)
                if answer:
                    resume_state = provider_result.resume_state if provider_result is not None else None
                    feedback = answer
                    continue

            # Try to parse TaskGraph
            resume_state = None
            graph, error = self._parse(raw)
            if graph is not None:
                logger.info(
                    "UnifiedPlanner succeeded on attempt %d (%d tasks)",
                    attempt + 1,
                    len(graph.tasks),
                )

                # Run deterministic validation (no LLM, milliseconds)
                from forge.core.planning.models import CodebaseMap

                minimal_map = CodebaseMap(
                    architecture_summary="(generated by unified planner)",
                    key_modules=[],
                )
                validation_result = validate_plan(
                    graph, minimal_map, spec_text, repo_ids=self._repo_ids
                )

                # Auto-fix minor issues if possible
                if validation_result.status == "pass" or not any(
                    i.severity in ("major", "fatal") for i in validation_result.issues
                ):
                    return UnifiedPlannerResult(
                        task_graph=graph,
                        cost_usd=total_cost,
                        input_tokens=total_input,
                        output_tokens=total_output,
                        validation_result=validation_result,
                        cost_breakdown={"planner": total_cost},
                    )

                # Major/fatal issues — give the agent one chance to fix
                issue_text = "\n".join(
                    f"- [{i.severity}] {i.description} (suggested: {i.suggested_fix})"
                    for i in validation_result.issues
                    if i.severity in ("major", "fatal")
                )
                feedback = (
                    f"Your TaskGraph has structural issues:\n{issue_text}\n\n"
                    "Fix these and produce a corrected TaskGraph JSON."
                )
                resume_state = provider_result.resume_state if provider_result is not None else None
                logger.warning(
                    "UnifiedPlanner attempt %d has %d validation issues, retrying",
                    attempt + 1,
                    len(validation_result.issues),
                )
                continue

            feedback = f"Invalid output: {error}"
            logger.warning("UnifiedPlanner attempt %d parse failed: %s", attempt + 1, error)

        logger.error("UnifiedPlanner failed after %d retries", self._max_retries)
        return UnifiedPlannerResult(
            task_graph=None,
            cost_usd=total_cost,
            input_tokens=total_input,
            output_tokens=total_output,
            cost_breakdown={"planner": total_cost},
        )

    def _build_prompt(
        self,
        user_input: str,
        spec_text: str,
        snapshot_text: str,
        conventions: str,
        feedback: str | None,
    ) -> str:
        parts = [f"## User Request\n\n{user_input}"]
        if spec_text:
            parts.append(f"## Spec Document\n\n{spec_text}")
        if snapshot_text:
            parts.append(f"## Project Snapshot\n\n{snapshot_text}")
        if conventions:
            parts.append(f"## Project Conventions\n\n{conventions}")
        if feedback:
            parts.append(f"## Previous Attempt Feedback\n\n{feedback}")
        parts.append(
            "## Your Task\n\n"
            "1. Explore the codebase as needed to understand the request.\n"
            "2. Assess the task type: simple fix, review/analysis, medium feature, or large feature.\n"
            "3. If anything is ambiguous, ask a question (FORGE_QUESTION) before planning.\n"
            "4. Produce a TaskGraph JSON with the right number of tasks for the complexity.\n"
            "   - Simple fix → 1 task. Don't over-decompose.\n"
            "   - Review → find issues, each fix = 1 task.\n"
            "   - Feature → decompose into parallel tasks."
        )
        return "\n\n".join(parts)

    def _parse(self, raw: str) -> tuple[TaskGraph | None, str | None]:
        """Extract and validate TaskGraph JSON from agent output."""
        raw = raw.strip()

        # Find ALL fenced JSON blocks and try each (last is usually best)
        blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if not blocks:
            extracted = extract_json_block(raw)
            if extracted:
                blocks = [extracted]

        last_error: str | None = None
        for candidate in reversed(blocks):
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError as e:
                last_error = f"Invalid JSON: {e}"
                continue
            try:
                graph = TaskGraph.model_validate(data)
            except PydanticValidationError as e:
                last_error = f"Schema validation failed: {e}"
                continue

            # Basic structural checks (duplicate IDs, invalid dep refs)
            task_ids = {t.id for t in graph.tasks}
            seen: set[str] = set()
            valid = True
            for t in graph.tasks:
                if t.id in seen:
                    last_error = f"Duplicate task id: '{t.id}'"
                    valid = False
                    break
                seen.add(t.id)
                for dep in t.depends_on:
                    if dep not in task_ids:
                        last_error = f"Task '{t.id}' depends on unknown task '{dep}'"
                        valid = False
                        break
                if not valid:
                    break
            # Multi-repo validation: check repo assignments and cross-repo paths
            if valid and self._repo_ids is not None:
                for t in graph.tasks:
                    task_repo = t.repo
                    if task_repo not in self._repo_ids:
                        last_error = (
                            f"Task '{t.id}' has repo='{task_repo}' but valid repos are: "
                            f"{', '.join(sorted(self._repo_ids))}"
                        )
                        valid = False
                        break
                    # Check for cross-repo file paths
                    for file_path in t.files:
                        first_segment = file_path.split("/")[0]
                        if first_segment in self._repo_ids and first_segment != task_repo:
                            last_error = (
                                f"Task '{t.id}' has file '{file_path}' that appears to reference "
                                f"repo '{first_segment}' but task is assigned to repo '{task_repo}'"
                            )
                            valid = False
                            break
                    if not valid:
                        break
            if valid:
                return graph, None

        return None, last_error or "No JSON found in output"


def _build_unified_system_prompt(
    question_protocol: str,
    lessons_block: str = "",
    repo_ids: set[str] | None = None,
) -> str:
    """Build the unified planner's system prompt."""
    multi_repo_section = ""
    if repo_ids is not None and len(repo_ids) > 1:
        sorted_repos = sorted(repo_ids)
        repo_list = ", ".join(f"`{r}`" for r in sorted_repos)
        multi_repo_section = f"""

## Multi-Repo Workspace

This workspace contains multiple repositories: {repo_list}

### CRITICAL: Repo Exclusion Rule
**Read the user's task description carefully.** If the user says to "ignore", "skip", "exclude", or says a repo "has nothing to do with" the task — those repos are OFF LIMITS. This can apply to one or multiple repos. You MUST NOT:
- Create any tasks for excluded repos
- Read or browse files in excluded repos
- Reference excluded repos in any task description
- Mention excluded repos in any task's description, title, or instructions to the agent
Violating this rule will cause the pipeline to fail. Only create tasks for repos the user explicitly wants changed.

Additionally, in the task descriptions you write for agents, include a clear note listing ALL excluded repos:
"Do NOT read, modify, or reference files in [list all excluded repos]. They are out of scope for this pipeline."
This ensures agents also respect the exclusions even if they are tempted to look at other repos.

### Repo Assignment Rules
- Every task MUST have a `"repo"` field set to one of the available repos.
- A task operates in exactly ONE repo. If work spans repos, split into separate tasks with dependencies.
- File paths are RELATIVE to the repo root (not the workspace root).

### Cross-Repo Dependencies
- If task B in repo `frontend` depends on an API from task A in repo `backend`, use `depends_on`.
- Document the interface in `integration_hints`.

### Output Schema (with repo field)

```json
{{
  "tasks": [
    {{
      "id": "task-1",
      "title": "Backend models",
      "description": "...",
      "files": ["src/models.py"],
      "depends_on": [],
      "complexity": "low",
      "repo": "{sorted_repos[0]}"
    }},
    {{
      "id": "task-2",
      "title": "Frontend components",
      "description": "...",
      "files": ["src/App.tsx"],
      "depends_on": ["task-1"],
      "complexity": "medium",
      "repo": "{sorted_repos[-1]}"
    }}
  ]
}}
```
"""

    return f"""You are the planning agent for Forge, a multi-agent coding orchestration system.

## Your Capabilities

You have FULL READ ACCESS to the codebase:
- **Glob**: Find files by pattern (e.g., "src/**/*.py", "**/*test*")
- **Grep**: Search file contents (e.g., function definitions, import patterns)
- **Read**: Read specific files
- **Bash**: Run read-only commands (git log, git diff, wc -l, find, etc.)

Use these tools to understand the codebase BEFORE planning.

## Workflow

### Phase 1: Explore (use as many turns as needed)

Start with the Project Snapshot below to understand the directory structure.
Then explore strategically:

1. **Glob** for entry points, configs, and key files related to the request
2. **Read** the most important files (interfaces, types, main modules)
3. **Grep** for specific patterns (function names, imports, API endpoints)

**Goal-directed reading**: After each file you read, ask yourself:
"Do I have enough context to plan this task?"
- If YES → stop reading, start planning
- If NO → identify the ONE specific question you can't answer, and read the ONE file
  most likely to answer it

Do NOT read files "just to be thorough." Do NOT read test files or generated files
unless the task specifically involves them.

### Phase 2: Assess Task Type

After exploration, determine the nature of the task:

**A) Simple / Small Fix** (1-3 files, clear what to do)
→ Produce a TaskGraph with a SINGLE task. No decomposition needed.
Examples: fix a bug, update a config, add a small feature, rename something.

**B) Review / Analysis** (user wants feedback on existing code or a PR)
→ Analyze the code/PR, then produce a TaskGraph with tasks to FIX the issues you found.
If you found issues: each fix becomes a task. If no issues: produce a single task
describing what was reviewed and that no changes are needed.
Examples: "review this PR", "check for bugs in auth", "audit the API endpoints".

**C) Medium Feature** (4-8 files, clear architecture)
→ Decompose into 2-4 independent tasks that can run in parallel.

**D) Large Feature** (8+ files, cross-cutting concerns)
→ Full decomposition into 3-8 tasks with dependencies and integration hints.

**Choose the SIMPLEST approach that fits.** Don't decompose a 2-file fix into 3 tasks.
Don't create a single task for work that clearly has independent parallel parts.

### Phase 3: Clarify (REQUIRED for ambiguous tasks)

Before producing a plan, evaluate:
1. Are there multiple valid interpretations of this request?
2. Would asking one question save 10 minutes of wrong work?
3. Is there a technology, pattern, or approach choice the user should decide?

If ANY answer is yes: **you MUST ask** using the FORGE_QUESTION protocol below.
Do NOT proceed with assumptions when a 30-second question would give certainty.

{question_protocol}

{lessons_block}

### Phase 4: Plan

Produce a TaskGraph as valid JSON.

## Output Schema

```json
{{
  "conventions": {{
    "styling": "...",
    "naming": "...",
    "testing": "..."
  }},
  "tasks": [
    {{
      "id": "task-1",
      "title": "Short title",
      "description": "Detailed implementation description with concrete edits, patterns to follow, test requirements, and edge cases",
      "files": ["src/file.py"],
      "depends_on": [],
      "complexity": "low"
    }}
  ],
  "integration_hints": [
    {{
      "producer_task_id": "task-1",
      "consumer_task_ids": ["task-3"],
      "interface_type": "api_endpoint",
      "description": "REST API for X",
      "endpoint_hints": ["GET /api/x"]
    }}
  ]
}}
```

## Task Rules

- Each task owns specific files. **No two independent tasks may own the same file** (unless one depends on the other).
- Use depends_on ONLY when a task genuinely needs another's output.
- complexity: "low", "medium", or "high".
- **COMPLETE file lists**: if a task's description mentions modifying a file, that file MUST
  be in the task's "files" array. Agents can ONLY edit files in their task's "files" list.
  Agents can also create/modify test files related to their owned files — those don't need
  to be in the files list.
- **NEVER create tasks for git operations** (rebase, merge, branch management). The
  orchestrator handles ALL git operations automatically.
- Every task MUST produce code changes.
- **For single-task plans**: just produce one task. Don't force decomposition.
- **For review tasks**: each issue you find that needs fixing becomes a separate task.
  If no fixes are needed, produce a single task with complexity "low" explaining that
  the review found no actionable issues.

## Task Descriptions — Be Specific

Since you have direct access to the code, write descriptions that reference what you actually saw:
- What functions/classes to create or modify (reference actual names from the code)
- Inputs and outputs (reference actual types)
- Existing patterns to follow (reference specific files you read)
- Test requirements (reference existing test patterns)
- Edge cases and error handling

{multi_repo_section}## Final Output

Output ONLY the TaskGraph JSON at the end. No markdown explanation after the JSON block."""
