# Forge

**One command. Multiple agents. Reviewed code delivered via pull request.**

Forge is a multi-agent orchestration engine that takes a natural-language task, decomposes it into parallel sub-tasks, dispatches isolated Claude agents to write the code, reviews every change through a multi-gate pipeline, and merges everything into a clean PR — automatically.

```bash
forge run "Build a REST API with JWT auth, user registration, and integration tests"
```

That's it. Forge plans the work, spins up agents in parallel, reviews their output, and opens a pull request when everything passes.

<p align="center">
  <img src="docs/screenshots/forge-pipeline-planning.png" alt="Forge — planning phase with task breakdown" width="800" />
</p>

<p align="center">
  <img src="docs/screenshots/forge-pipeline-complete.png" alt="Forge — completed pipeline with cost tracking" width="800" />
</p>

---

## Why Forge?

Writing code with an AI assistant is powerful — but you're still the bottleneck.

| Pain point | How Forge solves it |
|---|---|
| You prompt one thing at a time | Forge decomposes your task into a **dependency graph** and runs independent tasks **in parallel** |
| AI changes break other code | Each agent works in an **isolated git worktree** — no file conflicts between concurrent agents |
| You manually review AI output | A **multi-gate review pipeline** (build → lint → test → LLM review → merge check) catches issues before anything touches `main` |
| Context gets lost in long sessions | Each agent is a **fresh Claude session** with a focused prompt — no context bleeding |
| Merging is manual and error-prone | Forge **rebases and fast-forward merges** each task, then auto-creates a **pull request** |
| You can't control AI spend | **Per-pipeline budget limits** with real-time cost tracking — hard stop when the budget is hit |

---

## Quick Start

```bash
# Install
git clone https://github.com/tarunms7/forge-orchestrator.git
cd forge-orchestrator && python -m venv .venv && source .venv/bin/activate
pip install -e .

# Prerequisites: Python 3.12+, Git, Claude CLI (claude login)

# Run in your project
cd your-project
forge init
forge run "Add input validation to all API endpoints with proper error messages"
```

---

## What Happens Under the Hood

```
forge run "your task"
        |
   1. PLAN -----> Claude decomposes into a task graph (DAG)
        |
   2. EXECUTE --> Parallel agents write code in isolated worktrees
        |
   3. REVIEW ---> Multi-gate pipeline: build → lint → test → LLM review → merge check
        |
   4. MERGE ----> Rebase + fast-forward into working branch, auto-create PR
```

**Planning** — A text-only Claude session decomposes your request into a DAG of tasks with dependencies, file ownership, and complexity ratings. You can edit the plan before execution — reorder tasks, add/remove tasks, or change file assignments.

**Execution** — Each task gets its own git worktree (isolated branch + directory). A Claude agent with full tool access writes the code and commits.

**Review** — Every task passes up to 5 gates: (1) configurable build command, (2) `ruff` lint on changed files, (3) configurable test command, (4) a separate Claude session reviews the diff against the task spec, (5) merge readiness check. You can optionally require human approval before merge.

**Merge** — Task branches are rebased onto the working branch and fast-forward merged. When all tasks pass, Forge opens a pull request via `gh pr create`.

If any step fails, Forge retries the task up to 3 times with feedback from the failure.

---

## Web Dashboard

Forge includes a real-time web UI for monitoring and controlling pipelines:

```bash
export FORGE_JWT_SECRET="your-secret-key"
forge serve   # Backend :8000 + Frontend :3000
```

- **Live pipeline progress** via WebSocket with streaming agent output
- **Interactive plan editing** — drag-and-drop reordering, add/remove tasks, edit dependencies
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

| Strategy | Planner | Agent | Reviewer | Best for |
|---|---|---|---|---|
| `cost-optimized` | Haiku | Sonnet | Haiku | Exploration, prototyping |
| `balanced` (default) | Sonnet | Sonnet | Haiku | Most tasks |
| `quality-first` | Opus | Opus | Sonnet | Complex architecture |

```bash
FORGE_MODEL_STRATEGY=quality-first forge run "Refactor the auth system to use OAuth2"
```

---

## Configuration

All settings use the `FORGE_` env prefix or `forge/config/settings.py`:

| Setting | Default | Description |
|---|---|---|
| `FORGE_MAX_AGENTS` | 4 | Max concurrent agent sessions |
| `FORGE_AGENT_TIMEOUT_SECONDS` | 600 | Per-task timeout (10 min) |
| `FORGE_MAX_RETRIES` | 3 | Retries per task on failure |
| `FORGE_BUILD_CMD` | *(none)* | Shell command to run as build gate (e.g. `npm run build`) |
| `FORGE_TEST_CMD` | *(none)* | Shell command to run as test gate (e.g. `pytest`) |
| `FORGE_BUDGET_LIMIT_USD` | 0 (unlimited) | Per-pipeline spend cap — hard stop when exceeded |
| `FORGE_REQUIRE_APPROVAL` | false | Require human approval before merging each task |
| `FORGE_MODEL_STRATEGY` | balanced | Model routing strategy |
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
2. After passing review, each task branch is rebased and fast-forward merged into the working branch
3. When all tasks complete, Forge runs `gh pr create` automatically
4. You review and merge through your normal workflow

Worktrees are cleaned up after merge. Only the merged commits remain.

---

## Architecture

```
forge/
  cli/               CLI entry (forge init, run, serve)
  config/
    settings.py      Pydantic settings (FORGE_ env prefix)
  core/
    daemon.py         Async orchestration loop
    planner.py        Task decomposition + retry logic
    scheduler.py      DAG-aware scheduling + resource gating
    state.py          Task state machine
    model_router.py   Strategy-based model selection
    monitor.py        CPU/memory/disk resource monitoring
    sdk_helpers.py    Claude Code SDK wrapper
  agents/
    adapter.py        ClaudeAdapter (agent interface)
    runtime.py        Timeout-wrapped execution
  review/
    pipeline.py       Multi-gate review orchestration
    auto_check.py     Gate 1: ruff lint
    llm_review.py     Gate 2: LLM diff review
    merge_check.py    Gate 3: merge readiness
  merge/
    worktree.py       Git worktree lifecycle
    worker.py         Rebase + fast-forward merge
  storage/
    db.py             Async SQLAlchemy (SQLite/Postgres)
  api/
    routes/tasks.py   REST + WebSocket endpoints
web/
  src/                Next.js 14 + TypeScript + Tailwind + Zustand
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
# Unit tests across 30+ modules
pytest forge/ -q

# Frontend type check
cd web && npx tsc --noEmit
```

---

## Limitations

- **Cost** — Each task spawns 2-3 Claude sessions. A 4-task pipeline makes ~10 Claude calls. Use `cost-optimized` strategy for exploration, or set `FORGE_BUDGET_LIMIT_USD` to cap spend.
- **Speed** — Tasks with dependencies run sequentially. Independent tasks run in parallel.
- **Linting** — The lint gate currently lints Python files only (via `ruff`). Other languages pass automatically. Use `FORGE_BUILD_CMD` and `FORGE_TEST_CMD` for language-specific checks.
- **Merge conflicts** — If two tasks modify the same file, the later merge may fail and retry. The planner is instructed to avoid file overlap, but it's not guaranteed.

---

## Requirements

- Python 3.12+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated (`claude login`)
- Git 2.20+ (worktree support)
- `gh` CLI (for auto-PR creation)

---

## License

MIT
