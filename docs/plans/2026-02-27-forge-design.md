# Forge — Multi-Agent Orchestration Engine

## Design Document

**Date:** 2026-02-27
**Status:** Approved
**Author:** mtarun + Claude

---

## 1. Vision

Forge is a hybrid multi-agent orchestration engine for AI coding agents. Core principle: **LLMs propose, code disposes.**

Unlike existing systems (Delegate, Entourage) where an LLM agent acts as the manager, Forge uses a deterministic orchestration engine with LLM intelligence only where it's needed: task decomposition and code execution.

### Goals

- Reliable multi-agent coding that actually ships working code
- Resource-aware scheduling (the gap no existing tool fills)
- Mandatory review pipeline — no code merges without passing three gates
- Coding standards enforced programmatically, not just documented
- Cross-session continuity via structured build logs
- Zero-config local setup (SQLite), optional Postgres for scale

### Non-Goals (v1)

- Cloud burst / remote execution (architecture supports it, not built yet)
- Web dashboard (TUI first)
- Multi-user / team features
- Non-Claude agent backends (adapter interface exists, only Claude implemented)

---

## 2. Architecture

```
USER INPUT
    │
    ▼
PLANNER (LLM Layer)
    Decomposes task → structured TaskGraph (typed JSON)
    Identifies file ownership per subtask
    Estimates complexity per subtask
    │
    ▼
ORCHESTRATION ENGINE (Deterministic Code)
    ├── Validator: cycles, file conflicts, schema
    ├── Scheduler: DAG ordering, priority, ready queue
    ├── Resource Monitor: CPU/mem/disk, dynamic throttle, backpressure
    ├── State Machine: todo → wip → review → done
    └── Merge Pipeline: rebase → test → merge
    │
    ▼
EXECUTION LAYER (LLM Agents, max ~4)
    Each agent: isolated git worktree, scoped file access
    │
    ▼
REVIEW PIPELINE (Mandatory, 3 gates)
    Gate 1 → Gate 2 → Gate 3 → Merge to main
```

### Key Differentiators

1. Orchestration engine is deterministic code, not an LLM agent
2. Planner output is a validated, typed TaskGraph — not free-form messages
3. Resource monitoring is a first-class subsystem
4. Agents are scoped to specific files — no two agents touch the same files
5. Default 2-4 agents (research-backed sweet spot, not 32-256)

---

## 3. Core Components

### 3.1 Planner (LLM Layer)

Takes user input + codebase context, produces a TaskGraph:

```json
{
  "tasks": [
    {
      "id": "task-1",
      "title": "Create user model",
      "description": "...",
      "files": ["src/models/user.py", "src/models/__init__.py"],
      "depends_on": [],
      "complexity": "low"
    },
    {
      "id": "task-2",
      "title": "Build auth endpoints",
      "files": ["src/api/auth.py", "src/api/__init__.py"],
      "depends_on": ["task-1"],
      "complexity": "medium"
    }
  ]
}
```

