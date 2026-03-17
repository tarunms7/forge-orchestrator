# Forge Planning Pipeline Overhaul: Unified Planner

## Goal

Replace the 4-stage planning pipeline (Scout → Architect → Detailer → Validator) with a single Unified Planner that has full tool access. The validator remains as a deterministic Python check (no LLM). The question protocol becomes first-class. The feedback loop is removed — if validation fails, return issues to the user instead of silently re-running.

## Why

The current pipeline has these proven failures:
1. **Architect is blind** — disallowed_tools blocks Bash, Glob, Grep, Edit, Write. It works from Scout's CodebaseMap, a lossy JSON summary. When the map is incomplete, the Architect produces bad plans.
2. **Detailer gets empty context** — `CodebaseMap.slice_for_files()` often returns an empty slice because Scout didn't index those specific files. The Detailer then produces generic bullets.
3. **Feedback loop is expensive** — up to 3 full Architect + Detailer re-runs. Each re-run costs time and money with diminishing returns.
4. **Information degrades** — Scout reads code → compresses to CodebaseMap → Architect plans from summary → Detailer gets sliced summary → Execution agent reads the ACTUAL files. Each hop loses fidelity.

## Architecture After This Change

```
forge run "task"
  → gather_project_snapshot()          # Already exists, no change
  → UnifiedPlanner.run()               # NEW: single Opus call with tool access
      - Reads system prompt with project snapshot pre-loaded
      - Uses Read/Glob/Grep to explore what it needs (goal-directed)
      - Asks clarifying questions via FORGE_QUESTION protocol
      - Produces TaskGraph JSON with detailed descriptions
  → validate_plan()                    # Existing, no change
      - If pass → return TaskGraph
      - If fail → return issues to caller (NO re-run)
  → Execute
```

## Output Contract (What the Planner MUST Produce)

The planner's output is a JSON object matching `TaskGraph`:

```json
{
  "conventions": {
    "styling": "...",
    "naming": "...",
    "testing": "..."
  },
  "tasks": [
    {
      "id": "task-1",
      "title": "Short title",
      "description": "Detailed implementation description (5-10 bullets covering: concrete edits, patterns to follow, tests, edge cases, dependencies)",
      "files": ["src/file.py", "tests/test_file.py"],
      "depends_on": [],
      "complexity": "low|medium|high"
    }
  ],
  "integration_hints": [
    {
      "producer_task_id": "task-1",
      "consumer_task_ids": ["task-3"],
      "interface_type": "api_endpoint|shared_type|event|file_import",
      "description": "What the contract is",
      "endpoint_hints": ["GET /api/x"]
    }
  ]
}
```

Each task description MUST include:
- What functions/classes to create or modify
- Inputs and outputs
- Existing patterns to follow (reference specific files by path)
- Test requirements
- Edge cases and error handling

Each task's `files` array MUST include EVERY file mentioned in the description. Files not in this array cannot be edited at runtime.

---

## Task Breakdown

### Task 1: Create `forge/core/planning/unified_planner.py`

**Files:** `forge/core/planning/unified_planner.py`
**Depends on:** nothing
**Complexity:** high

Create the new UnifiedPlanner class. This is the core of the change.

