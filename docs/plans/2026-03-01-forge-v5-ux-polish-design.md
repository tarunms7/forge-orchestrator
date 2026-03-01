# Forge v5: UX Polish, Shared Context & README

**Date:** 2026-03-01
**Status:** Draft

## Problem Statement

Forge pipelines waste tokens on redundant codebase scanning (each of 1+2N Claude sessions independently discovers the project), provide no visibility during planning ("Connecting..." then plan appears), display activity in a disconnected bottom timeline instead of per-task, and the README is stale (117 tests, no Web UI, says code merges to main).

## Design Decisions

### 1. Project Snapshot — Shared Context (P0)

**Root cause of redundant scanning:** `_gather_context()` only returns a flat file list. The planner (opus) then spends 7+ turns reading files. Each agent also independently scans the codebase. With 4 agents, that's 5 independent codebase explorations.

**Solution:** Build a rich `ProjectSnapshot` once per pipeline and inject it into every Claude session's prompt.

#### 1a. Snapshot Content

The snapshot captures everything a Claude session needs to understand the project *before* using tools:

```python
@dataclass
class ProjectSnapshot:
    file_tree: str          # Hierarchical tree with file sizes (like `tree -sh`)
    total_files: int
    total_loc: int          # Lines of code (non-empty, non-comment)
    languages: dict[str, int]  # Language -> file count
    readme_excerpt: str     # First 200 lines of README.md (if exists)
    config_summary: str     # pyproject.toml/setup.py key fields
    module_index: str       # Top-level modules with docstrings
    recent_commits: str     # Last 10 commits (oneline)
    git_branch: str         # Current branch name
```

#### 1b. Gathering Logic

New function `gather_project_snapshot()` in `forge/core/context.py`:

```
gather_project_snapshot(project_dir: str) -> ProjectSnapshot
  1. file_tree:     `git ls-files` piped through a tree formatter (respects .gitignore)
  2. total_loc:     `wc -l` on tracked files (fast, subprocess)
  3. languages:     Extension counting from git ls-files
  4. readme:        Read first 200 lines of README.md if present
  5. config:        Parse pyproject.toml [project] section (name, version, deps, python-requires)
  6. module_index:  For each top-level Python package, read __init__.py docstring
  7. recent_commits: `git log --oneline -10`
  8. git_branch:    `git rev-parse --abbrev-ref HEAD`
```

**Performance target:** < 2 seconds for a 500-file repo. All operations are local git/filesystem — no LLM calls.

#### 1c. Injection Points

The snapshot is injected into three places:

| Session | Current Context | With Snapshot |
|---------|----------------|---------------|
| Planner | Flat file list via `_gather_context()` | Full snapshot as prompt prefix |
| Agent | Working directory only (system prompt) | Snapshot appended to system prompt |
| L2 Reviewer | Task spec + diff only | Snapshot prefix for architectural awareness |

**Planner prompt change:**
```
Before: "Project files:\n./forge/core/daemon.py\n./forge/agents/adapter.py\n..."
After:   Full ProjectSnapshot text block (tree, README excerpt, config, module index)
```

The planner should still have tool access (max_turns=10) — the snapshot reduces exploratory reads from ~7 to ~1-2, but doesn't eliminate the need for targeted deep reads.

**Agent system prompt change:**
```
Before: "Your working directory is {cwd}. Do NOT read outside this directory."
After:  "Your working directory is {cwd}. Do NOT read outside this directory.

         === PROJECT CONTEXT ===
         {snapshot.format_for_agent()}
         === END PROJECT CONTEXT ==="
```