The engine validates this graph before accepting it:
- No circular dependencies
- No file ownership conflicts (two tasks can't own the same file)
- Schema validation (all required fields present, types correct)
- Complexity estimates within bounds

If validation fails, the planner is asked to revise.

### 3.2 Orchestration Engine (Deterministic Code)

#### Validator
- Cycle detection in DAG (topological sort)
- File ownership conflict detection
- TaskGraph schema validation (Pydantic models)

#### Scheduler
- Maintains a ready queue: tasks whose dependencies are all `done`
- Dispatches tasks to available agents based on priority
- Respects resource constraints from the monitor

#### Resource Monitor
- Uses `psutil` for real-time system metrics
- Tracks: CPU usage, available memory, disk space, agent subprocess count
- Dynamic concurrency: if CPU > 80% or memory < 20% free, pause new dispatches
- Backpressure: queued tasks wait until resources free up
- Reports metrics to TUI dashboard

#### State Machine
```
todo → in_progress → in_review → merging → done
                  ↑               │
                  └── rejected ───┘  (max 3 retries, then escalate)

                  → cancelled (terminal)
                  → error (terminal, escalate to user)
```

#### Merge Pipeline
1. Create disposable merge worktree
2. Rebase task branch onto main
3. Run full test suite
4. Fast-forward merge to main
5. Cleanup worktree
6. Squash-reapply fallback if rebase fails

### 3.3 Execution Layer (LLM Agents)

Each agent is a Claude Code subprocess via `claude_agent_sdk`:
- Isolated git worktree per task
- File access scoped to task's declared files
- Tool access constrained (no branch topology commands)
- Context rotation at ~80k tokens with structured summary
- Agent prompt includes: task spec, relevant module registry entries, coding standards

### 3.4 Review Pipeline (Mandatory, 3 Gates)

#### Gate 1: Auto-Check (Programmatic)
- Tests pass
- Lint clean
- No file conflicts with other agents
- Build succeeds
- Standards check (function length, file length, type hints, no bare except)
- FAIL → back to agent with specific errors

#### Gate 2: LLM Review (Separate Claude Instance)
- Fresh context, NOT the same agent that wrote the code
- Reviews against: original task spec, coding standards, security checklist
- Checks for code duplication against module registry
- FAIL → back to agent with review notes

#### Gate 3: Merge Check (Programmatic)
- Rebase on latest main
- Tests pass post-rebase
- No merge conflicts
- FAIL → back to agent to resolve

**Bounded failure loops:** 3 failures at same gate → escalate to user.

---

## 4. Coding Standards Enforcement

### STANDARDS.md (Source of Truth)

#### Architecture
- SOLID principles: every class has one responsibility
- No function longer than 30 lines (extract helpers)
- No file longer than 300 lines (split into modules)
- Dependency injection over hard-coded dependencies

#### Reuse-First
- Before writing ANY new function, search existing codebase
- Common patterns live in core/utils/
- If 3+ lines of logic appear twice, extract to shared function

#### Modularity
- One module = one concern
- Public API at top of file, private helpers below
- No circular imports (enforced by validator)
- Type hints on all public functions

#### Error Handling
- Custom exception hierarchy (ForgeError base)
- Never bare except
- Errors carry context (what failed, why, what to do)

#### Testing
- Every public function has at least one test
- Tests live next to code: module.py → module_test.py
- No test depends on another test's state

### Module Registry

Auto-maintained index of every function, class, and utility in the codebase.
Updated on every commit to main. Agents receive relevant registry entries
in their prompt so they reuse existing code instead of duplicating it.

Gate 2 (LLM Review) cross-references new code against the registry to
catch duplication.

### Programmatic Enforcement (Part of Gate 1)

```
checks:
  - max_function_length: 30 lines
  - max_file_length: 300 lines
  - no_circular_imports: dependency graph is acyclic
  - type_hints_on_public_api: all public functions typed
  - no_bare_except: no bare except clauses
  - no_duplicate_signatures: cross-ref module registry
```

---

## 5. Cross-Session Continuity

### The Problem

Token limits exhaust mid-build. New sessions need to pick up exactly
where the last one left off with full context.

### Persistent Files

```
.forge/
  build-log.md           ← Checklist of all tasks with status
  session-handoff.md     ← Exact state at end of last session
  module-registry.json   ← Auto-updated index of all code
  decisions.md           ← Architectural decisions and rationale
  architecture.md        ← Current system shape
```

### End-of-Session Contract

Every session updates before ending:

| File | Contents |
|------|----------|
| build-log.md | Overall progress checklist |
| session-handoff.md | WIP state, files touched, blockers, next steps |
| module-registry.json | Regenerated from current codebase |
| decisions.md | New decisions made this session |

### Start-of-Session Contract

Every session reads on startup:

1. build-log.md — overall progress
2. session-handoff.md — where exactly we stopped
3. STANDARDS.md — coding rules
4. module-registry.json — what code exists
5. decisions.md — what's been decided

---

## 6. Tech Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.12+ | Best Claude Agent SDK support |
| Agent SDK | claude_agent_sdk | First-class Claude Code integration |
| Database | SQLite (default), Postgres (optional) | Zero-config local, scale when needed |
| ORM | SQLAlchemy 2.0 async | Abstracts SQLite/Postgres difference |
| Async | asyncio + uvloop | Event loop for daemon + agent dispatch |
| System monitoring | psutil | CPU/memory/disk metrics |
| CLI | click | Standard Python CLI framework |
| TUI | Rich + Textual | Terminal dashboard |
| Git isolation | git worktrees | Filesystem-level agent isolation |
| Schema validation | Pydantic v2 | TaskGraph and message schemas |
| Testing | pytest + pytest-asyncio | Standard Python testing |

### Project Structure

```
forge/
  core/
    engine.py          ← Orchestration engine (scheduler, dispatcher)
    planner.py         ← LLM planning layer (task decomposition)
    validator.py       ← TaskGraph validation (cycles, conflicts, schema)
    scheduler.py       ← DAG-aware task scheduling + ready queue
    monitor.py         ← Resource monitoring (CPU/mem/disk)
    state.py           ← Task state machine
    models.py          ← Pydantic models (TaskGraph, Task, Agent, etc.)
  agents/
    runtime.py         ← Agent subprocess lifecycle
    telephone.py       ← Persistent Claude subprocess wrapper
    sandbox.py         ← File/tool access scoping
  review/
    pipeline.py        ← 3-gate review orchestration
    auto_check.py      ← Gate 1: programmatic checks
    llm_review.py      ← Gate 2: LLM code review
    merge_check.py     ← Gate 3: pre-merge validation
    standards.py       ← Standards enforcement checks
  merge/
    worker.py          ← Merge execution (rebase, test, merge)
    worktree.py        ← Git worktree lifecycle
  registry/
    index.py           ← Module registry (build + query)
  storage/
    db.py              ← Database layer (SQLAlchemy models)
    migrations/        ← Alembic migrations
  cli/
    main.py            ← CLI entry point
    commands.py        ← CLI commands
  tui/
    dashboard.py       ← Textual TUI
  config/
    settings.py        ← Configuration management
  STANDARDS.md
  .forge/
    build-log.md
    session-handoff.md
    module-registry.json
    decisions.md
```

---

## 7. Design Decisions Log

| Decision | Choice | Alternatives Considered | Rationale |
|----------|--------|------------------------|-----------|
| Orchestration model | Hybrid (LLM plans, code enforces) | Pure LLM manager, pure deterministic | LLM creativity + programmatic reliability |
| Agent backend | Claude Code primary, others optional | Claude-only, pluggable from day 1 | Optimize for best integration, keep door open |
| Deployment | Local-first, cloud burst later | Local-only, cloud-first | Zero-config start, scale path exists |
| Resource handling | Monitor and throttle | Predictive scheduling, cloud burst | Solves the gap with minimum complexity |
| Database | SQLite default, Postgres optional | SQLite-only, Postgres-only | Zero-config local, real scaling path |
| Review pipeline | Mandatory 3-gate | Optional review, single gate | Reliability is the core value proposition |
| Max agents | ~4 default | 32-256 (like Delegate/Entourage) | Research shows accuracy saturates at 4 |
| Standards | Programmatic enforcement | Documentation only | Docs get ignored, code checks don't |
