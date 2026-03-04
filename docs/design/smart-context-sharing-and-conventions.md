# Design: Smart Context Sharing & Persistent Project Conventions

**Feature:** Planner-extracted conventions, `.forge/conventions.md`, inter-agent context sharing, shared file registry
**Status:** Draft
**Date:** 2026-03-04

---

## 1. Overview

Currently, each agent starts with a `ProjectSnapshot` (file tree, module index, LOC stats) but has **zero knowledge** of:

- Project coding conventions (naming, styling, import patterns)
- What other agents in the same pipeline have already built
- Which files were created/modified by completed dependencies

This leads to agents reinventing conventions (e.g., one agent uses `camelCase`, another uses `snake_case`) and lacking awareness of interfaces created by sibling tasks.

This design introduces four mechanisms:

1. **Planner-extracted conventions** — the planner outputs a structured conventions block alongside the TaskGraph
2. **`.forge/conventions.md`** — a persistent, user-editable file that seeds all future pipelines
3. **Inter-agent implementation summaries** — completed agents leave a brief summary for dependent agents
4. **Shared file registry** — dependent agents receive an exact list of files modified by their prerequisites

---

## 2. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Pipeline Start                               │
│                                                                     │
│  .forge/conventions.md ──► read at startup                          │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────┐                   │
│  │              PLANNER                         │                   │
│  │  - Explores codebase (Read, Glob, Grep)      │                   │
│  │  - Outputs TaskGraph JSON                    │                   │
│  │  - Outputs conventions JSON (NEW)            │                   │
│  └──────────┬──────────────────────┬────────────┘                   │
│             │                      │                                │
│        TaskGraph              conventions                           │
│             │                      │                                │
│             ▼                      ▼                                │
│     PipelineRow.task_graph_json   PipelineRow.conventions_json (NEW)│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    AGENT PROMPT ASSEMBLY                    │    │
│  │                                                             │    │
│  │  Base system prompt (AGENT_SYSTEM_PROMPT_TEMPLATE)          │    │
│  │    + ProjectSnapshot.format_for_agent()                     │    │
│  │    + conventions_json  ◄── from PipelineRow (NEW)           │    │
│  │    + conventions.md    ◄── from .forge/conventions.md (NEW) │    │
│  │    + dependency summaries ◄── from completed TaskRows (NEW) │    │
│  │    + files modified     ◄── from completed TaskRows (NEW)   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  ┌──────────────┐        ┌──────────────┐        ┌──────────────┐  │
│  │   Agent 1    │───────►│   Agent 2    │───────►│   Agent 3    │  │
│  │  (no deps)   │        │ (depends: 1) │        │ (depends: 2) │  │
│  └──────┬───────┘        └──────┬───────┘        └──────────────┘  │
│         │                       │                                   │
│    on completion:          receives:                                │
│    - impl_summary ──►      - Agent 1's summary                     │
│    - files_changed ──►     - Agent 1's files list                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │               POST-PIPELINE (if auto_update=True)           │    │
│  │  Merge planner conventions into .forge/conventions.md       │    │
│  └─────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. DB Schema Changes

### 3.1 PipelineRow — new column

```python
# forge/storage/db.py — PipelineRow

conventions_json: Mapped[str | None] = mapped_column(
    String, nullable=True, default=None
)
```

Stores the JSON string of planner-extracted conventions for the pipeline. Populated after planning completes, before agent execution begins.

### 3.2 TaskRow — new column

```python
# forge/storage/db.py — TaskRow

implementation_summary: Mapped[str | None] = mapped_column(
    String, nullable=True, default=None
)
```

Stores a brief (≤300 char) summary of what the agent actually implemented. Populated when the task reaches `DONE` state. Read by dependent tasks at agent prompt assembly time.

### 3.3 Migration

Alembic migration adds both nullable columns with `ALTER TABLE`:

```sql
ALTER TABLE pipelines ADD COLUMN conventions_json TEXT;
ALTER TABLE tasks ADD COLUMN implementation_summary TEXT;
```

No data migration needed — existing rows get `NULL` and the code treats `None` as "no conventions/summary available."

---

## 4. Planner Changes

