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

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from claude_code_sdk import ClaudeCodeOptions
from pydantic import ValidationError as PydanticValidationError

from forge.agents.adapter import _build_question_protocol
from forge.core.models import TaskGraph
from forge.core.planning.models import ValidationResult
from forge.core.planning.validator import validate_plan
from forge.core.sdk_helpers import sdk_query

logger = logging.getLogger("forge.planning.unified")

_QUESTION_PATTERN = re.compile(r"FORGE_QUESTION:\s*\n?\s*(\{.*?\})\s*$", re.DOTALL)


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
        model: str = "opus",
        cwd: str | None = None,
        max_retries: int = 3,
        max_turns: int = 30,
        autonomy: str = "balanced",
        question_limit: int = 5,
        repo_ids: set[str] | None = None,
    ) -> None:
        self._model = model
        self._cwd = cwd
        self._max_retries = max_retries
        self._max_turns = max_turns
        self._autonomy = autonomy
        self._question_limit = question_limit
        self._repo_ids = repo_ids

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
        resume_session: str | None = None
        feedback: str | None = None

        for attempt in range(self._max_retries):
            logger.info("UnifiedPlanner attempt %d/%d", attempt + 1, self._max_retries)

            if resume_session:
                prompt = f"User answered: {feedback}\n\nNow produce the TaskGraph JSON."
            else:
                prompt = self._build_prompt(user_input, spec_text, snapshot_text, conventions, feedback)

            question_protocol = _build_question_protocol(
                autonomy=self._autonomy,
                remaining=self._question_limit - questions_asked,
            )
            system_prompt = _build_unified_system_prompt(question_protocol, lessons_block=lessons_block, repo_ids=self._repo_ids)

            options = ClaudeCodeOptions(
                system_prompt=system_prompt,
                max_turns=self._max_turns,
                model=self._model,
                # Full codebase read access — the key improvement over the old Architect
                disallowed_tools=["Edit", "Write"],
                permission_mode="acceptEdits",
            )
            if self._cwd:
                options.cwd = self._cwd
            if resume_session:
                options.resume = resume_session

            try:
                result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
            except Exception as e:
                logger.warning("UnifiedPlanner SDK error on attempt %d: %s", attempt + 1, e)
                feedback = f"SDK error: {e}"
                resume_session = None
                continue

            if not result:
                continue

            total_cost += result.cost_usd
            total_input += result.input_tokens
            total_output += result.output_tokens

            raw = result.result or ""

            # Check for FORGE_QUESTION — agent wants human input
            q_match = _QUESTION_PATTERN.search(raw)
            if q_match and on_question and questions_asked < self._question_limit:
                try:
                    q_data = json.loads(q_match.group(1))
                except json.JSONDecodeError:
                    q_data = None
                if q_data and "question" in q_data:
                    questions_asked += 1
                    answer = await on_question(q_data)
                    if answer:
                        resume_session = result.session_id
                        feedback = answer
                        continue

            # Try to parse TaskGraph
            resume_session = None
            graph, error = self._parse(raw)
            if graph is not None:
                logger.info(
                    "UnifiedPlanner succeeded on attempt %d (%d tasks)",
                    attempt + 1,
                    len(graph.tasks),
                )

                # Run deterministic validation (no LLM, milliseconds)
                # We pass a minimal CodebaseMap since validator only uses it for
                # future semantic checks (not current structural ones)
                from forge.core.planning.models import CodebaseMap

                minimal_map = CodebaseMap(
                    architecture_summary="(generated by unified planner)",
                    key_modules=[],
                )
                validation_result = validate_plan(graph, minimal_map, spec_text, repo_ids=self._repo_ids)

                # Auto-fix minor issues if possible
                if validation_result.status == "pass" or not any(
                    i.severity in ("major", "fatal") for i in validation_result.issues
                ):
                    # Plan is good or only has minor issues
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
                resume_session = result.session_id
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
            "Explore the codebase as needed, then produce a TaskGraph JSON.\n"
            "If anything is ambiguous, ask a question before planning."
        )
        return "\n\n".join(parts)

    def _parse(self, raw: str) -> tuple[TaskGraph | None, str | None]:
        """Extract and validate TaskGraph JSON from agent output."""
        raw = raw.strip()

        # Find ALL fenced JSON blocks and try each (last is usually best)
        blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if not blocks:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                blocks = [raw[start : end + 1]]

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