```python
# forge/core/planning/unified_planner.py

"""Unified planner: single LLM session with tool access for codebase exploration + task decomposition."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from forge.core.models import TaskGraph
from forge.core.planning.models import CodebaseMap, ValidationResult
from forge.core.planning.validator import validate_plan
from forge.core.sdk_helpers import sdk_query

logger = logging.getLogger("forge.planning.unified")

# Match FORGE_QUESTION: {...} at the end of output
_QUESTION_PATTERN = re.compile(r"FORGE_QUESTION:\s*(\{.*\})\s*$", re.DOTALL)


@dataclass
class UnifiedPlanningResult:
    """Result of a unified planning run."""
    task_graph: TaskGraph | None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    validation_result: ValidationResult | None = None
    questions_asked: int = 0


class UnifiedPlanner:
    """Single-session planner that explores the codebase and decomposes into tasks.

    Unlike the old pipeline (Scout → Architect → Detailer → Validator), this
    planner has full tool access (Read, Glob, Grep) and produces the TaskGraph
    in one session. No information loss between stages.
    """

    def __init__(
        self,
        model: str = "opus",
        cwd: str | None = None,
        autonomy: str = "balanced",
        question_limit: int = 5,
        on_message: Callable | None = None,
        on_question: Callable | None = None,
    ) -> None:
        self._model = model
        self._cwd = cwd
        self._autonomy = autonomy
        self._question_limit = question_limit
        self._on_message = on_message
        self._on_question = on_question

    async def run(
        self,
        *,
        user_input: str,
        spec_text: str = "",
        snapshot_text: str = "",
        conventions: str = "",
    ) -> UnifiedPlanningResult:
        """Run the unified planning session.

        Args:
            user_input: The user's task description.
            spec_text: Optional specification document.
            snapshot_text: Project snapshot (from ProjectSnapshot.format_for_planner()).
            conventions: Optional project conventions / prompt modifier.

        Returns:
            UnifiedPlanningResult with TaskGraph and cost info.
        """
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            user_input=user_input,
            spec_text=spec_text,
            snapshot_text=snapshot_text,
            conventions=conventions,
        )

        total_cost = 0.0
        total_input = 0
        total_output = 0
        questions_asked = 0
        resume_session: str | None = None

        # Main loop: run SDK, check for questions, retry if question answered
        max_attempts = self._question_limit + 2  # questions + initial + final
        for attempt in range(max_attempts):
            options = {
                "model": self._model,
                "max_turns": 30,
                "system_prompt": system_prompt,
                "permission_mode": "acceptEdits",
                # Allow read-only codebase tools + Read for targeted checks
                "disallowed_tools": ["Edit", "Write"],
            }
            if resume_session:
                options["resume"] = resume_session

            prompt = user_prompt if attempt == 0 else None  # resume uses no new prompt
            if attempt > 0 and not resume_session:
                break  # No session to resume and not first attempt

            result = await sdk_query(
                prompt=prompt or user_prompt,
                options=options,
                on_message=self._on_message,
            )

            if result is None:
                logger.error("SDK query returned None on attempt %d", attempt)
                break

            total_cost += result.cost_usd
            total_input += result.input_tokens
            total_output += result.output_tokens

            raw = result.result_text

            # Check for question
            q_match = _QUESTION_PATTERN.search(raw)
            if (
                q_match
                and self._on_question
                and questions_asked < self._question_limit
            ):
                try:
                    q_data = json.loads(q_match.group(1))
                except json.JSONDecodeError:
                    logger.warning("Failed to parse question JSON")
                    break

                questions_asked += 1
                answer = await self._on_question(q_data)
                if answer and answer != "Proceed with your best judgment.":
                    resume_session = result.session_id
                    # The answer will be sent as the next prompt on resume
                    user_prompt = f"Answer to your question: {answer}\n\nNow continue with planning. Produce the TaskGraph JSON."
                    continue
                # If no answer or auto-proceed, try to parse what we have
                # Fall through to JSON extraction

            # Try to extract TaskGraph JSON
            task_graph = self._extract_task_graph(raw)
            if task_graph is not None:
                # Run deterministic validation
                validation_result = validate_plan(
                    task_graph, CodebaseMap(architecture_summary="(unified planner)"), spec_text
                )
                return UnifiedPlanningResult(
                    task_graph=task_graph,
                    cost_usd=total_cost,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    validation_result=validation_result,
                    questions_asked=questions_asked,
                )

            # No TaskGraph found — retry with explicit instruction
            if attempt == 0:
                resume_session = result.session_id
                user_prompt = (
                    "You did not produce a valid TaskGraph JSON. "
                    "Please output ONLY the TaskGraph JSON now, inside a ```json code fence. "
                    "No other text."
                )
                continue

            logger.error("Failed to extract TaskGraph after %d attempts", attempt + 1)
            break

        return UnifiedPlanningResult(
            task_graph=None,
            cost_usd=total_cost,
            input_tokens=total_input,
            output_tokens=total_output,
            questions_asked=questions_asked,
        )

    def _build_system_prompt(self) -> str:
        """Build the system prompt for the unified planner."""
        question_protocol = self._build_question_protocol()

        return f"""You are the planning engine for Forge, a multi-agent coding orchestration system.

Your job: explore the codebase, understand it, and decompose the user's request into a TaskGraph — a set of independent, parallelizable tasks that coding agents will execute in isolated git worktrees.

## Your Capabilities

You have FULL READ ACCESS to the codebase:
- **Glob**: Find files by pattern (e.g., "src/**/*.py")
- **Grep**: Search file contents (e.g., function names, imports, patterns)
- **Read**: Read specific files
- **Bash**: Run read-only commands (git log, wc -l, find). Do NOT use Bash for reading files or searching — use Read and Grep instead.

You CANNOT edit files. Your only output is the TaskGraph JSON.

## How to Work

### Phase 1: Understand the Codebase (goal-directed exploration)

Start with the Project Snapshot provided below — it gives you the file tree, languages, README, and module index FOR FREE. Do not re-discover what's already there.

Then explore ONLY what you need to decompose this specific task:
1. Read entry points and module boundaries relevant to the task
2. Read interfaces between modules the task will touch
3. Read existing patterns (error handling, testing, naming) that tasks should follow

**STOP READING when you can answer:**
- What modules does this task touch?
- What are the interfaces between those modules?
- What patterns should tasks follow?

Do NOT read files "just to be thorough." Do NOT read test files unless the task involves testing. Do NOT read files in modules unrelated to the task.

### Phase 2: Ask Questions (if needed)

{question_protocol}

### Phase 3: Produce the TaskGraph

Output a single JSON code block (```json ... ```) containing the TaskGraph.

## TaskGraph Schema

```json
{{{{
  "conventions": {{{{
    "styling": "...",
    "naming": "...",
    "testing": "..."
  }}}},
  "tasks": [
    {{{{
      "id": "task-1",
      "title": "Short title",
      "description": "Detailed implementation description",
      "files": ["src/file.py"],
      "depends_on": [],
      "complexity": "low|medium|high"
    }}}}
  ],
  "integration_hints": [
    {{{{
      "producer_task_id": "task-1",
      "consumer_task_ids": ["task-3"],
      "interface_type": "api_endpoint|shared_type|event|file_import",
      "description": "What the contract is"
    }}}}
  ]
}}}}
```

## Task Rules

1. **File ownership is exclusive.** No two independent tasks may own the same file. If two tasks need the same file, one must depend on the other.
2. **Files array is the source of truth.** Agents can ONLY edit files in their task's "files" array. If the description says "modify X" but X is not in "files", the agent will fail. EVERY file mentioned in the description MUST be in "files".
3. **Minimize dependencies.** Independent tasks run in parallel. Only use depends_on when a task genuinely needs another's output (e.g., task-2 imports a class that task-1 creates).
4. **Each task does ONE thing.** Keep tasks focused. A task should be completable by one agent in 10-20 minutes.
5. **Max 10 files per task.** If a task needs more, split it.
6. **No git tasks.** NEVER create tasks for git operations (rebase, merge, cherry-pick, branch management). Forge handles all git operations automatically.
7. **Every task produces code changes.** If a task would only run commands with no file edits, it should not exist.

## Task Descriptions — Be Implementation-Ready

Each description MUST include:
- What functions/classes to create or modify (be specific: "Add method `validate_token(token: str) -> bool` to `AuthService` in `src/auth/service.py`")
- Inputs and outputs of new functions
- Existing patterns to follow (reference specific files: "Follow the pattern in `src/api/users.py:create_user()`")
- Test requirements ("Add tests in `tests/test_auth.py` covering: valid token, expired token, malformed token")
- Edge cases and error handling
- Integration points with other tasks (if any)

Target 5-10 bullets per task, under 250 words.

## Output

After exploration and any questions, output ONLY the TaskGraph as a JSON code block. No markdown headers, no explanation, just the JSON."""

    def _build_question_protocol(self) -> str:
        """Build the question protocol based on autonomy setting."""
        if self._autonomy == "full":
            return """You are in FULL AUTONOMY mode. Make all decisions yourself. Do not ask questions."""

        if self._autonomy == "supervised":
            return f"""You are in SUPERVISED mode. Ask questions when:
- The task is ambiguous about WHAT to build (not HOW)
- Multiple valid approaches exist and the choice affects the user
- A wrong assumption would cause significant rework

To ask a question, output at the END of your response:
FORGE_QUESTION: {{"question": "Your question here", "suggestions": ["Option A", "Option B"], "context": "Why you're asking"}}

You may ask up to {self._question_limit} questions. After asking, WAIT for the answer before continuing.
Do NOT ask questions about implementation details you can figure out from the code."""

        # balanced (default)
        return f"""You may ask questions when genuinely blocked. To ask, output at the END of your response:
FORGE_QUESTION: {{"question": "Your question here", "suggestions": ["Option A", "Option B"], "context": "Why you're asking"}}

You may ask up to {self._question_limit} questions total. Ask ONLY when:
- The user's intent is unclear and a wrong guess would waste significant work
- There are 2+ valid architectural approaches with very different tradeoffs

Do NOT ask about:
- Implementation details you can determine from the code
- Preferences that don't materially affect the outcome
- Things already specified in the user's input or spec"""

    def _build_user_prompt(
        self,
        *,
        user_input: str,
        spec_text: str,
        snapshot_text: str,
        conventions: str,
    ) -> str:
        """Build the user prompt with all context."""
        sections = []

        if snapshot_text:
            sections.append(f"## Project Snapshot\n\n{snapshot_text}")

        sections.append(f"## User Request\n\n{user_input}")

        if spec_text:
            sections.append(f"## Specification\n\n{spec_text}")

        if conventions:
            sections.append(f"## Project Conventions\n\n{conventions}")

        return "\n\n---\n\n".join(sections)

    def _extract_task_graph(self, raw: str) -> TaskGraph | None:
        """Extract and validate TaskGraph from raw LLM output.

        Tries multiple JSON blocks in reverse order (last block is most
        likely the final answer).
        """
        # Find all ```json ... ``` blocks
        json_blocks = re.findall(r"```json\s*(.*?)```", raw, re.DOTALL)

        # Also try ``` ... ``` blocks (some models forget the json tag)
        if not json_blocks:
            json_blocks = re.findall(r"```\s*(.*?)```", raw, re.DOTALL)

        # Also try raw JSON (no code fence)
        if not json_blocks:
            # Look for { ... } that contains "tasks"
            match = re.search(r'\{[^{}]*"tasks"\s*:\s*\[.*\]\s*[^{}]*\}', raw, re.DOTALL)
            if match:
                json_blocks = [match.group(0)]

        # Try blocks in reverse order (last is usually the final answer)
        for block in reversed(json_blocks):
            try:
                data = json.loads(block.strip())
                graph = TaskGraph.model_validate(data)

                # Basic sanity: no duplicate IDs
                ids = [t.id for t in graph.tasks]
                if len(ids) != len(set(ids)):
                    logger.warning("Duplicate task IDs found, skipping block")
                    continue

                # All depends_on reference existing tasks
                id_set = set(ids)
                valid = True
                for task in graph.tasks:
                    for dep in task.depends_on:
                        if dep not in id_set:
                            logger.warning("Task '%s' depends on unknown '%s'", task.id, dep)
                            valid = False
                if not valid:
                    continue

                return graph
            except (json.JSONDecodeError, Exception) as e:
                logger.debug("Failed to parse JSON block: %s", e)
                continue

        return None