### 4.1 Modified PLANNER_SYSTEM_PROMPT

The planner system prompt (in `forge/core/claude_planner.py`) is extended to request a `conventions` block in the JSON output:

```python
PLANNER_SYSTEM_PROMPT = """You are a task decomposition engine for a multi-agent coding system called Forge.

Given a user request and project context, produce a plan as valid JSON with this exact schema:

{
  "conventions": {
    "styling": "how styles are handled (CSS modules, Tailwind, etc.)",
    "state_management": "state management approach if applicable",
    "component_patterns": "UI component patterns if applicable",
    "naming": "naming conventions (camelCase, snake_case, etc.)",
    "testing": "testing framework and patterns",
    "imports": "import style (absolute, relative, aliases)",
    "error_handling": "error handling patterns if notable",
    "other": "any other project-specific conventions worth noting"
  },
  "tasks": [
    {
      "id": "task-1",
      "title": "Short title",
      "description": "What to do",
      "files": ["src/file.py"],
      "depends_on": [],
      "complexity": "low"
    }
  ]
}

Rules:
- The "conventions" object captures the coding patterns you observe in the existing codebase.
  Only include keys where you found clear evidence. Omit keys where the convention is unclear.
  These conventions will be forwarded to every coding agent so they write consistent code.
- Each task must own specific files. No two tasks may own the same file.
- Use depends_on to express ordering (task-2 depends on task-1 if task-2 needs task-1's output).
- complexity is one of: "low", "medium", "high"
- Keep tasks focused: each task should do ONE thing well.
- Aim for 2-6 tasks. Only go higher for genuinely large features.
- MINIMIZE dependencies. Only add depends_on when a task genuinely needs another task's output files. Independent tasks should have empty depends_on so they run in parallel.
- Never make test tasks depend on implementation tasks — tests should be self-contained with mocks.
- If the user request mentions attached images (file paths), you MUST read them first with the Read tool before planning. Include the image paths in relevant task descriptions so agents can also read them.
- Output ONLY valid JSON. No markdown fences, no explanation, just the JSON object."""
```

### 4.2 conventions.md injection into planner

If `.forge/conventions.md` exists, its contents are appended to the planner's user prompt so the planner is aware of user-defined conventions and can incorporate them (or refine them):

```python
# forge/core/claude_planner.py — ClaudePlannerLLM._build_prompt()

def _build_prompt(
    self, user_input: str, context: str, feedback: str | None,
) -> str:
    parts = [f"User request: {user_input}"]
    if context:
        parts.append(f"Project context:\n{context}")

    # NEW: inject persistent conventions file if it exists
    conventions_path = os.path.join(self._cwd or ".", ".forge", "conventions.md")
    if os.path.isfile(conventions_path):
        with open(conventions_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            parts.append(
                f"Existing project conventions (from .forge/conventions.md):\n{content}"
            )

    if feedback:
        parts.append(f"Previous attempt feedback:\n{feedback}")
    parts.append("Respond with ONLY the TaskGraph JSON.")
    return "\n\n".join(parts)
```

### 4.3 Parsing conventions from planner output

The planner's JSON now contains both `tasks` and `conventions`. The extraction happens in the daemon after `generate_plan()` returns:

```python
# forge/core/daemon.py — after plan generation

import json

raw_json = await planner.generate_plan(user_input, context)
parsed = json.loads(raw_json)

task_graph = parsed["tasks"]
conventions = parsed.get("conventions")  # May be None

# Store conventions in DB
if conventions:
    await db.update_pipeline_conventions(
        pipeline_id, json.dumps(conventions)
    )
```

The `_extract_json()` function in `claude_planner.py` already handles extracting the top-level JSON object, so no change needed there — the returned JSON will now just be a larger object with both keys.

---

## 5. `.forge/conventions.md` File Specification

### 5.1 Format

A plain Markdown file at `{project_root}/.forge/conventions.md`:

```markdown
# Project Conventions

## Styling
CSS variables defined in globals.css. Tailwind v4 utility classes.

## State Management
Zustand stores in /stores/ directory. One store per domain.

## Component Patterns
React functional components only. 'use client' directive for client components.

## Naming
- TypeScript/JavaScript: camelCase for variables/functions, PascalCase for components
- Python: snake_case for variables/functions, PascalCase for classes

## Testing
- Backend: pytest with fixtures in conftest.py
- Frontend: no test framework configured yet

## Imports
- Next.js: absolute imports with @/ prefix
- Python: relative imports within packages, absolute for cross-package

## Notes
- Always use conventional commits (feat/fix/refactor/test/docs/chore)
- Keep components under 200 lines
```

### 5.2 User workflow

- Created manually by the user, or auto-generated on first pipeline run
- Users can edit freely — their edits are preserved across pipeline runs
- Located in `.forge/` which is already `.gitignore`d by convention (project-local state)
- If the user wants to share conventions across the team, they can move it to a tracked location and symlink

### 5.3 Auto-update strategy (FORGE_AUTO_UPDATE_CONVENTIONS=True)

After a successful pipeline completes, the daemon merges planner-extracted conventions into the file:

```python
# forge/core/conventions.py (NEW FILE)

import json
import os

def update_conventions_file(
    project_dir: str,
    planner_conventions: dict,
) -> None:
    """Append newly discovered conventions to .forge/conventions.md.

    Merge strategy:
    - Read existing file content
    - For each key in planner_conventions, check if a corresponding
      ## heading already exists in the file
    - Only append sections for NEW keys not already present
    - Never modify or delete existing content
    - Append new discoveries under a "## Auto-discovered" heading
      with a timestamp so the user can review
    """
    conventions_path = os.path.join(project_dir, ".forge", "conventions.md")

    existing_content = ""
    if os.path.isfile(conventions_path):
        with open(conventions_path, "r", encoding="utf-8") as f:
            existing_content = f.read()

    existing_lower = existing_content.lower()

    # Map convention keys to markdown headings
    key_to_heading = {
        "styling": "styling",
        "state_management": "state management",
        "component_patterns": "component patterns",
        "naming": "naming",
        "testing": "testing",
        "imports": "imports",
        "error_handling": "error handling",
        "other": "notes",
    }

    new_sections = []
    for key, value in planner_conventions.items():
        if not value:
            continue
        heading = key_to_heading.get(key, key.replace("_", " "))
        # Skip if heading already exists in file
        if f"## {heading}" in existing_lower:
            continue
        new_sections.append(f"## {heading.title()}\n{value}\n")

    if not new_sections:
        return  # Nothing new to add

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    appendix = (
        f"\n\n---\n_Auto-discovered by Forge planner on {timestamp}:_\n\n"
        + "\n".join(new_sections)
    )

    if not existing_content:
        # Create file with header
        content = "# Project Conventions\n" + appendix
    else:
        content = existing_content.rstrip() + appendix

    os.makedirs(os.path.dirname(conventions_path), exist_ok=True)
    with open(conventions_path, "w", encoding="utf-8") as f:
        f.write(content)
```

---

## 6. Agent System Prompt Modifications

### 6.1 Modified AGENT_SYSTEM_PROMPT_TEMPLATE

```python
# forge/agents/adapter.py

AGENT_SYSTEM_PROMPT_TEMPLATE = """You are a coding agent working on a specific task within the Forge orchestration system.

Your working directory is {cwd}. Do NOT read, write, or execute anything outside this directory{extra_dirs_clause}.

You have access to a git worktree isolated to your task. Write clean, tested code.

{project_context}

{conventions_block}

{dependency_context}

Rules:
- Only modify files listed in your task specification
- Follow existing code style and patterns — see the conventions section above
- Write tests for any new functionality
- Commit your changes with a SHORT conventional commit message (max 72 chars) — use feat/fix/refactor/test/docs/chore prefix and describe WHAT changed, not the task title
- If you encounter an error, fix it rather than giving up
- If image file paths are mentioned in the task description, use the Read tool to view them (images are readable)"""
```

### 6.2 Conventions block construction