`format_for_agent()` returns a condensed version: file tree + config summary + module index. No README (agents don't need project description — they have a task spec).

**L2 reviewer change:**
```
Before: "Task: {title}\nDescription: {description}\nGit diff:\n{diff}"
After:  "=== PROJECT CONTEXT ===\n{snapshot.format_for_reviewer()}\n===\n\nTask: {title}\n..."
```

`format_for_reviewer()` returns: file tree + module index. Gives the reviewer enough context to understand whether the code fits the project's architecture.

#### 1d. Caching

The snapshot is computed once in `daemon.plan()` and stored on the `ForgeDaemon` instance:

```python
class ForgeDaemon:
    async def plan(self, user_input, db, ...):
        self._snapshot = gather_project_snapshot(self._project_dir)
        # ... pass to planner

    async def _execute_task(self, ...):
        # ... pass self._snapshot to agent
```

For the web flow, the snapshot is also stored in the pipeline row (JSON column) so it survives server restarts during long-running pipelines.

#### 1e. Event Emission

Emit `pipeline:snapshot_ready` after gathering, before planning starts:

```python
await self._emit("pipeline:snapshot_ready", {
    "total_files": snapshot.total_files,
    "total_loc": snapshot.total_loc,
    "languages": snapshot.languages,
    "git_branch": snapshot.git_branch,
}, db=db, pipeline_id=pid)
```

Frontend displays this as a summary card during the planning phase.

### 2. Planning UI Visibility (P0)

**Root cause:** The daemon emits `pipeline:phase_changed` with `phase: "planning"` at the start and `pipeline:plan_ready` at the end. Nothing in between. The `planner:output` event type is already registered in the WebSocket bridge but never emitted.

**Solution:** Stream planner activity to the frontend using the existing `on_message` callback pattern.

#### 2a. Backend: Planner Streaming

Add `on_message` parameter to `ClaudePlannerLLM.generate_plan()`:

```python
class ClaudePlannerLLM(PlannerLLM):
    async def generate_plan(self, user_input, context, feedback=None, on_message=None) -> str:
        options = ClaudeCodeOptions(...)
        result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
```

In `daemon.plan()`, wire an `on_message` callback that emits `planner:output`:

```python
async def plan(self, user_input, db, ...):
    async def _on_planner_msg(msg):
        text = _extract_text(msg)
        if text:
            await self._emit("planner:output", {"line": text}, db=db, pipeline_id=pid)

    planner_llm = ClaudePlannerLLM(model=planner_model, cwd=self._project_dir)
    # Pass streaming callback through Planner to LLM
    graph = await planner.plan(user_input, context=snapshot_text, on_message=_on_planner_msg)
```

This requires threading `on_message` through the `Planner.plan()` → `PlannerLLM.generate_plan()` chain. The abstract `PlannerLLM` interface gets an optional `on_message` parameter.

**Event payload:**
```json
{"type": "planner:output", "line": "Reading forge/core/daemon.py..."}
{"type": "planner:output", "line": "Identified 3 modules that need changes..."}
```

The `planner:output` type is already in the `_bridge_events` list — no WebSocket changes needed.

#### 2b. Frontend: PlannerCard Component

New component: `web/src/components/task/PlannerCard.tsx`

Displayed when `phase === "planning"`:

```
┌─────────────────────────────────────────┐
│ 🧠 Planning                            │
│ Model: opus  |  Strategy: quality-first │
│                                         │
│ ┌─────────────────────────────────────┐ │
│ │ Reading forge/core/daemon.py...     │ │
│ │ Reading forge/agents/adapter.py...  │ │
│ │ Analyzing module dependencies...    │ │
│ │ ▊                                   │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

- Shows streaming planner output (like agent output, auto-scrolling)
- Displays model name and strategy
- Animated pulse indicator while active
- Collapsed into a summary card once planning completes (shows task count, duration)

#### 2c. Store Changes

The `taskStore.ts` already has `plannerOutput: string[]` — it's populated but never rendered. The `handleEvent` for `planner:output` already exists:

```typescript
case "planner:output":
    set((s) => ({ plannerOutput: [...s.plannerOutput, event.line] }));
    break;
```

Only the rendering is missing. Add `plannerModel` and `plannerStrategy` fields to store (sent via `pipeline:phase_changed` payload).

### 3. Timeline Rework — Per-Task Activity (P1)

**Root cause:** The `TimelinePanel` at the bottom shows all events in a flat chronological list. Events from different tasks interleave, making it hard to follow any single task's journey.

**Solution:** Move activity into each `AgentCard` and `TaskDetailPanel`. Remove the bottom `TimelinePanel`.

#### 3a. Per-Task Activity in AgentCard

Add a collapsible "Activity" section to `AgentCard` between the existing "Agent Output" and "Review Gates" sections:

```
┌─ task-1: Create user model ──────── ✅ done ─┐
│                                               │
│ Files Changed: models/user.py, db/schema.py   │
│                                               │
│ ▾ Activity (6 events)                         │
│ ┌───────────────────────────────────────────┐ │
│ │ 2:31 PM  State: pending → working        │ │
│ │ 2:31 PM  Agent started (sonnet)           │ │
│ │ 2:33 PM  State: working → in_review      │ │
│ │ 2:33 PM  L1 lint: ✅ passed              │ │
│ │ 2:33 PM  L2 review: ✅ passed            │ │
│ │ 2:34 PM  Merged successfully (+87 -0)    │ │
│ └───────────────────────────────────────────┘ │
│                                               │
│ Cost: $0.12                                   │
└───────────────────────────────────────────────┘
```

**Data source:** Filter `timeline[]` from the store by `taskId === task.id`. The timeline entries already have `task_id` fields.

#### 3b. Pipeline-Level Events → Top Banner

Events without a `task_id` (pipeline-level: phase changes, PR status, preflight results) move to a small banner area between `PipelineProgress` and the agent cards grid:

```
┌─────────────────────────────────────────────┐
│ ℹ Pipeline: Executing (3/5 tasks complete)  │
│ ⏱ Started: 2:30 PM  |  Elapsed: 4m 12s     │
└─────────────────────────────────────────────┘
```

This replaces the need for pipeline-level timeline entries.

#### 3c. TaskDetailPanel Enhancement

The slide-out `TaskDetailPanel` already shows review gates and merge result. Add the full activity log (same data as the collapsed AgentCard activity, but always expanded and with more detail):

- Full agent output (already there)
- Activity log with timestamps (new section, between output and review gates)
- Expandable event payloads (click to see raw JSON for debugging)

#### 3d. Remove TimelinePanel

Delete `TimelinePanel.tsx` and remove it from `page.tsx`. The `timeline[]` array in the store stays — it's still populated and used for per-task filtering. The data model doesn't change, only the rendering.

#### 3e. Store Changes

Add a derived selector for per-task timeline filtering:

```typescript
// In taskStore.ts or as a selector
export const useTaskTimeline = (taskId: string) =>
    useTaskStore((s) => s.timeline.filter(e => e.taskId === taskId));
```

### 4. README Comprehensive Update (P1)

**What's wrong:** The README reflects Forge v0.1.0 (pre-Web-UI, pre-event-sourcing, pre-PR-workflow). Multiple sections contain outdated information.

#### 4a. Sections to Update

| Section | Current | Should Be |
|---------|---------|-----------|
| Test count | 117 | 421+ |
| CLI sessions table | max_turns=1, no tools for planner | max_turns=10, planner reads files |
| CLI sessions table | max_turns=1 for reviewer | max_turns=2 for reviewer |
| Merge flow | "merged code on your `main` branch" | PR-based workflow via `gh pr create` |
| "No streaming output" limitation | Listed | Remove (WebSocket streaming exists) |
| Project status | "117 unit tests passing" | "421+ unit tests, 30+ modules" |
| Architecture module map | Missing web/ modules | Add FastAPI, Next.js, WebSocket modules |

#### 4b. New Sections to Add

**Web UI section** (after Quick Start):
```markdown
## Web UI

Forge includes a full web dashboard for managing pipelines:

\`\`\`bash
# Start the backend + frontend
forge serve

# Backend: http://localhost:8000
# Frontend: http://localhost:3000
\`\`\`

Features:
- Real-time pipeline progress via WebSocket
- Agent output streaming (live code generation)
- Review gate results with expandable details
- One-click task retry and pipeline resume
- Auto-PR creation when all tasks pass
```

**Model routing section** (after Configuration):
```markdown
## Model Routing

Forge routes different pipeline stages to different Claude models based on your strategy:

| Strategy | Planner | Agent | Reviewer |
|----------|---------|-------|----------|
| `cost-optimized` | haiku | sonnet | haiku |
| `balanced` | sonnet | sonnet | haiku |
| `quality-first` | opus | opus | sonnet |
```

#### 4c. Diagram Updates

Update the pipeline diagram to show PR creation:
```
4. MERGE → rebase + ff → auto-PR creation → code review
```

Update the architecture module map to include:
```
forge/
  api/
    routes/tasks.py        Pipeline REST + WebSocket endpoints
    ws/manager.py          WebSocket connection manager
  core/
    events.py              Event emitter (pub/sub)
    model_router.py        Strategy-based model selection
    context.py             Project snapshot gathering (NEW)
web/
  src/
    app/tasks/view/        Pipeline execution view
    components/task/       AgentCard, PipelineProgress, PlannerCard (NEW)
    stores/taskStore.ts    Zustand state management
    hooks/useWebSocket.ts  WebSocket hook
```

#### 4d. Updated Limitations

Remove:
- "No streaming output" (false — WebSocket streaming exists)

Update:
- Cost section: mention model routing strategies for cost control
- Add: "Web UI requires Node.js 18+ for the Next.js frontend"

Keep:
- Language limitation (Gate 1 only lints Python)
- Merge conflict behavior
- Speed limitation (sequential within dependencies)

## Files to Create/Modify

| File | Action | Section |
|------|--------|---------|
| `forge/core/context.py` | **Create** | 1: Project snapshot gathering |
| `forge/core/daemon.py` | Modify | 1: Inject snapshot; 2: Planner streaming |
| `forge/agents/adapter.py` | Modify | 1: Accept + inject snapshot into system prompt |
| `forge/core/planner.py` | Modify | 2: Thread on_message through to LLM |
| `forge/core/claude_planner.py` | Modify | 2: Accept on_message, pass to sdk_query |
| `forge/review/llm_review.py` | Modify | 1: Accept + inject snapshot for reviewer context |
| `forge/api/routes/tasks.py` | Modify | 1: Emit snapshot_ready; 2: Bridge planner events |
| `web/src/components/task/PlannerCard.tsx` | **Create** | 2: Planning visibility UI |
| `web/src/components/task/AgentCard.tsx` | Modify | 3: Add per-task activity section |
| `web/src/components/task/TaskDetailPanel.tsx` | Modify | 3: Add activity log section |
| `web/src/components/task/TimelinePanel.tsx` | **Delete** | 3: Remove bottom timeline |
| `web/src/app/tasks/view/page.tsx` | Modify | 2: Add PlannerCard; 3: Remove TimelinePanel, add pipeline banner |
| `web/src/stores/taskStore.ts` | Modify | 2: Add planner metadata; 3: Add per-task timeline selector |
| `README.md` | Modify | 4: Comprehensive update |

## Build Order

**Phase 1 (P0) — Backend context + planner streaming:**
1. `forge/core/context.py` — snapshot gathering
2. `forge/core/daemon.py` — inject snapshot, planner streaming callback
3. `forge/core/planner.py` + `forge/core/claude_planner.py` — thread on_message
4. `forge/agents/adapter.py` — inject snapshot into agent system prompt
5. `forge/review/llm_review.py` — inject snapshot into reviewer prompt

**Phase 2 (P0) — Frontend planning visibility:**
6. `web/src/stores/taskStore.ts` — planner metadata fields
7. `web/src/components/task/PlannerCard.tsx` — new component
8. `web/src/app/tasks/view/page.tsx` — render PlannerCard during planning phase

**Phase 3 (P1) — Timeline rework:**
9. `web/src/components/task/AgentCard.tsx` — per-task activity section
10. `web/src/components/task/TaskDetailPanel.tsx` — full activity log
11. `web/src/app/tasks/view/page.tsx` — pipeline banner, remove TimelinePanel
12. Delete `web/src/components/task/TimelinePanel.tsx`

**Phase 4 (P1) — README:**
13. `README.md` — comprehensive update

## Verification

```bash
# 1. Existing tests pass
pytest forge/ -q

# 2. Snapshot gathering works
python -c "from forge.core.context import gather_project_snapshot; s = gather_project_snapshot('.'); print(s)"

# 3. E2E: Planning shows streaming output in Web UI
forge serve
# → Create pipeline → Watch PlannerCard stream activity

# 4. E2E: Agent cards show per-task activity
# → Execute pipeline → Verify activity sections in each card

# 5. README accuracy check
# → Verify test count, architecture diagram, new sections
```

## NOT in Scope

- **Rate limiting / concurrency management** — Known issue (4 opus agents get throttled). Separate design needed for adaptive concurrency.
- **"0 Files Changed" bug** — CompletionSummary shows 0 even when tasks changed files. Separate bugfix.
- **Project snapshot caching across pipelines** — Snapshot is per-pipeline. Cross-pipeline caching (invalidation via file watchers) is future work.
- **Non-Python file tree** — Snapshot gathers all tracked files, but LOC counting focuses on common languages. Exotic languages may not get accurate counts.
