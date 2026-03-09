<div align="center">

# Forge

### One command. Multiple agents. Reviewed code delivered via pull request.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Claude Code](https://img.shields.io/badge/powered%20by-Claude%20Code-cc785c?logo=anthropic&logoColor=white)](https://docs.anthropic.com/en/docs/claude-code)
[![Next.js](https://img.shields.io/badge/dashboard-Next.js-000?logo=next.js)](https://nextjs.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Forge is a multi-agent orchestration engine that takes a natural-language task, decomposes it into a parallel task graph, **generates cross-task interface contracts**, dispatches isolated Claude agents to write the code, reviews every change through a multi-gate pipeline, and merges everything into a clean PR — automatically.

[Quick Start](#-quick-start) &#8226; [How It Works](#-how-it-works) &#8226; [Contract Builder](#-contract-builder) &#8226; [Web Dashboard](#-web-dashboard) &#8226; [Configuration](#%EF%B8%8F-configuration)

</div>

<br/>

```bash
forge run "Build a REST API with JWT auth, user registration, and integration tests"
```

That's it. Forge plans the work, generates interface contracts so agents agree on API shapes before writing a line of code, spins up agents in parallel, reviews their output, and opens a pull request when everything passes.

<br/>

<p align="center">
  <img src="docs/screenshots/forge-pipeline-planning.png" alt="Forge — planning phase with task breakdown" width="800" />
</p>

<p align="center">
  <img src="docs/screenshots/forge-pipeline-complete.png" alt="Forge — completed pipeline with cost tracking" width="800" />
</p>

---

## Why Forge?

Writing code with an AI assistant is powerful — but you're still the bottleneck. You prompt one thing at a time, manually review every change, copy-paste between files, and pray that the backend and frontend agree on field names. Forge removes all of that.

| Pain point | How Forge solves it |
|---|---|
| You prompt one thing at a time | Forge decomposes your task into a **dependency graph** and runs independent tasks **in parallel** |
| Parallel agents produce incompatible interfaces | The **Contract Builder** generates binding API & type contracts *before* any code is written — agents build against the same spec |
| AI changes break other code | Each agent works in an **isolated git worktree** — no file conflicts between concurrent agents |
| You manually review AI output | A **multi-gate review pipeline** (build > lint > test > LLM review > contract compliance > merge check) catches issues before anything touches `main` |
| Context gets lost in long sessions | Each agent is a **fresh Claude session** with a focused prompt + its relevant contracts — no context bleeding |
| Merging is manual and error-prone | Forge **rebases and fast-forward merges** each task, then auto-creates a **pull request** |
| You can't control AI spend | **Per-pipeline budget limits** with real-time cost tracking — hard stop when the budget is hit |

---

## Quick Start

```bash
# Clone & install
git clone https://github.com/tarunms7/forge-orchestrator.git
cd forge-orchestrator && python -m venv .venv && source .venv/bin/activate
pip install -e .

# Check prerequisites
forge doctor

# Run in your project — no init required, Forge auto-creates .forge/ on first run
cd your-project
forge run "Add input validation to all API endpoints"
```

---

## How It Works

```
forge run "your task"
        |
   1. PLAN ---------> Claude decomposes into a task graph (DAG) with dependencies & integration hints
        |
   2. CONTRACT -----> Contract Builder generates binding API & type contracts from hints
        |
   3. EXECUTE ------> Parallel agents write code in isolated worktrees, each with its contracts injected
        |
   4. REVIEW -------> Multi-gate pipeline: build > lint > test > LLM review > contract compliance
        |
   5. MERGE --------> Rebase + fast-forward into working branch, auto-create PR
```

**Plan** — A Claude session decomposes your request into a DAG of tasks with dependencies, file ownership, complexity ratings, and *integration hints* that flag where tasks will interact (shared APIs, types, events). You can edit the plan before execution.

**Contract** — The Contract Builder takes integration hints and generates precise, binding interface contracts: API contracts (method, path, request/response schemas, producer/consumer task IDs) and type contracts (shared data structures with field specs). These contracts are generated *before* any code is written.

**Execute** — Each task gets its own git worktree. A Claude agent with full tool access writes the code and commits. Agents producing APIs are prompted with the exact response shape they must implement; agents consuming APIs receive the exact shape they should expect. Two independent agents, same spec, compatible on first try.

**Review** — Every task passes up to 5 gates: (1) configurable build command, (2) `ruff` lint on changed files, (3) configurable test command, (4) a separate Claude session reviews the diff against the task spec *and* contract compliance, (5) merge readiness check.

**Merge** — Task branches are rebased onto the working branch and fast-forward merged. When all tasks pass, Forge opens a pull request via `gh pr create`.

If any step fails, Forge retries the task up to 3 times with feedback from the failure.

---

## Contract Builder

> The #1 problem with multi-agent code generation isn't quality — it's **integration**. Two agents writing a backend API and a frontend client will independently invent different field names, response shapes, and auth patterns. Forge solves this with contracts.

The Contract Builder is a dedicated LLM stage that runs between planning and execution. It reads the task graph's integration hints, inspects the existing codebase for patterns, and generates precise interface contracts that every agent must follow.

<p align="center">
  <img src="docs/screenshots/forge-contract-builder.png" alt="Forge — Contract Builder showing API and type contracts" width="800" />
</p>

### What contracts look like

**API Contracts** define exact endpoint shapes:
```
POST /api/templates
  Request:  { name: string, description: string, tasks: TaskConfig[] }
  Response: { id: string, name: string, created_at: string }
  Producer: task-1 (backend)  |  Consumer: task-2 (frontend)
```

**Type Contracts** define shared data structures:
```
PipelineTemplate:
  id: string          — UUID for user-created, slug for built-in
  name: string        — Display name
  description: string — Human-readable summary
  tasks: TaskConfig[] — Array of task configurations
  Used by: task-1, task-2, task-3
```

### How contracts flow through the pipeline

1. **Planner** flags integration hints: *"task-1 produces a REST API that task-2 consumes"*
2. **Contract Builder** generates precise API & type contracts from hints + codebase context
3. **Agents** receive their contracts injected into the system prompt:
   - Producers see: *"You MUST implement these exact response shapes"*
   - Consumers see: *"You MUST call these exact endpoints with these shapes"*
4. **Reviewers** verify contract compliance: *"Does the diff match the contract?"*
5. **IDs are remapped** at runtime so contracts track prefixed task IDs seamlessly

Contracts degrade gracefully — if generation fails, agents proceed without them. No crash, no abort, just a softer guarantee.

---

## Web Dashboard

Forge includes a real-time web UI for monitoring and controlling pipelines:

```bash
forge serve   # Backend :8000 + Frontend :3000 (single-user mode by default)
```

> Set `FORGE_JWT_SECRET` to enable multi-user JWT authentication. Without it, Forge runs in single-user mode with no login required.

- **Live pipeline progress** via WebSocket with streaming agent output
- **Interactive plan editing** — drag-and-drop reordering, add/remove tasks, edit dependencies
- **Contract viewer** — browse generated API & type contracts with producer/consumer linkage
- **Review gate results** — build, lint, test, and LLM review status per task
- **Pre-merge approval gates** — review diffs and approve/reject before merge
- **Pause/resume** pipeline execution mid-flight
- **Real-time cost tracking** — per-task and per-pipeline with budget enforcement
- **One-click retry, cancel, and restart**
- **Auto-PR creation** when all tasks pass
- **Pipeline history** with duration, task counts, and cost

---

## Model Routing

Control cost vs. quality by routing different pipeline stages to different Claude models:

| Strategy | Planner | Contract Builder | Agent | Reviewer | Best for |
|---|---|---|---|---|---|
| `cost-optimized` | Haiku | Haiku | Sonnet | Haiku | Exploration, prototyping |
| `balanced` (default) | Sonnet | Sonnet | Sonnet | Haiku | Most tasks |
| `quality-first` | Opus | Opus | Opus | Sonnet | Complex architecture |

```bash
FORGE_MODEL_STRATEGY=quality-first forge run "Refactor the auth system to use OAuth2"
```

Per-stage model overrides are also supported:
```bash
FORGE_PLANNER_MODEL=opus FORGE_CONTRACT_BUILDER_MODEL=sonnet forge run "..."
```

---

## Configuration

All settings use the `FORGE_` env prefix. See [`.env.example`](.env.example) for all available settings with descriptions.

Build and test commands (`FORGE_BUILD_CMD`, `FORGE_TEST_CMD`) are **auto-detected** from your project when unset — Forge looks for `package.json`, `Makefile`, `pyproject.toml`, etc. and picks the right command automatically.

| Setting | Default | Description |
|---|---|---|
| `FORGE_MAX_AGENTS` | 4 | Max concurrent agent sessions |
| `FORGE_AGENT_TIMEOUT_SECONDS` | 600 | Per-task timeout (10 min) |
| `FORGE_MAX_RETRIES` | 5 | Retries per task on failure |
| `FORGE_BUILD_CMD` | *(auto-detected)* | Build command (e.g. `npm run build`) |
| `FORGE_TEST_CMD` | *(auto-detected)* | Test command (e.g. `pytest`) |
| `FORGE_BUDGET_LIMIT_USD` | 0 (unlimited) | Per-pipeline spend cap — hard stop when exceeded |
| `FORGE_REQUIRE_APPROVAL` | false | Require human approval before merging each task |
| `FORGE_MODEL_STRATEGY` | auto | Model routing strategy (`auto`, `fast`, `quality`) |
| `FORGE_CPU_THRESHOLD` | 80.0 | Max CPU % before backpressure |
| `FORGE_MEMORY_THRESHOLD_PCT` | 10.0 | Min available memory % |
| `FORGE_DISK_THRESHOLD_GB` | 5.0 | Min free disk space |

```bash
FORGE_BUILD_CMD="npm run build" FORGE_TEST_CMD="pytest -x" FORGE_BUDGET_LIMIT_USD=5 forge run "Add dark mode support"
```

---

## How Code Is Delivered

Your generated code arrives as a **pull request** — not pushed directly to `main`:

1. Each task works in an isolated git worktree (`/.forge/worktrees/task-N/`)
2. After passing review + contract compliance, each task branch is rebased and fast-forward merged into the working branch
3. When all tasks complete, Forge runs `gh pr create` automatically
4. You review and merge through your normal workflow

Worktrees are cleaned up after merge. Only the merged commits remain.

---

## Architecture

```
forge/
  cli/                 CLI entry (forge init, run, serve)
  config/
    settings.py        Pydantic settings (FORGE_ env prefix)
  core/
    daemon.py           Async orchestration loop
    planner.py          Task decomposition + integration hint extraction
    contract_builder.py Contract generation with validation & retry
    contracts.py        Contract models (API, Type, IntegrationHint, ContractSet)
    scheduler.py        DAG-aware scheduling + resource gating
    state.py            Task state machine
    model_router.py     Strategy-based model selection (includes contract_builder stage)
    monitor.py          CPU/memory/disk resource monitoring
    sdk_helpers.py      Claude Code SDK wrapper
  agents/
    adapter.py          ClaudeAdapter (agent interface + contract injection)
    runtime.py          Timeout-wrapped execution
  review/
    pipeline.py         Multi-gate review orchestration
    auto_check.py       Gate 1: ruff lint
    llm_review.py       Gate 2: LLM diff review + contract compliance
    merge_check.py      Gate 3: merge readiness
  merge/
    worktree.py         Git worktree lifecycle
    worker.py           Rebase + fast-forward merge
  storage/
    db.py               Async SQLAlchemy (SQLite/Postgres)
  api/
    routes/tasks.py     REST + WebSocket endpoints (includes /contracts API)
    models/schemas.py   Pydantic response models
web/
  src/                  Next.js + TypeScript + Zustand
    components/task/
      ContractsPanel.tsx  Contract viewer (API & type contracts with task linkage)
```

### Task State Machine

```
TODO --> IN_PROGRESS --> IN_REVIEW --> AWAITING_APPROVAL --> MERGING --> DONE
  |          |               |               |                 |
  +----> ERROR <-------------+---------------+-----------------+
  |
  +----> CANCELLED
```

> `AWAITING_APPROVAL` is only entered when `FORGE_REQUIRE_APPROVAL=true`. Otherwise tasks go straight from `IN_REVIEW` to `MERGING`.

---

## Testing

```bash
# 400+ unit tests across 30+ modules
pytest forge/ -q

# Frontend type check
cd web && npx tsc --noEmit
```

---

## Limitations

- **Cost** — Each task spawns 2-3 Claude sessions, plus the contract builder. A 4-task pipeline makes ~12 Claude calls. Use `cost-optimized` strategy for exploration, or set `FORGE_BUDGET_LIMIT_USD` to cap spend.
- **Speed** — Tasks with dependencies run sequentially. Independent tasks run in parallel.
- **Linting** — The lint gate currently lints Python files only (via `ruff`). Other languages pass automatically. Use `FORGE_BUILD_CMD` and `FORGE_TEST_CMD` for language-specific checks.
- **Merge conflicts** — If two tasks modify the same file, the later merge may fail and retry. The planner is instructed to avoid file overlap, but it's not guaranteed.
- **Contracts** — Contract generation adds ~15-30s to pipeline startup. For simple single-task pipelines with no cross-task interfaces, contracts are skipped automatically.

---

## Requirements

- Python 3.12+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated (`claude login`)
- Git 2.20+ (worktree support)
- `gh` CLI (for auto-PR creation)

Run `forge doctor` to verify your setup.

---

## License

MIT
