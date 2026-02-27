# Forge

**Multi-agent orchestration engine that decomposes coding tasks, dispatches parallel Claude agents, reviews their work, and merges it — all automatically.**

```
$ forge run "Build a REST API with JWT auth and user registration"

Planning...
Plan created: 4 tasks
  - task-1: Create user model and database schema
  - task-2: Implement JWT auth middleware
  - task-3: Build registration and login endpoints
  - task-4: Write integration tests

Starting task-1: Create user model and database schema
task-1 agent completed (187 diff lines)
  Gate 1: Auto-checks... passed
  Gate 2: LLM review... passed
  Gate 3 (merge readiness): auto-pass
task-1 merged successfully!

Starting task-2: Implement JWT auth middleware
...

Complete: 4 done, 0 errors
```

One command. Multiple agents. Reviewed and merged code on your `main` branch.

---

## How It Works

Forge takes a natural language task, breaks it into sub-tasks, and runs them through a full software engineering pipeline:

```
                         forge run "Build X"
                               |
                    +----------v-----------+
                    |      1. PLANNING      |
                    |  Claude decomposes    |
                    |  task into sub-tasks  |
                    +----------+-----------+
                               |
                    TaskGraph (DAG of tasks)
                               |
               +---------------+---------------+
               |               |               |
        +------v------+ +-----v-------+ +-----v-------+
        |   task-1    | |   task-2    | |   task-3    |
        | (worktree)  | | (worktree)  | | (worktree)  |
        +------+------+ +------+------+ +------+------+
               |               |               |
        2. EXECUTE       2. EXECUTE       (blocked:
        Claude agent     Claude agent      depends on
        writes code      writes code       task-1)
               |               |
        +------v------+ +-----v-------+
        | 3. REVIEW   | | 3. REVIEW   |
        | Gate 1: lint| | Gate 1: lint|
        | Gate 2: LLM | | Gate 2: LLM |
        | Gate 3: ok  | | Gate 3: ok  |
        +------+------+ +------+------+
               |               |
        +------v------+ +-----v-------+
        | 4. MERGE    | | 4. MERGE    |
        | rebase+ff   | | rebase+ff   |
        | into main   | | into main   |
        +-------------+ +-------------+
```

### The Pipeline in Detail

**1. Planning** — A Claude session (text-only, no tools) decomposes your request into a `TaskGraph`: a DAG of tasks with dependencies, file ownership, and complexity ratings. The planner retries with feedback if it produces invalid JSON or cyclic dependencies.

**2. Scheduling** — The scheduler finds tasks whose dependencies are all `DONE` and pairs them with idle agents. A resource monitor gates dispatch if CPU, memory, or disk is constrained.