def _build_unified_system_prompt(question_protocol: str, lessons_block: str = "", repo_ids: set[str] | None = None) -> str:
    """Build the unified planner's system prompt."""
    multi_repo_section = ""
    if repo_ids is not None and len(repo_ids) > 1:
        sorted_repos = sorted(repo_ids)
        repo_list = ", ".join(f"`{r}`" for r in sorted_repos)
        multi_repo_section = f"""

## Multi-Repo Workspace

This workspace contains multiple repositories: {repo_list}

### CRITICAL: Repo Exclusion Rule
**Read the user's task description carefully.** If the user says to "ignore", "skip", "exclude", or says a repo "has nothing to do with" the task — that repo is OFF LIMITS. You MUST NOT:
- Create any tasks for that repo
- Read or browse files in that repo
- Reference that repo in any task description
Violating this rule will cause the pipeline to fail. Only create tasks for repos the user explicitly wants changed.

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

    return f"""You are a planning agent for Forge, a multi-agent coding orchestration system.

Your job: explore the codebase, understand the architecture, and decompose the user's
request into a TaskGraph — a set of independent, well-defined tasks that can be executed
in parallel by separate coding agents.

## Your Capabilities

You have FULL READ ACCESS to the codebase:
- **Glob**: Find files by pattern (e.g., "src/**/*.py", "**/*test*")
- **Grep**: Search file contents (e.g., function definitions, import patterns)
- **Read**: Read specific files
- **Bash**: Run read-only commands (git log, wc -l, find ... | wc)

Use these tools to understand the codebase BEFORE planning. You are not working
from a summary — you have direct access to the actual code.

## Workflow

### Phase 1: Explore (use as many turns as needed)

Start with the Project Snapshot below to understand the directory structure.
Then explore strategically:

1. **Glob** for entry points, configs, and key files related to the request
2. **Read** the most important files (interfaces, types, main modules)
3. **Grep** for specific patterns (function names, imports, API endpoints)

**Goal-directed reading**: After each file you read, ask yourself:
"Can I now decompose this task into independent work units with clear file ownership?"
- If YES → stop reading, start planning
- If NO → identify the ONE specific question you can't answer, and read the ONE file
  most likely to answer it

Do NOT read files "just to be thorough." Do NOT read test files or generated files
unless the task specifically involves them.

### Phase 2: Clarify (if needed)

If the request is ambiguous and you have questions remaining, ask BEFORE planning.
It is better to pause for 30 seconds than to build the wrong plan.

{question_protocol}

{lessons_block}

### Phase 3: Plan

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

## Task Decomposition Rules

- Each task owns specific files. **No two independent tasks may own the same file.**
- If task A creates a module but integration belongs to task B, use depends_on or shared file lists.
- Use depends_on ONLY when a task genuinely needs another's output.
- complexity: "low", "medium", or "high".
- Keep tasks focused: each does ONE thing well.
- **MINIMIZE dependencies** — independent tasks run in parallel.
- **COMPLETE file lists**: if a task's description mentions modifying a file, that file MUST
  be in the task's "files" array. Agents can ONLY edit files in their task's "files" list.
- **NEVER create tasks for git operations** (rebase, merge, branch management). The
  orchestrator handles ALL git operations automatically.
- Every task MUST produce code changes.

## Task Descriptions — Be Specific

Since you have direct access to the code, write descriptions that reference what you actually saw:
- What functions/classes to create or modify (reference actual names from the code)
- Inputs and outputs (reference actual types)
- Existing patterns to follow (reference specific files and line ranges you read)
- Test requirements (reference existing test patterns)
- Edge cases and error handling

{multi_repo_section}## Final Output

Output ONLY the TaskGraph JSON at the end. No markdown explanation after the JSON block."""
