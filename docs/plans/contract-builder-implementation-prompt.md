# Contract Builder — Implementation Prompt

> Copy everything below the line and paste it as your first message to a new Claude session.

---

## Task

Implement the **Contract Builder** feature for Forge — a new pipeline phase that generates cross-task interface contracts so parallel agents build against the same API specs.

## Context

Forge is a multi-agent orchestration engine that decomposes coding tasks into parallel subtasks, each executed by an independent Claude agent in its own git worktree. The #1 class of bugs is **API contract mismatches** — when a backend agent builds `GET /api/templates` returning `{ builtin: [...], user: [...] }` but the frontend agent assumes it returns a flat array, because neither agent can see the other's code during development.

The solution: a **Contract Builder** phase that runs after the planner and before execution. It reads the planner's integration hints, generates precise interface contracts (API shapes, shared types, field names), and injects them into each agent's prompt. Both BE and FE agents build against the same contract, independently, in parallel.

## Design Document

**Read this file first — it contains the complete design, all data models, system prompts, and exact code to write:**

```
docs/design/contract-builder.md
```

Read the ENTIRE document before writing any code. It has 16 sections covering everything from data models to testing strategy.

## Key Files to Understand Before Coding

Read these files to understand the current system architecture:

| File | Why |
|------|-----|
| `forge/core/models.py` | Current TaskDefinition, TaskGraph, TaskRecord — you'll add `integration_hints` here |
| `forge/core/claude_planner.py` | Current planner system prompt — you'll add integration_hints schema + rules |
| `forge/core/planner.py` | Abstract PlannerLLM interface + Planner orchestrator with retry — follow this pattern for ContractBuilder |
| `forge/agents/adapter.py` | Agent system prompt template + `_build_options()` — you'll add `{contracts_block}` placeholder |
| `forge/agents/runtime.py` | AgentRuntime.run_task() — you'll add `contracts_block` param |
| `forge/core/daemon.py` | Pipeline orchestration: plan → execute — you'll insert contracts between them |
| `forge/core/daemon_executor.py` | `_stream_agent()` method — you'll load contracts here and pass to adapter |
| `forge/core/daemon_review.py` | Review pipeline with `custom_review_focus` — you'll inject contract compliance here |
| `forge/storage/db.py` | Database schema — you'll add `contracts_json` column to `PipelineRow` |
| `forge/core/model_router.py` | Model selection — you'll add `contract_builder` role |
| `forge/core/validator.py` | TaskGraph validation — you'll add optional integration_hints validation |

## Implementation Order (follow this exactly)

### Step 1: Data Models (`forge/core/contracts.py` — NEW FILE)
Create all contract data models: `ContractType`, `FieldSpec`, `TypeContract`, `APIContract`, `IntegrationHint`, `ContractSet`, `TaskContracts`. Include `format_for_agent()` and `format_for_reviewer()` methods on `TaskContracts`. Include `contracts_for_task()` and `has_contracts()` on `ContractSet`. All code is in Section 5 of the design doc.

### Step 2: Contract Builder LLM (`forge/core/contract_builder.py` — NEW FILE)
Create `ContractBuilderLLM` (makes the SDK call) and `ContractBuilder` (retry + validation wrapper). Follow the exact pattern from `forge/core/claude_planner.py` and `forge/core/planner.py`. System prompt is in Section 7. Include `_extract_json()` helper, `_parse_and_validate()` with task ID validation.

### Step 3: Enhanced Planner (`forge/core/models.py` + `forge/core/claude_planner.py`)
- Add `integration_hints: list[dict] | None = None` to `TaskDefinition` and `TaskGraph` in `models.py`
- Update `PLANNER_SYSTEM_PROMPT` in `claude_planner.py` — add `integration_hints` to the schema example and add the integration hints rules. See Section 6 for exact text.
- **IMPORTANT**: `integration_hints` must be optional (default None) for backward compatibility. Existing planner outputs without hints must still be valid.

### Step 4: Database Changes (`forge/storage/db.py`)
- Add `contracts_json = Column(Text, nullable=True)` to `PipelineRow`
- Add `set_pipeline_contracts()` and `get_pipeline_contracts()` methods
- The column is auto-added by `_add_missing_columns()` — no manual migration needed