```

**Key design decisions:**
- `disallowed_tools: ["Edit", "Write"]` — planner can Read, Glob, Grep, Bash but cannot modify code
- `max_turns: 30` — enough for exploration + planning. NOT a hard cap on reading; the system prompt teaches goal-directed reading
- Question protocol integrated directly — no separate question limit per "stage"
- JSON extraction tries multiple blocks in reverse order (same pattern as existing Architect)
- Validation runs once after extraction. No feedback loop — if it fails, the result contains the validation issues and the caller decides what to do
- `resume` session used for questions and retry-on-parse-failure

---

### Task 2: Create `forge/core/planning/unified_planner_test.py`

**Files:** `forge/core/planning/unified_planner_test.py`
**Depends on:** task-1
**Complexity:** medium

Create unit tests for the UnifiedPlanner. Follow the existing test patterns in `forge/core/planning/architect_test.py` and `forge/core/planning/scout_test.py`:

1. **Test `_extract_task_graph`** (no SDK needed):
   - Valid JSON in code fence → parses correctly
   - Valid JSON without code fence → parses correctly
   - Multiple JSON blocks → takes last valid one
   - Duplicate task IDs → rejected
   - Invalid depends_on reference → rejected
   - Malformed JSON → returns None
   - Empty output → returns None

2. **Test `_build_system_prompt`**:
   - Returns string containing "TaskGraph"
   - Contains question protocol matching autonomy setting
   - Full autonomy → contains "Do not ask questions"
   - Supervised → contains "SUPERVISED mode"
   - Balanced → contains "genuinely blocked"

3. **Test `_build_user_prompt`**:
   - Includes user_input
   - Includes spec_text when provided
   - Includes snapshot_text when provided
   - Omits empty sections

4. **Test `run` with mocked SDK** (mock `sdk_query`):
   - Happy path: SDK returns valid TaskGraph JSON → returns UnifiedPlanningResult with task_graph
   - SDK returns question → on_question called → session resumed with answer
   - SDK returns no JSON → retry with explicit instruction
   - SDK returns None → returns result with task_graph=None
   - Validation runs and result is attached

Use `unittest.mock.AsyncMock` for `sdk_query` and `on_question`. Use `pytest.mark.asyncio` for async tests.

---

### Task 3: Update `forge/core/daemon.py` to use UnifiedPlanner

**Files:** `forge/core/daemon.py`
**Depends on:** task-1
**Complexity:** medium

Replace the deep planning path (Scout + Architect + Detailer + PlanningPipeline) with UnifiedPlanner.

**What to change in `plan()` method (starting at line 278):**

1. **Remove imports** of Scout, Architect, DetailerFactory, PlanningPipeline from the deep planning branch (lines 342-345).

2. **Add import** of UnifiedPlanner:
   ```python
   from forge.core.planning.unified_planner import UnifiedPlanner, UnifiedPlanningResult
   ```

3. **Replace lines 341-476** (the entire `if use_deep:` block) with:

   ```python
   if use_deep:
       from forge.core.planning.unified_planner import UnifiedPlanner

       planner_model_deep = planner_model  # Already selected as high-quality
       console.print(f"[dim]Unified planning: {planner_model_deep}[/dim]")

       async def _on_planner_msg_deep(msg):
           text = _extract_activity(msg) if not isinstance(msg, str) else msg
           if not text:
               return
           if pipeline_id:
               await self._emit("planning:planner", {"line": text}, db=db, pipeline_id=pipeline_id)
           else:
               await self._events.emit("planning:planner", {"line": text})

       # Question handling (same pattern as before)
       pending_planning_answer: dict[str, asyncio.Event] = {}
       planning_answers: dict[str, str] = {}

       async def _on_planner_question(question_data: dict) -> str:
           q = await db.create_task_question(
               task_id="__planning__",
               pipeline_id=pipeline_id or "",
               question=question_data["question"],
               suggestions=question_data.get("suggestions"),
               context={"text": question_data.get("context", "")},
               stage="planning",
           )
           if pipeline_id:
               await self._emit("planning:question", {
                   "question_id": q.id, "question": question_data,
               }, db=db, pipeline_id=pipeline_id)
           else:
               await self._events.emit("planning:question", {
                   "question_id": q.id, "question": question_data,
               })

           event = asyncio.Event()
           pending_planning_answer[q.id] = event
           try:
               timeout = self._settings.question_timeout
               await asyncio.wait_for(event.wait(), timeout=timeout)
           except asyncio.TimeoutError:
               pending_planning_answer.pop(q.id, None)
               logger.info("Planning question %s timed out after %ds", q.id, timeout)
               return "Proceed with your best judgment."
           except asyncio.CancelledError:
               pending_planning_answer.pop(q.id, None)
               return "Proceed with your best judgment."
           return planning_answers.pop(q.id, "Proceed with your best judgment.")

       async def _on_planning_answer(data: dict):
           q_id = data.get("question_id")
           answer = data.get("answer")
           if q_id and answer:
               planning_answers[q_id] = answer
               ev = pending_planning_answer.pop(q_id, None)
               if ev:
                   ev.set()

       self._events.on("planning:answer", _on_planning_answer)

       unified = UnifiedPlanner(
           model=planner_model_deep,
           cwd=self._project_dir,
           autonomy=self._settings.autonomy,
           question_limit=self._settings.question_limit,
           on_message=_on_planner_msg_deep,
           on_question=_on_planner_question,
       )

       try:
           planning_result = await unified.run(
               user_input=user_input,
               spec_text=spec_text,
               snapshot_text=self._snapshot.format_for_planner() if self._snapshot else "",
               conventions=planner_prompt_modifier,
           )
       finally:
           handlers = self._events._handlers.get("planning:answer", [])
           if _on_planning_answer in handlers:
               handlers.remove(_on_planning_answer)

       if planning_result.task_graph is None:
           raise RuntimeError("Unified planner failed to produce a TaskGraph")

       graph = planning_result.task_graph

       # Track costs
       if pipeline_id and planning_result.cost_usd > 0:
           await db.add_pipeline_cost(pipeline_id, planning_result.cost_usd)
           await db.set_pipeline_planner_cost(pipeline_id, planning_result.cost_usd)
           total_cost = await db.get_pipeline_cost(pipeline_id)
           await self._emit("pipeline:cost_update", {
               "planner_cost_usd": planning_result.cost_usd,
               "total_cost_usd": total_cost,
               "cost_breakdown": {"planner": planning_result.cost_usd},
           }, db=db, pipeline_id=pipeline_id)

       # Log validation issues if any
       if planning_result.validation_result and planning_result.validation_result.status == "fail":
           for issue in planning_result.validation_result.issues:
               console.print(f"[yellow]  Validation {issue.severity}: {issue.description}[/yellow]")
   ```

4. **Remove CodebaseMap caching** (lines 451-476). The unified planner doesn't produce a CodebaseMap. (If caching is needed later, it can be re-added as a planner feature.)

5. **Keep the simple planning path** (lines 488-506) unchanged — it's the fallback for small tasks.

6. **Keep everything after** (lines 508-541) unchanged — plan_ready emission, phase transition, cost estimation all work the same since they only depend on `graph: TaskGraph`.

---

### Task 4: Update tests that import old pipeline classes

**Files:** `forge/core/planning/pipeline_test.py`
**Depends on:** task-1, task-3
**Complexity:** low

Update `pipeline_test.py` to test the new UnifiedPlanner integration path instead of the old Scout → Architect → Detailer → PlanningPipeline.

If the existing tests mock Scout, Architect, DetailerFactory:
- Replace with mocks of `UnifiedPlanner.run()` returning `UnifiedPlanningResult`
- Test that `daemon.plan()` with `deep_plan=True` calls `UnifiedPlanner.run()` with correct args
- Test that `daemon.plan()` with `deep_plan=False` still uses the simple planner path
- Test question handling flows through correctly

Keep the validator tests (`validator_test.py` / `forge/core/planning/test_validator.py`) unchanged — the validator is not changing.

---

### Task 5: Remove Scout, Architect, Detailer, PlanningPipeline (cleanup)

**Files:** `forge/core/planning/scout.py`, `forge/core/planning/architect.py`, `forge/core/planning/detailer.py`, `forge/core/planning/pipeline.py`, `forge/core/planning/prompts.py`, `forge/core/planning/scout_test.py`, `forge/core/planning/architect_test.py`, `forge/core/planning/detailer_test.py`
**Depends on:** task-3, task-4
**Complexity:** low

1. **Delete** the following files entirely:
   - `forge/core/planning/scout.py`
   - `forge/core/planning/architect.py`
   - `forge/core/planning/detailer.py`
   - `forge/core/planning/pipeline.py`
   - `forge/core/planning/prompts.py`
   - `forge/core/planning/scout_test.py`
   - `forge/core/planning/architect_test.py`
   - `forge/core/planning/detailer_test.py`
   - `forge/core/planning/pipeline_test.py`

2. **Grep the entire codebase** for any remaining imports of:
   - `from forge.core.planning.scout import`
   - `from forge.core.planning.architect import`
   - `from forge.core.planning.detailer import`
   - `from forge.core.planning.pipeline import`
   - `from forge.core.planning.prompts import`
   - `PlanningPipeline`
   - `Scout(`
   - `Architect(`
   - `DetailerFactory`
   - `SCOUT_SYSTEM_PROMPT`
   - `build_architect_system_prompt`
   - `DETAILER_SYSTEM_PROMPT`

3. **Remove or update** every remaining reference found. Common locations:
   - `forge/core/daemon.py` — any leftover imports in the non-deep-plan branch
   - `forge/core/planning/__init__.py` — re-exports
   - `forge/core/planning/cache.py` — may reference CodebaseMap (keep CodebaseMap model, just remove Scout references)
   - `tests/` — any integration tests

4. **Keep these files unchanged:**
   - `forge/core/planning/models.py` — CodebaseMap, ValidationResult, etc. are still used
   - `forge/core/planning/validator.py` — still used by UnifiedPlanner
   - `forge/core/planning/cache.py` — may be useful later; just remove Scout-specific references if any

---

### Task 6: Update `forge/core/planning/__init__.py` exports

**Files:** `forge/core/planning/__init__.py`
**Depends on:** task-5
**Complexity:** low

Update the package's `__init__.py` to export the new public API:

```python
from forge.core.planning.unified_planner import UnifiedPlanner, UnifiedPlanningResult
from forge.core.planning.validator import validate_plan
from forge.core.planning.models import (
    CodebaseMap,
    ValidationResult,
    ValidationIssue,
    PlanFeedback,
)
```

Remove all exports of Scout, Architect, Detailer, DetailerFactory, PlanningPipeline, ScoutResult, ArchitectResult, DetailerResult.

---

## What This Does NOT Change

- **`forge/core/models.py`** — TaskGraph, TaskDefinition unchanged
- **`forge/core/planning/validator.py`** — unchanged, still runs deterministic checks
- **`forge/core/planning/models.py`** — CodebaseMap and other models unchanged (may be used by cache or future features)
- **`forge/core/context.py`** — ProjectSnapshot gathering unchanged
- **`forge/core/daemon_executor.py`** — execution layer unchanged
- **`forge/core/daemon_merge.py`** — merge layer unchanged
- **`forge/core/scheduler.py`** — scheduling unchanged
- **`forge/config/settings.py`** — settings unchanged (autonomy, question_limit, question_timeout all still used)
- **Simple planner path** — `forge/core/planner.py` + `ClaudePlannerLLM` unchanged, still used when `use_deep=False`

## Validation Criteria

After implementation:
1. `forge run "add a health check endpoint"` on a real project should:
   - Complete planning in under 5 minutes (not 20)
   - Produce a valid TaskGraph
   - Show the planner exploring the codebase via Read/Glob/Grep in the logs
2. All existing tests pass (except deleted ones for Scout/Architect/Detailer/Pipeline)
3. New unified_planner_test.py passes
4. No remaining imports of deleted modules anywhere in the codebase