```python
# forge/agents/adapter.py — new helper

def _build_conventions_block(
    conventions_json: str | None,
    conventions_md: str | None,
) -> str:
    """Build the conventions section for the agent system prompt.

    Args:
        conventions_json: Planner-extracted conventions (JSON string from DB).
        conventions_md: Contents of .forge/conventions.md file.

    Returns:
        Formatted conventions block, or empty string if none available.
    """
    parts = []

    if conventions_json:
        try:
            conventions = json.loads(conventions_json)
            lines = ["## Project Conventions (from planner analysis)"]
            for key, value in conventions.items():
                heading = key.replace("_", " ").title()
                lines.append(f"- **{heading}:** {value}")
            parts.append("\n".join(lines))
        except (json.JSONDecodeError, TypeError):
            pass

    if conventions_md:
        parts.append(
            "## Project Conventions (from .forge/conventions.md)\n"
            + conventions_md
        )

    return "\n\n".join(parts)
```

### 6.3 Dependency context block construction

```python
# forge/agents/adapter.py — new helper

def _build_dependency_context(
    completed_deps: list[dict],
) -> str:
    """Build the dependency context section for the agent system prompt.

    Args:
        completed_deps: List of dicts with keys:
            - task_id: str
            - title: str
            - implementation_summary: str | None
            - files_changed: list[str]

    Returns:
        Formatted dependency context, or empty string if no deps.
    """
    if not completed_deps:
        return ""

    lines = ["## Completed Dependencies"]
    lines.append(
        "The following tasks were completed by other agents before you. "
        "Their changes are already merged into your worktree.\n"
    )

    for dep in completed_deps:
        lines.append(f"### Task: {dep['title']} ({dep['task_id']})")

        if dep.get("implementation_summary"):
            lines.append(f"**What was done:** {dep['implementation_summary']}")

        files = dep.get("files_changed", [])
        if files:
            lines.append("**Files modified:**")
            for f in files:
                lines.append(f"  - `{f}`")

        lines.append("")  # blank line between deps

    return "\n".join(lines)
```

### 6.4 Wiring into ClaudeAdapter._build_options

```python
# forge/agents/adapter.py — ClaudeAdapter._build_options (modified signature)

def _build_options(
    self,
    worktree_path: str,
    allowed_dirs: list[str],
    model: str = "sonnet",
    project_context: str = "",
    conventions_json: str | None = None,
    conventions_md: str | None = None,
    completed_deps: list[dict] | None = None,
) -> ClaudeCodeOptions:
    # ... existing extra_dirs_clause logic ...

    conventions_block = _build_conventions_block(conventions_json, conventions_md)
    dependency_context = _build_dependency_context(completed_deps or [])

    system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
        cwd=worktree_path,
        extra_dirs_clause=extra_dirs_clause,
        project_context=project_context,
        conventions_block=conventions_block,
        dependency_context=dependency_context,
    )
    # ... rest unchanged ...
```

---

## 7. Implementation Summary Extraction

### 7.1 Strategy

When an agent completes successfully and its task reaches `DONE` state, extract an implementation summary. Two sources, in priority order:

1. **Commit message** — the agent's git commit message is the most reliable signal of what was done. Extract it from the worktree before cleanup.
2. **Agent result text** — `AgentResult.summary` (already capped at 500 chars) serves as fallback.

```python
# forge/core/daemon_helpers.py — new function

def _extract_implementation_summary(
    worktree_path: str,
    agent_summary: str,
) -> str:
    """Extract a brief implementation summary from a completed agent's work.

    Combines the git commit message (what was done) with the agent's
    final summary (any additional context). Returns ≤300 chars.

    Args:
        worktree_path: Path to the agent's worktree (pre-cleanup).
        agent_summary: The AgentResult.summary text.

    Returns:
        A concise summary string.
    """
    # Get commit message(s) from this worktree
    result = subprocess.run(
        ["git", "log", "--format=%s", "-3"],  # last 3 commit subjects
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    commit_msgs = result.stdout.strip() if result.returncode == 0 else ""

    parts = []
    if commit_msgs:
        parts.append(f"Commits: {commit_msgs}")
    if agent_summary and agent_summary != "Task completed":
        # Trim agent summary to leave room
        parts.append(agent_summary[:200])

    summary = " | ".join(parts)
    return summary[:300] if summary else "Task completed (no details captured)"
```

