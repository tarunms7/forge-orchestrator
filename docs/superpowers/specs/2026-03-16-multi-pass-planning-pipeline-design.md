# Multi-Pass Planning Pipeline

**Date:** 2026-03-16
**Status:** Draft
**Goal:** Replace the single-shot planner with a multi-stage pipeline that accurately decomposes large projects (50+ tasks, 100+ files) into high-quality task graphs.

## Problem

The current planner is a single Claude session (max 30 turns) that must:
1. Read the codebase
2. Understand the spec/request
3. Decompose into tasks
4. Produce valid TaskGraph JSON

For large features or product-scale builds, this hits hard limits:
- Runs out of turns reading files before it can plan
- Context window fills with code, leaving no room for reasoning about decomposition
- Produces vague task descriptions because it rushes to output JSON
- No opportunity to ask the user about spec ambiguities before committing to a plan

## Solution: 4-Stage Planning Pipeline

Split planning into specialized, sequential stages. Each stage is a focused Claude Code session with a single responsibility and clear input/output contracts.

```
Spec + Codebase
      │
      ▼
┌─────────────┐
│   SCOUT     │  Reads codebase, produces structured CodebaseMap
│ (30 turns)  │  Output: CodebaseMap JSON
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  ARCHITECT  │  Reads spec + CodebaseMap, decomposes into tasks
│ (20 turns)  │  Can ask FORGE_QUESTIONs about spec ambiguities
│             │  Output: RoughTaskGraph JSON
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  DETAILERS (parallel, one per task)     │
│  Each gets: task + relevant CodebaseMap │
│  slice + integration contracts          │
│  Output: EnrichedTask per task          │
└──────┬──────────────────────────────────┘
       │
       ▼
┌─────────────┐
│  VALIDATOR  │  Reviews complete plan for consistency
│ (10 turns)  │  Output: ValidationResult (pass/fail + issues)
└──────┬──────┘
       │
       ▼
   TaskGraph (final)
```

### Why This Works for Huge Projects

- **Scout** spends all its turns reading code — no planning pressure
- **Architect** never reads files — gets the scout's deep understanding as pre-built context, spends all turns on decomposition reasoning
- **Detailers** run in parallel — a 30-task plan takes roughly the same time as a 5-task plan
- **Validator** catches issues no single-pass planner would — file conflicts, dependency gaps, coverage holes
- Each session stays within context window because its job is narrow

## Stage 1: Scout

### Purpose
Deep LLM-powered exploration of the codebase. Produces a structured `CodebaseMap` that all downstream stages consume instead of reading files themselves.

### Input
- `ProjectSnapshot` (existing — file tree, LOC, languages, module index)
- Spec document text
- User request string

### Output: CodebaseMap

The `CodebaseMap` is validated via a Pydantic model (defined in `forge/core/planning/models.py`) to ensure the scout's LLM output is structurally valid, similar to how `TaskGraph` is validated today. Fields in `existing_patterns` are optional — the scout only populates keys where it found clear evidence in the codebase.

```json
{
  "architecture_summary": "Monorepo with Python backend (FastAPI + SQLAlchemy) and Next.js frontend. CLI entry at forge/cli/main.py, core orchestration in forge/core/, API in forge/api/.",
  "key_modules": [
    {
      "path": "forge/core/daemon.py",
      "purpose": "Main orchestration loop: plan → contract → execute → review → merge",
      "key_interfaces": [
        "ForgeDaemon.execute(pipeline_id)",
        "ForgeDaemon._execution_loop_inner(db, pipeline)"
      ],
      "dependencies": ["daemon_executor.py", "daemon_review.py", "daemon_merge.py"],
      "loc": 938
    }
  ],
  "existing_patterns": {
    "error_handling": "Custom exception hierarchy in forge/core/errors.py",
    "testing": "pytest + pytest-asyncio, colocated *_test.py files",
    "state_management": "Pydantic models in models.py, state machine in state.py"
  },
  "relevant_interfaces": [
    {
      "name": "PlannerLLM",
      "file": "forge/core/planner.py",
      "signature": "async generate_plan(user_input, context, feedback) -> str",
      "notes": "ABC — concrete impl is ClaudePlannerLLM in claude_planner.py"
    }
  ],
  "risks": [
    "daemon.py is 938 lines — modifications need care",
    "tasks.py route file is 1801 lines — consider splitting if adding routes"
  ]
}
```