**3. Execution** — Each task gets an isolated [git worktree](https://git-scm.com/docs/git-worktree) (its own branch and working directory). A Claude agent session with full tool access (`Read`, `Edit`, `Write`, `Bash`, etc.) implements the task and commits the changes.

**4. Review** — A 3-gate pipeline:
- **Gate 1 (Auto):** `ruff` lints only the changed Python files. Fast and deterministic.
- **Gate 2 (LLM):** A *separate* Claude session reviews the diff against the task spec. Returns `PASS` or `FAIL` with reasoning.
- **Gate 3 (Merge):** Auto-pass (merge readiness is handled by the merge step).

**5. Merge** — The task branch is rebased onto `main` and fast-forward merged. If there's a conflict, the task retries.

**6. Retry** — If any step fails (agent error, review rejection, merge conflict), the task retries up to 3 times with a fresh attempt.

---

## Where Does the Code Go?

Your generated code ends up **on your `main` branch**, with clean commits:

```
$ git log --oneline
edba7f5 feat: add comprehensive pytest test suite for fibonacci module
3cf368e feat: implement Fibonacci module with recursive and iterative approaches
69e9eef init
```

During execution, each task works in an isolated directory:

```
your-project/
  .forge/
    forge.db              # Task state (SQLite, fresh per run)
    worktrees/
      task-1/             # Isolated git worktree (auto-cleaned)
      task-2/             # Each task gets its own branch + directory
```

After merge, worktrees are deleted. Only the merged commits on `main` remain.

---

## Claude Sessions: What Gets Spawned

Forge spawns **separate Claude CLI processes** for each step. They do not share context — each is a fresh session with a specific role:

| Step | Claude Session | Tools | Purpose |
|------|---------------|-------|---------|
| Planning | `claude --print --max-turns 1` | None | Decompose task into JSON TaskGraph |
| Agent Execution | `claude --print --permission-mode bypassPermissions` | Read, Edit, Write, Glob, Grep, Bash | Write code in isolated worktree |
| Gate 2 Review | `claude --print --max-turns 1` | None | Review diff against task spec |

**Total Claude sessions per task:** 3 (plan once + execute + review). For N tasks, Forge spawns 1 + 2N Claude sessions.

All sessions use `claude-code-sdk` which wraps the `claude` CLI. Auth uses your existing `claude login` — no API key needed.

---

## Installation

### Prerequisites

- Python 3.12+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated (`claude login`)
- Git

### Install

```bash
git clone https://github.com/tarunms7/forge-orchestrator.git
cd forge-orchestrator
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Verify

```bash
forge --version    # Forge, version 0.1.0
claude --version   # Verify Claude CLI is available
```

---

## Quick Start

```bash
# 1. Go to your project (must be a git repo with at least one commit)
cd your-project
git init && git add -A && git commit -m "init"

# 2. Initialize Forge
forge init

# 3. Run a task
forge run "Create a Python function that calculates fibonacci numbers, with tests"
```

Forge will:
1. Plan the task into sub-tasks
2. Create agents and worktrees
3. Execute each task (Claude writes code)
4. Review changes (lint + LLM review)
5. Merge to `main`
6. Print a summary

---

## Configuration

All settings are in `forge/config/settings.py` and can be overridden via environment variables with the `FORGE_` prefix:

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `max_agents` | 4 | `FORGE_MAX_AGENTS` | Max concurrent agent sessions |
| `agent_timeout_seconds` | 1800 | `FORGE_AGENT_TIMEOUT_SECONDS` | Per-task timeout (30 min) |
| `max_retries` | 3 | `FORGE_MAX_RETRIES` | Retries per task on failure |
| `cpu_threshold` | 80.0 | `FORGE_CPU_THRESHOLD` | Max CPU % before backpressure |
| `memory_threshold_pct` | 10.0 | `FORGE_MEMORY_THRESHOLD_PCT` | Min memory % available |
| `disk_threshold_gb` | 5.0 | `FORGE_DISK_THRESHOLD_GB` | Min disk GB free |
| `scheduler_poll_interval` | 1.0 | `FORGE_SCHEDULER_POLL_INTERVAL` | Seconds between dispatch cycles |

Example:

```bash
FORGE_MAX_AGENTS=2 FORGE_MAX_RETRIES=1 forge run "Add dark mode support"
```

---

## Architecture

### Module Map

```
forge/
  cli/main.py              CLI entry point (forge init, forge run)
  core/
    daemon.py               Main async orchestration loop
    planner.py              Abstract planner + retry logic
    claude_planner.py       Claude-backed task decomposition
    scheduler.py            DAG-aware task scheduling
    state.py                Task state machine (TODO -> DONE)
    models.py               Pydantic data models (TaskGraph, TaskRecord)
    validator.py            TaskGraph validation (cycles, conflicts)
    monitor.py              Resource monitoring (CPU, memory, disk)
    sdk_helpers.py          Claude Code SDK wrapper
    errors.py               Error hierarchy
    engine.py               Deterministic engine (internal)
    continuity.py           Session handoff files
  agents/
    adapter.py              Agent interface + ClaudeAdapter
    runtime.py              Timeout-wrapped agent execution
  storage/
    db.py                   Async SQLAlchemy (SQLite or Postgres)
  merge/
    worktree.py             Git worktree lifecycle
    worker.py               Rebase + fast-forward merge
  review/
    pipeline.py             3-gate review orchestration
    auto_check.py           Gate 1: programmatic checks
    standards.py            Gate 1: AST-based code standards
    llm_review.py           Gate 2: LLM code review
    merge_check.py          Gate 3: merge readiness
  registry/
    index.py                Module registry (AST function indexing)
  config/
    settings.py             ForgeSettings (pydantic-settings)
  tui/
    dashboard.py            Rich status table
```

### Task State Machine

```
TODO ──> IN_PROGRESS ──> IN_REVIEW ──> MERGING ──> DONE
  |          |               |            |
  +──> ERROR <───────────────+────────────+
  |
  +──> CANCELLED
```

- `TODO`: Waiting for dependencies + idle agent
- `IN_PROGRESS`: Agent is writing code
- `IN_REVIEW`: Going through 3-gate review
- `MERGING`: Rebasing and merging to main
- `DONE`: Successfully merged
- `ERROR`: Failed after max retries

### Data Flow

```
User Input ──> ClaudePlannerLLM ──> TaskGraph (validated)
                                        |
                                   Store in DB
                                        |
                              Scheduler.dispatch_plan()
                                   /    |    \
                              task-1  task-2  task-3  (parallel)
                                |       |       |
                          Worktree  Worktree  Worktree  (git isolation)
                                |       |       |
                         ClaudeAdapter (code)  ...
                                |       |
                           3-Gate Review  ...
                                |       |
                          MergeWorker   ...
                                |       |
                          main branch updated
```

### Key Design Decisions

**Why git worktrees?** Each agent gets a full working directory on its own branch. No file conflicts between concurrent agents. Clean rollback on failure.

**Why separate Claude sessions?** Isolation. The planner reasons about task structure (no tools needed). The agent writes code (full tool access). The reviewer checks code quality (fresh perspective, no tool access). No context bleeding.

**Why rebase + fast-forward?** Clean linear history on main. Each task's commits appear in order. No merge commits cluttering the log.

**Why retry with feedback?** LLM outputs are non-deterministic. A failed lint check or review often succeeds on the second try. The planner also retries with validation error feedback.

---

## Database

Forge uses SQLite by default (async via aiosqlite). The DB is **fresh per run** — no stale state from previous runs.

### Schema

**Tasks Table:**
```
id | title | description | files (JSON) | depends_on (JSON) | complexity
state | assigned_agent | retry_count | branch_name | worktree_path
```

**Agents Table:**
```
id | state (idle/working/paused) | current_task
```

### Postgres Support

For production use with persistent state:

```bash
pip install forge-orchestrator[postgres]
FORGE_DB_URL="postgresql+asyncpg://user:pass@localhost/forge" forge run "..."
```

---

## Review Pipeline

Every task passes through 3 gates before merging:

### Gate 1: Auto-Checks
- Runs `ruff check` on changed Python files only (not the whole project)
- Fast, deterministic, no LLM cost
- Catches syntax errors, import issues, style violations

### Gate 2: LLM Review
- A fresh Claude session reviews the git diff against the task specification
- System prompt: "Review this code. Respond with PASS or FAIL."
- Checks: spec satisfaction, bugs, logic errors, quality, security
- If FAIL: task retries with a new agent attempt

### Gate 3: Merge Readiness
- Currently auto-pass (merge conflicts caught by the merge step)
- Can be configured to run test suites post-rebase

---

## Testing

Forge has 117 unit tests covering all modules:

```bash
# Run all tests
pytest forge/ -q

# Run specific module tests
pytest forge/core/scheduler_test.py -v
pytest forge/storage/db_test.py -v
pytest forge/review/pipeline_test.py -v
```

Test files follow the pattern `{module}_test.py` and live next to their source files.

---

## Limitations

- **Cost**: Each task spawns 2-3 Claude sessions. A 4-task project = ~10 Claude API calls. Monitor usage.
- **Speed**: Sequential within dependencies. A 4-task linear chain takes 4x agent execution time.
- **Language**: Gate 1 only lints Python files (via ruff). Other languages pass Gate 1 automatically.
- **Merge conflicts**: If two tasks touch the same file (which the planner is instructed to avoid), the second task's merge will fail and retry.
- **No streaming output**: Agent execution output is not streamed — you see results after each step completes.

---

## Project Status

- 117 unit tests passing
- E2E pipeline verified: plan -> execute -> review -> merge
- Tested with fibonacci, REST API, and multi-file tasks
- Works from Claude Code terminal sessions (CLAUDECODE env var handled)

---

## License

MIT