### 7.2 When to call it

In `ExecutorMixin._emit_merge_success()`, after the task is marked `DONE` but before worktree cleanup:

```python
# forge/core/daemon_executor.py — _emit_merge_success (modified)

async def _emit_merge_success(self, db, task_id, pid, worktree_path, **kwargs):
    # ... existing logic ...
    await db.update_task_state(task_id, TaskState.DONE.value)

    # NEW: extract and store implementation summary
    task = await db.get_task(task_id)
    summary = _extract_implementation_summary(
        worktree_path, getattr(task, "summary", "") or ""
    )
    await db.update_task_implementation_summary(task_id, summary)

    # ... existing stats + emit logic ...
```

### 7.3 Feeding summaries to dependent agents

In `ExecutorMixin._stream_agent()`, before calling `runtime.run_task()`, look up completed dependency tasks:

```python
# forge/core/daemon_executor.py — _run_agent (modified)

async def _run_agent(self, db, runtime, worktree_mgr, task, task_id, ...):
    # ... existing setup ...

    # NEW: gather dependency context
    completed_deps = []
    for dep_id in (task.depends_on or []):
        dep_task = await db.get_task(dep_id)
        if dep_task and dep_task.state == TaskState.DONE.value:
            completed_deps.append({
                "task_id": dep_id,
                "title": dep_task.title,
                "implementation_summary": getattr(dep_task, "implementation_summary", None),
                "files_changed": dep_task.files,  # files from task spec
            })

    # NEW: load conventions
    pipeline = await db.get_pipeline(pid) if pid else None
    conventions_json = getattr(pipeline, "conventions_json", None) if pipeline else None
    conventions_md = _load_conventions_md(self._project_dir)

    result = await runtime.run_task(
        agent_id, prompt, worktree_path, task.files,
        allowed_dirs=self._settings.allowed_dirs,
        model=agent_model,
        on_message=_on_msg,
        project_context=self._snapshot.format_for_agent() if self._snapshot else "",
        conventions_json=conventions_json,        # NEW
        conventions_md=conventions_md,            # NEW
        completed_deps=completed_deps,            # NEW
    )
    # ...
```

Where `_load_conventions_md` is a simple file reader:

```python
def _load_conventions_md(project_dir: str) -> str | None:
    """Load .forge/conventions.md if it exists."""
    path = os.path.join(project_dir, ".forge", "conventions.md")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    return None
```

---

## 8. Settings Additions

```python
# forge/config/settings.py — ForgeSettings

class ForgeSettings(BaseSettings):
    # ... existing fields ...

    # Conventions
    auto_update_conventions: bool = False  # env: FORGE_AUTO_UPDATE_CONVENTIONS
```

When `True`, after a successful pipeline completes, the daemon calls `update_conventions_file()` with the planner-extracted conventions.

---

## 9. DB Helper Methods

New methods on the `ForgeDB` class:

```python
# forge/storage/db.py

async def update_pipeline_conventions(
    self, pipeline_id: str, conventions_json: str
) -> None:
    """Store planner-extracted conventions JSON on a pipeline."""
    async with self._session() as session:
        pipeline = await session.get(PipelineRow, pipeline_id)
        if pipeline:
            pipeline.conventions_json = conventions_json
            await session.commit()

async def update_task_implementation_summary(
    self, task_id: str, summary: str
) -> None:
    """Store an implementation summary on a completed task."""
    async with self._session() as session:
        task = await session.get(TaskRow, task_id)
        if task:
            task.implementation_summary = summary
            await session.commit()
```

---

## 10. AgentRuntime Interface Changes

The `AgentAdapter.run()` method and `AgentRuntime.run_task()` need new optional parameters threaded through:

```python
# forge/agents/adapter.py — AgentAdapter.run() signature

async def run(
    self,
    task_prompt: str,
    worktree_path: str,
    allowed_files: list[str],
    timeout_seconds: int,
    allowed_dirs: list[str] | None = None,
    model: str = "sonnet",
    on_message: Callable | None = None,
    project_context: str = "",
    conventions_json: str | None = None,        # NEW
    conventions_md: str | None = None,           # NEW
    completed_deps: list[dict] | None = None,    # NEW
) -> AgentResult:
```