### System Prompt Focus
"You are a codebase analyst. Read the project, understand its architecture, and produce a structured CodebaseMap JSON. Focus on modules relevant to the user's spec. Do NOT plan tasks — only document what exists."

### Tools
Read-only: `Read`, `Glob`, `Grep`, `Bash` (for git commands). Same as current planner.

### Model
Sonnet (efficient code reading, doesn't need creative reasoning).

### Turns
30 (same as current planner — all turns go to reading, not planning).

## Stage 2: Architect

### Purpose
Decompose the spec into a task graph using the CodebaseMap as context. This is the most critical stage — decomposition quality determines everything downstream.

### Input
- `CodebaseMap` (from scout)
- Spec document text
- User request string
- Conventions (from `.forge/conventions.md` + scout's `existing_patterns`)
- Answers to FORGE_QUESTIONs (if any were asked)

### Output: RoughTaskGraph
Same schema as current `TaskGraph` (tasks with IDs, titles, descriptions, files, dependencies, complexity, integration_hints). Descriptions are moderate detail — not implementation-ready yet, but clear enough for the validator to check.

### FORGE_QUESTION Integration

**Important:** The current FORGE_QUESTION infrastructure lives in `daemon_executor.py` and `agents/adapter.py` — it only works during task execution, not planning. The planning pipeline must implement its own question mechanism from scratch.

**Implementation approach:** The architect session uses the same `FORGE_QUESTION:` JSON output format that task agents use (defined in `adapter.py:_build_question_protocol()`). The `PlanningPipeline` orchestrator monitors the architect's SDK streaming output for this pattern:

1. Architect emits `FORGE_QUESTION:` JSON in its output
2. `PlanningPipeline._run_architect()` detects the pattern (same regex as `daemon_executor.py:_parse_forge_question`)
3. Pipeline pauses the architect by saving the SDK `session_id` from the response
4. Pipeline emits a `planning:question` event (see TUI Events section) with the question payload
5. TUI/API surfaces the question to the user (reuses existing `FollowUpScreen` in TUI)
6. User answers via TUI
7. Pipeline resumes the architect session using `ClaudeCodeOptions(resume=session_id)` with the user's answer appended as a new user message
8. Architect continues, now with the answer in context, and produces the TaskGraph

**This is new code** — it does NOT reuse `daemon_executor._handle_agent_question()` because that function is tightly coupled to task state management (AWAITING_INPUT state, agent slot release, task DB updates). The planning pipeline handles questions at the pipeline level, not the task level.

**Question budget:** Same as `question_limit` setting (default 3). Autonomy levels apply:
- `full`: never ask, decide autonomously
- `balanced`: ask about architecture/technology decisions only
- `supervised`: ask about everything unclear

**Question timeout:** Same as `question_timeout` setting (default 1800s / 30 min). If the user doesn't answer, the architect auto-decides and logs the assumption in the task descriptions.

### Why the Architect Doesn't Read Files
The CodebaseMap contains everything the architect needs about existing code. This means the architect can spend all its turns (20) on decomposition reasoning instead of wasting turns on file I/O. This is the key scalability insight — separating "understand the code" from "plan the work."

### Model
Opus (most critical stage — decomposition quality depends on reasoning depth).

### Tools
Read-only: `Read`, `Glob`, `Grep`, `Bash`. The architect CAN read files if the CodebaseMap is insufficient, but shouldn't need to for most cases.

## Stage 3: Detailers (Parallel)

### Purpose
Enrich each task from the architect's rough plan with implementation-ready detail: exact functions/classes to create/modify, test requirements, edge cases, patterns to follow.

### Input (per detailer)
- One task from the RoughTaskGraph
- Relevant slice of CodebaseMap (only modules that task's files touch)
- Integration contracts (if task has integration_hints)
- Conventions

### Output: EnrichedTask
The task with a fully detailed description. Example:

**Before (architect):**
> "Add rate limiting to the API"

**After (detailer):**
> "Create forge/api/middleware/rate_limit.py with a RateLimitMiddleware class following the pattern in forge/api/middleware/auth.py. Use sliding window algorithm with Redis (or in-memory dict for dev). Config via FORGE_RATE_LIMIT_RPM setting (default 60). Add to middleware stack in forge/api/app.py:create_app(). Write tests in forge/api/middleware/rate_limit_test.py covering: normal request, rate exceeded (429), window reset, concurrent requests."

### Parallelism
All detailers run concurrently, throttled by `max_agents` setting. This intentionally reuses the same setting as task execution — during planning, no task agents are running, so the full agent budget is available for detailers. A 30-task plan with `max_agents=4` runs ~8 batches of detailers. No separate `max_detailers` setting is needed because planning and execution never overlap.

### Model
Sonnet (focused task, doesn't need opus-level reasoning).

### Tools
Read-only: `Read`, `Glob`, `Grep`. Detailers may need to read specific files referenced in the CodebaseMap to write precise instructions.

### Turns
10 per detailer (focused job — read relevant files, produce enriched description).

## Stage 4: Validator

### Purpose
Review the complete enriched task graph for consistency issues. Catches problems that no single-pass planner would notice.

### Input
- Complete TaskGraph (all enriched tasks assembled)
- CodebaseMap
- Spec document text (to verify coverage)

### Output: ValidationResult

```json
{
  "status": "fail",
  "issues": [
    {
      "severity": "major",
      "category": "file_conflict",
      "affected_tasks": ["task-3", "task-7"],
      "description": "Both tasks modify forge/api/app.py without a dependency",
      "suggested_fix": "Add depends_on: task-3 → task-7"
    }
  ],
  "minor_fixes": [
    {
      "task_id": "task-5",
      "field": "files",
      "reason": "Missing test file for new module",
      "original_value": ["forge/api/middleware/rate_limit.py"],
      "fixed_value": ["forge/api/middleware/rate_limit.py", "forge/api/middleware/rate_limit_test.py"]
    },
    {
      "task_id": "task-8",
      "field": "description",
      "reason": "Description too vague — added test requirements",
      "original_value": "Add error handling",
      "fixed_value": "Add error handling to forge/core/daemon.py:_execution_loop_inner() for SDK timeout errors. Catch asyncio.TimeoutError, transition task to FAILED state, and emit pipeline event. Test: verify timeout triggers FAILED state and event emission."
    }
  ]
}
```

### Issue Categories

| Category | Examples | Action |
|----------|----------|--------|
| **MINOR** | Vague description, missing test mention, incomplete edge case list | Auto-fix in-process — validator produces `minor_fixes` array with original + fixed values per task field. The pipeline applies these fixes directly to the TaskGraph without re-running the architect. |
| **MAJOR** | File ownership conflict, missing dependency, task too large, integration gap, spec requirement not covered | Loop back to architect for scoped re-plan |
| **FATAL** | Circular dependency, spec requirement completely missing, fundamentally wrong decomposition | Human escalation — stop and surface to user |

### Checks Performed
1. **File ownership:** No two independent tasks modify the same file without a dependency
2. **Dependency validity:** All `depends_on` references exist, no cycles
3. **Spec coverage:** Every requirement in the spec maps to at least one task
4. **Task granularity:** No task is too large (>10 files) or too vague (<50 char description)
5. **Integration completeness:** Every integration_hint has matching tasks on both sides
6. **Convention compliance:** Tasks reference correct patterns from CodebaseMap
7. **Test coverage:** Every implementation task has corresponding test requirements

### Model
Sonnet (rule-based checks + LLM judgment, sonnet is sufficient).

### Turns
10 (focused review job).

## Validator ↔ Architect Feedback Loop

### State Machine

```
Architect produces RoughTaskGraph
    → Detailers enrich
    → Validator checks
        → PASS: proceed to human review
        → MINOR issues only: auto-fix, re-validate once
        → MAJOR issues: send PlanFeedback to Architect
            → Architect re-plans (scoped to affected tasks only)
            → Detailers re-enrich (only changed tasks)
            → Validator re-checks
        → FATAL issues: escalate to human
```

### PlanFeedback Contract (Validator → Architect)

```json
{
  "iteration": 2,
  "max_iterations": 3,
  "issues": [
    {
      "severity": "major",
      "category": "file_conflict",
      "affected_tasks": ["task-3", "task-7"],
      "description": "Both tasks modify forge/api/app.py without a dependency",
      "suggested_fix": "Add depends_on: task-3 → task-7, or consolidate"
    }
  ],
  "preserved_tasks": ["task-1", "task-2", "task-4", "task-5", "task-6"],
  "replan_scope": "Only replan task-3 and task-7. Do not change preserved tasks."
}
```

### Loop Rules (Hard-Coded)

1. **Max iterations: 3** — architect attempt 1 + 2 re-plans maximum
2. **Scope narrowing:** Each re-plan can only modify tasks in `replan_scope`. Architect cannot rewrite preserved tasks.
3. **MINOR auto-fix:** Validator fixes minor issues itself (rewrites descriptions, adds test mentions). No re-plan loop triggered.
4. **FATAL escalation:** Any fatal issue stops the loop and surfaces to user via pipeline events.
5. **Convergence check:** If iteration N+1 introduces NEW major issues not in iteration N, that's FATAL (architect is diverging). Escalate to human.
6. **Detailer skip:** On re-plan, only changed tasks go through detailers again. Unchanged tasks keep their enriched descriptions.
7. **Scope violation:** If architect modifies a preserved task, validator rejects immediately, deducts one iteration, retries.
8. **Ambiguity escalation:** After 2nd iteration, if issues stem from spec ambiguity, escalate to human as FORGE_QUESTION before 3rd attempt.

## Persistent CodebaseMap + Incremental Scouting

### Purpose
Store the Scout's CodebaseMap so subsequent pipelines on the same repo skip re-scouting unchanged code.

### Storage

```
.forge/
  codebase_map.json           # Full CodebaseMap (Scout output)
  codebase_map_meta.json      # Metadata for incremental detection
```

**codebase_map_meta.json:**
```json
{
  "created_at": "2026-03-16T10:30:00Z",
  "git_commit": "c8c4a0e",
  "git_branch": "main",
  "scout_model": "sonnet",
  "file_hashes": {
    "forge/core/daemon.py": "sha256:abc123...",
    "forge/core/planner.py": "sha256:def456..."
  }
}
```

### Cache Invalidation Rules

| Condition | Action |
|-----------|--------|
| `.forge/codebase_map.json` missing | Full scout |
| Same git HEAD as cached | Skip scout, use cached map |
| <20% of files changed since cached commit | Incremental scout |
| ≥20% of files changed | Full re-scout |
| Different branch than cached | Full re-scout |
| Cache >7 days old | Full re-scout |
| User runs `forge clean` | Delete cache, next run does full scout |

### Incremental Scout

When <20% of files changed:
1. Compute changed files: `git diff --name-only <cached_commit>..HEAD`
2. Give incremental scout: existing CodebaseMap + list of changed/new/deleted files
3. Scout reads only changed files (5-10 turns vs 30 for full scout)
4. Produces updated CodebaseMap entries for changed modules
5. Merge into existing map: update changed, add new, remove deleted
6. Save updated map + meta

### Cost Savings (approximate, will vary by codebase size)
- Full scout: ~$0.12 (sonnet, 30 turns)
- Incremental scout (10 files changed): ~$0.02 (sonnet, 5 turns)
- Cached (no changes): $0.00

### Gitignore

`.forge/codebase_map.json` and `.forge/codebase_map_meta.json` should be added to `.gitignore` (or `.forge/.gitignore`). They contain machine-specific analysis and should not be committed.

## Integration with Existing Daemon

### New Module Structure

```
forge/core/
  planner.py              # Keep: PlannerLLM ABC + Planner class (backward compat)
  planning/               # NEW directory
    __init__.py
    pipeline.py           # PlanningPipeline orchestrator
    scout.py              # Scout stage
    architect.py          # Architect stage
    detailer.py           # Detailer stage (parallel)
    validator.py          # Validator stage + feedback loop
    models.py             # CodebaseMap, PlanFeedback, ValidationResult
    prompts.py            # System prompts for each stage
    cache.py              # Persistent CodebaseMap + incremental detection
```

### Daemon Integration

The daemon's planning call changes from:

```python
planner = Planner(ClaudePlannerLLM(model=..., cwd=...))
task_graph = await planner.plan(user_input, context)
```

To:

```python
planning_pipeline = PlanningPipeline(
    scout=Scout(model="sonnet", cwd=project_dir),
    architect=Architect(model="opus", cwd=project_dir),
    detailer_factory=DetailerFactory(model="sonnet"),
    validator=PlanValidator(),
    settings=settings,
    on_question=question_handler,
    on_message=message_handler,
)
task_graph = await planning_pipeline.run(
    user_input=user_input,
    spec_text=spec_text,
    snapshot=project_snapshot,
)
```

### Callback Signatures

```python
# Called when a planning stage emits streaming output (for TUI display)
# stage: "scout" | "architect" | "detailer" | "validator"
# message: raw text from the Claude session
async def on_message(stage: str, message: str) -> None: ...

# Called when the architect emits a FORGE_QUESTION
# Returns the user's answer string (blocks until user responds or timeout)
# question: parsed ForgeQuestion dataclass (question text + suggestions)
# This is NOT the same as daemon_executor's question handler — it operates
# at the pipeline level, not the task level
async def on_question(question: ForgeQuestion) -> str | None: ...
```

The daemon wires these callbacks to the existing event system:
- `on_message` → emits `pipeline_events` with type `planning:stage_output`
- `on_question` → emits `pipeline_events` with type `planning:question`, then awaits answer via `asyncio.Event` (same pattern as `daemon_executor._handle_question_flow`)

### Backward Compatibility

The existing `Planner` class stays. `PlanningPipeline` is used when:
- A spec file is provided (`forge run --spec docs/spec.md "..."`) — always deep
- User explicitly requests it (`forge run --deep-plan "..."`) — always deep
- Setting: `planning_mode = "auto" | "simple" | "deep"` (default: "auto")

**Auto-mode heuristic:** When `planning_mode="auto"`, deep planning is selected if ANY of:
1. A `--spec` file is provided
2. The user request contains markdown structure (headers, lists, multiple paragraphs) indicating a structured feature description
3. The user request explicitly mentions multiple features/components (detected via keywords: "and", numbered lists, semicolons separating requirements)
4. The existing `ProjectSnapshot` shows >200 tracked files (larger codebase = more value from deep scouting)

If none of these trigger, the simple single-shot planner is used. This avoids the "501 characters for a simple task" problem — length alone is not sufficient.

### Model Selection Per Stage

| Stage | Default Model | Rationale |
|-------|---------------|-----------|
| Scout | sonnet | Efficient code reading, not creative work |
| Architect | opus | Most critical — decomposition quality is everything |
| Detailer | sonnet | Focused enrichment task |
| Validator | sonnet | Rule-based + LLM judgment |

Configurable via settings but these defaults are optimized for accuracy vs cost.

## TUI Integration

### Planning Progress Display

```
┌─ Planning ──────────────────────────────────┐
│ [■■■■■■■■■■] Scout       ✓ Complete (12s)  │
│ [■■■■■░░░░░] Architect   ⟳ Running...      │
│ [          ] Detailers   ○ Waiting          │
│ [          ] Validator   ○ Waiting          │
│                                             │
│ Architect output:                           │
│ > Reading spec document...                  │
│ > Identified 3 ambiguities, asking user...  │
└─────────────────────────────────────────────┘
```

### New Planning Sub-States

The TUI's `state.py` gets new planning sub-states (these are values of the existing `phase: str` field):
- `planning_scout` — Scout stage running
- `planning_architect` — Architect stage running (may pause for FORGE_QUESTION)
- `planning_detailing` — Detailers running in parallel
- `planning_validating` — Validator running
- `planning_replan` — Validator found issues, architect re-planning

### TUI Events

The planning pipeline emits events via the existing `pipeline_events` table. Each event has a `type` and JSON `payload`:

| Event Type | Payload | Triggers Transition |
|------------|---------|---------------------|
| `planning:stage_started` | `{"stage": "scout"}` | `planning` → `planning_scout` |
| `planning:stage_completed` | `{"stage": "scout", "duration_s": 12.3}` | `planning_scout` → `planning_architect` |
| `planning:stage_output` | `{"stage": "architect", "text": "Reading spec..."}` | (no transition, updates output display) |
| `planning:question` | `{"question": "...", "suggestions": [...]}` | (pauses architect, shows follow-up screen) |
| `planning:question_answered` | `{"answer": "Use JWT"}` | (resumes architect) |
| `planning:validation_result` | `{"status": "fail", "issues": [...]}` | `planning_validating` → `planning_replan` |
| `planning:complete` | `{"task_count": 12, "cost_usd": 1.01}` | `planning_*` → `planned` |
| `planning:failed` | `{"error": "...", "stage": "architect"}` | `planning_*` → `error` |

The TUI's `app.py` message handler maps these events to UI updates. The existing `PlannerCard` widget is replaced by a new `PlanningPipelineCard` that shows per-stage progress bars.

## Error Handling

| Stage | Failure Mode | Recovery |
|-------|-------------|----------|
| Scout | SDK error / timeout | Retry 3x. If all fail, fall back to `ProjectSnapshot` (no CodebaseMap) |
| Scout | Invalid CodebaseMap JSON | Retry with feedback. 3 retries exhausted → fall back to `ProjectSnapshot` |
| Architect | SDK error / timeout | Retry 3x. If all fail → surface error to user |
| Architect | Invalid TaskGraph JSON | Retry with validation feedback (existing mechanism) |
| Architect | FORGE_QUESTION timeout | Auto-decide, log assumption, continue |
| Detailer | One detailer fails | Retry 2x. If still fails → use rough description from architect |
| Detailer | All detailers fail | Fatal → surface to user |
| Validator | SDK error | Skip validation, present plan with warning |
| Validator | FATAL issues found | Escalate to user immediately |
| Pipeline | Budget exceeded mid-planning | Stop at current stage, present what we have |

**Key principle:** Every stage has a graceful degradation path. The system never hard-fails if a single stage has issues.

## Cost Tracking

Planning cost tracked per stage and reported to user:

```
Planning Cost Breakdown:
  Scout:      $0.12  (sonnet, 15 turns)
  Architect:  $0.45  (opus, 8 turns)
  Detailers:  $0.36  (sonnet, 12 tasks × ~3 turns each)
  Validator:  $0.08  (sonnet, 5 turns)
  Total:      $1.01
```

Integrates with existing `budget.py`. Planning cost counts toward pipeline's `budget_limit_usd`.

## Testing Strategy

### Unit Tests (per stage)
- **Scout:** Mock SDK, verify CodebaseMap schema, test fallback to ProjectSnapshot
- **Architect:** Mock SDK, verify TaskGraph schema, test FORGE_QUESTION emission/resumption
- **Detailer:** Mock SDK, verify task enrichment, test parallel execution
- **Validator:** Pure logic — file conflicts, dep cycles, convergence check, scope violations
- **Cache:** File hash comparison, invalidation rules, incremental merge

### Integration Tests
- Full `PlanningPipeline.run()` with mocked SDK — verify data flows between stages
- Validator↔Architect loop: max iterations, scope narrowing, convergence detection
- Auto-mode detection: heuristic selects simple vs deep planning correctly
- Error recovery: each fallback path works
- Incremental scouting: changed files detected, map updated correctly

### No E2E Tests for Planning Pipeline
Real SDK calls too slow/expensive for CI. Existing e2e test covers full daemon flow via simple planner path.

## CLI Changes

New flag for `forge run`:
- `--spec <path>` — Path to spec document (markdown or text file). Triggers deep planning. PDF support is out of scope for this spec.
- `--deep-plan` — Force deep planning even without a spec file.

New setting:
- `planning_mode` — `"auto"` (default), `"simple"`, `"deep"`

## Summary of Key Decisions

1. **4-stage pipeline:** Scout → Architect → Detailers → Validator
2. **Scout produces CodebaseMap** — deep LLM-generated codebase understanding, shared by all downstream stages + task agents
3. **Architect uses opus** — decomposition quality is the highest-value decision in the pipeline
4. **Architect can ask FORGE_QUESTIONs** — spec ambiguities resolved before planning
5. **Detailers parallelize** — one per task, enriches to implementation-ready detail
6. **Validator loop:** max 3 iterations, scope-narrowed re-plans, convergence check, fatal escalation
7. **Persistent CodebaseMap** — cached in `.forge/`, incremental scouting for subsequent runs
8. **Backward compatible** — simple planner for small tasks, auto-detected
9. **TUI-first** — stage progress in TUI, web UI deferred
10. **Graceful degradation** — every stage has a fallback path