### Step 5: Agent Prompt Integration (`forge/agents/adapter.py` + `forge/agents/runtime.py`)
- Add `{contracts_block}` placeholder to `AGENT_SYSTEM_PROMPT_TEMPLATE` (between `{conventions_block}` and `{dependency_context}`)
- Add "If Interface Contracts are provided above, you MUST implement them EXACTLY..." to the Rules section
- Add `contracts_block: str = ""` parameter to `_build_options()` and `run()` in adapter
- Add `contracts_block: str = ""` parameter to `AgentRuntime.run_task()` and pass it through

### Step 6: Pipeline Integration (`forge/core/daemon.py` + `forge/core/daemon_executor.py`)
- Add `generate_contracts()` method to `ForgeDaemon` in `daemon.py` (Section 11)
- Update `run()` to call `generate_contracts()` between `plan()` and `execute()`
- Store result as `self._contracts`
- Update `_stream_agent()` in `daemon_executor.py` to load contracts (from `self._contracts` or from DB) and pass `contracts_block` to runtime

### Step 7: Review Integration (`forge/core/daemon_review.py`)
- In `_run_review()`, load contracts for the task being reviewed
- Append `task_contracts.format_for_reviewer()` to `custom_review_focus` before passing to `gate2_llm_review()`
- Zero architectural changes — just enriching the existing review focus string

### Step 8: Model Router (`forge/core/model_router.py`)
- Add `"contract_builder"` as a valid role in model selection
- Map it to same logic as `"planner"` (typically uses the high-complexity model)

### Step 9: Validator Enhancement (`forge/core/validator.py`)
- Add optional validation: if `integration_hints` exists, verify referenced task IDs exist in the graph

### Step 10: Tests
Write tests for each new/modified file:
- `forge/core/contracts_test.py` — model round-trips, `contracts_for_task()` filtering, `format_for_agent()` output, `format_for_reviewer()` output, `has_contracts()` behavior
- `forge/core/contract_builder_test.py` — JSON parsing, validation (unknown task IDs), graceful degradation on failure, skip when no hints
- Update `forge/core/models_test.py` — TaskDefinition/TaskGraph with integration_hints (backward compat)
- Update `forge/core/claude_planner_test.py` — planner output with and without integration_hints
- Update `forge/agents/adapter_test.py` — contracts_block in system prompt

### Step 11: API + Frontend (minimal)
- Add `GET /pipelines/{id}/contracts` endpoint in `forge/api/routes/pipelines.py`
- Frontend: Show "Generating contracts..." phase in pipeline status (between "Planning" and "Executing")

## Critical Rules

1. **NEVER commit directly to main.** Create a clean branch from main, commit there, push, and open a PR.
2. **Branch naming**: Use `feat/contract-builder` as the branch name.
3. **All SDK calls go through `forge/core/sdk_helpers.py:sdk_query()`** — never call claude-code-sdk directly.
4. **`integration_hints` MUST be optional** (None default) on both TaskDefinition and TaskGraph. Existing pipelines without hints must work identically to today.
5. **Graceful degradation**: If contract generation fails after retries, return an empty `ContractSet` and proceed without contracts. Never crash the pipeline.
6. **Auto-skip**: If the planner produces no integration hints, skip the contract phase entirely (zero cost, zero latency).
7. **Follow existing patterns**: The ContractBuilder class should mirror the Planner class structure (abstract LLM → concrete LLM → orchestrator with retry + validation).
8. **Run existing tests** after each step to make sure nothing breaks: `python -m pytest forge/ -x -q`
9. **The design doc is the source of truth** — if anything in this prompt conflicts with `docs/design/contract-builder.md`, the design doc wins.

## What Success Looks Like

When you're done, a pipeline that has both BE and FE tasks will:
1. Planner outputs `integration_hints` identifying the API contract between them
2. Contract Builder generates precise API contracts (field names, types, response shapes)
3. Both agents receive the same contract in their prompts — one as "producing", one as "consuming"
4. Both tasks run in **parallel** (no `depends_on` needed for API integration)
5. The L2 reviewer checks contract compliance
6. Contracts are persisted in the DB and visible via API

A pipeline with no cross-task interfaces skips the contract phase entirely with zero overhead.