The `AgentRuntime.run_task()` method (which wraps the adapter) passes these through to `ClaudeAdapter._build_options()`.

---

## 11. Example: Full Agent System Prompt (Assembled)

For a task `task-2` that depends on `task-1`:

```
You are a coding agent working on a specific task within the Forge orchestration system.

Your working directory is /project/.forge/worktrees/task-2. Do NOT read, write, or execute
anything outside this directory.

You have access to a git worktree isolated to your task. Write clean, tested code.

## Project Snapshot

**Branch:** forge/pipeline-abc123
**Files:** 142 | **LOC:** 8503

### File Tree
src/
  components/
    Button.tsx
    ...

## Project Conventions (from planner analysis)
- **Styling:** Tailwind v4 utility classes, CSS variables in globals.css
- **State Management:** Zustand stores in /stores/
- **Naming:** camelCase for TS, snake_case for Python
- **Testing:** pytest for Python, no frontend tests yet
- **Imports:** absolute imports with @/ prefix for Next.js

## Project Conventions (from .forge/conventions.md)
# Project Conventions

## Component Patterns
React functional components only. 'use client' directive for client components.
Keep components under 200 lines.

## Completed Dependencies

The following tasks were completed by other agents before you.
Their changes are already merged into your worktree.

### Task: Add user authentication API (task-1)
**What was done:** Commits: feat: add JWT auth endpoints with refresh tokens | Created /api/auth/login, /api/auth/register, /api/auth/refresh endpoints using jose library. Tokens stored in httpOnly cookies.
**Files modified:**
  - `src/app/api/auth/login/route.ts`
  - `src/app/api/auth/register/route.ts`
  - `src/app/api/auth/refresh/route.ts`
  - `src/lib/auth.ts`
  - `src/stores/authStore.ts`

Rules:
- Only modify files listed in your task specification
- Follow existing code style and patterns — see the conventions section above
- Write tests for any new functionality
- Commit your changes with a SHORT conventional commit message (max 72 chars)
- If you encounter an error, fix it rather than giving up
```

---

## 12. Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `forge/storage/db.py` | Modify | Add `conventions_json` to PipelineRow, `implementation_summary` to TaskRow, add DB helper methods |
| `forge/core/claude_planner.py` | Modify | Update `PLANNER_SYSTEM_PROMPT` to request conventions, inject `conventions.md` into planner prompt |
| `forge/agents/adapter.py` | Modify | Add `_build_conventions_block()`, `_build_dependency_context()`, update template and `_build_options()` signature |
| `forge/core/daemon_executor.py` | Modify | Wire conventions + dependency context into `_run_agent()`, extract summary in `_emit_merge_success()` |
| `forge/core/daemon_helpers.py` | Modify | Add `_extract_implementation_summary()` |
| `forge/core/conventions.py` | Create | `update_conventions_file()` for auto-update merge strategy |
| `forge/config/settings.py` | Modify | Add `auto_update_conventions: bool` |
| `forge/core/daemon.py` | Modify | Call `update_conventions_file()` post-pipeline if setting enabled, store conventions after planning |

---

## 13. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Planner omits conventions block from JSON | Treat `"conventions"` as optional — `parsed.get("conventions")` returns `None`, code proceeds without it. TaskGraph extraction (`parsed["tasks"]`) unchanged. |
| conventions.md grows unboundedly | Auto-update only appends new headings. Cap file size at 10KB — if exceeded, skip auto-update and log a warning. |
| Implementation summary too vague | Use commit messages as primary source (agents are instructed to write descriptive commits). Fallback to agent result text. |
| Prompt size bloat from conventions + deps | Conventions block is ~200-400 tokens. Each dependency summary is ~100-150 tokens. With max 6 tasks, worst case adds ~1000 tokens — well within claude-code-sdk context. |
| Planner conventions conflict with user conventions.md | Planner sees conventions.md content and should align. Both are injected into agent prompt — agent sees both and can reconcile. User conventions.md takes precedence (listed second = seen later). |
