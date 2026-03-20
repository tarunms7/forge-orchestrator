# Multi-Repo Workspace Support

**Date:** 2026-03-21
**Status:** Design
**Principle:** If we build it, it works. No excuses.

---

## 1. Problem Statement

Forge currently assumes one pipeline operates on one git repository. Many teams work with multiple repos (frontend, backend, infra, shared libs). They cannot use Forge for cross-repo features like "add user auth with backend API + frontend login page + shared types."

## 2. Design Invariant

**Every task belongs to exactly one repo.** No task ever writes to two repos. An agent can *read* all repos (via `allowed_dirs`) but *writes* only in its assigned repo's worktree. This is non-negotiable — git does not support atomic cross-repo commits, so any design that tries to span repos in a single task is fundamentally broken.

## 3. Architecture Overview

A **workspace** is a directory that references one or more git repos. Forge already operates on a "workspace" — today it's just a workspace with one repo.

```
workspace/                          # CWD when running forge
  .forge/                           # workspace-level state (created by forge)
    forge.toml                      # workspace config (repos, settings)
    worktrees/
      backend/                      # per-repo worktree root
        task-abc-1/                 # worktree for task-abc-1 in backend
        task-abc-2/
      frontend/
        task-abc-3/                 # worktree for task-abc-3 in frontend
  backend/                          # repo A (or symlink/path reference)
    .forge/forge.toml               # repo-specific config (lint, test, build)
    .git/
  frontend/                         # repo B
    .forge/forge.toml
    .git/
```

**Single-repo backward compatibility**: When no `--repo` flags are given, Forge behaves exactly as today. Internally: `repos = [{"id": "default", "path": ".", "base_branch": <auto-detected>}]`. The `.forge/` directory lives inside the repo (current behavior). No migration, no config changes, no surprises.

---

## 4. Data Model Changes

### 4.1 TaskDefinition (forge/core/models.py)

```python
class TaskDefinition(BaseModel):
    id: str
    title: str
    description: str
    files: list[str]                    # relative paths within the repo
    depends_on: list[str] = []
    complexity: Complexity = "medium"
    repo: str = "default"               # NEW — which repo this task operates in
```

**Validation rules:**
- `repo` must match a known repo ID from the pipeline's `repos` list
- `files` are relative to the repo root, never absolute, never cross-repo
- Planner validation (`validate_plan`) rejects tasks with unknown `repo` values

**Propagation**: The `repo` field must also be added to:
- `TaskRecord` (runtime projection in `models.py`) — set via `TaskRecord.from_definition()`
- `TaskRow` (database, Section 4.2) — stored as `repo_id` column
- `TaskRecord.from_definition()` must copy `definition.repo → record.repo`

### 4.2 TaskRow (forge/storage/db.py)

```python
repo_id: Mapped[str] = mapped_column(String, default="default")
```

Added via the existing `_add_missing_columns()` auto-migration. Existing tasks get `"default"`. No manual migration needed.

### 4.3 PipelineRow (forge/storage/db.py)

```python
repos_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
```

JSON-serialized list of repo configs:
```json
[
  {"id": "backend", "path": "/abs/path/to/backend", "base_branch": "main"},
  {"id": "frontend", "path": "/abs/path/to/frontend", "base_branch": "main"}
]
```

**For single-repo pipelines**: `repos_json` is `null`. The existing `project_dir` column is the sole source of truth. Zero ambiguity.

**For multi-repo pipelines**: `repos_json` is set. `project_dir` stores the workspace root (where `.forge/` lives). Each entry in `repos_json` also stores the pipeline branch name for that repo (set during `execute()`). Helper method:

```python
def get_repos(self) -> list[dict]:
    if self.repos_json:
        return json.loads(self.repos_json)
    # Single-repo fallback — use existing columns.
    # base_branch is always populated by set_pipeline_base_branch()
    # during execute(). If somehow None, raise rather than guess.
    if not self.base_branch:
        raise ValueError(
            f"Pipeline {self.id} has no base_branch set. "
            "This should have been set during execute()."
        )
    return [{"id": "default", "path": self.project_dir,
             "base_branch": self.base_branch,
             "branch_name": self.branch_name or ""}]
```

**`repos_json` extended schema** (set during `execute()`):
```json
[
  {"id": "backend", "path": "/abs/path", "base_branch": "main",
   "branch_name": "forge/pipeline-abc123", "pr_url": "https://..."},
  {"id": "frontend", "path": "/abs/path", "base_branch": "main",
   "branch_name": "forge/pipeline-abc123", "pr_url": "https://..."}
]
```

This eliminates the need for separate `pr_urls_json` or `pipeline_branches_json` columns. **Authoritative source rule:**
- **Single-repo**: `repos_json` is `null`. `PipelineRow.branch_name` and `PipelineRow.pr_url` are authoritative.
- **Multi-repo**: `repos_json` is authoritative for all per-repo values. The top-level `branch_name` and `pr_url` columns store the first repo's values as a convenience for backward-compatible API responses, but code should always read from `get_repos()` which abstracts this.

### 4.4 RepoConfig (new dataclass, forge/core/models.py)

```python
@dataclass(frozen=True)
class RepoConfig:
    id: str              # unique identifier (e.g., "backend", "frontend")
    path: str            # absolute path to the repo root
    base_branch: str     # default branch for this repo (e.g., "main", "develop")
```

---

## 5. CLI Interface

### 5.1 Single-repo (unchanged)

```bash
forge run "add auth"
# CWD must be a git repo
# Equivalent to: --repo default=.
```

### 5.2 Multi-repo

```bash
forge run "add auth with login page" \
  --repo backend=./backend \
  --repo frontend=./frontend
```

**Parsing rules:**
- Format: `--repo <name>=<path>`
- `<name>`: alphanumeric + hyphens, no spaces, unique across all `--repo` flags
- `<path>`: relative or absolute path to a git repo
- At least one `--repo` required if any are specified (no mixing implicit + explicit)

### 5.3 Startup Validation (fail fast)

Before any planning or execution, validate ALL repos. Fail on the first error with a clear message:

| Check | Error message |
|-------|---------------|
| Path doesn't exist | `Error: repo 'frontend' path './frontend' does not exist` |
| Not a git repo | `Error: repo 'frontend' at './frontend' is not a git repository (no .git directory)` |
| Dirty working tree | `Error: repo 'frontend' has uncommitted changes. Commit or stash before running Forge.` |
| Duplicate repo IDs | `Error: duplicate repo ID 'backend' — each --repo must have a unique name` |
| Duplicate paths | `Error: repos 'backend' and 'api' both point to './backend' — each repo must be a distinct directory` |
| Nested repo paths | `Error: repo 'shared' at './backend/libs/shared' is inside repo 'backend' at './backend'. Nested repos are not supported — use separate directories.` |
| No commits | `Error: repo 'frontend' has no commits. Make an initial commit first.` |
| gh CLI missing | `Error: 'gh' CLI not found. Required for PR creation. Install from https://cli.github.com` |

**Nested path detection**: After resolving all paths to absolute, check every pair `(a, b)` — if `a.startswith(b + "/")` or vice versa, reject. This prevents worktree creation inside another repo's tree (which would confuse `git worktree list`) and prevents scope enforcement from misattributing files.

Every check is performed synchronously before any LLM calls. No wasted money on invalid setups.

### 5.4 TUI Mode

```bash
forge tui --repo backend=./backend --repo frontend=./frontend
```

Same validation. The TUI home screen shows which repos are configured.

### 5.5 Workspace Config File (optional, for convenience)

Users who repeatedly use the same repos can create a workspace config instead of passing `--repo` flags every time:

```toml
# .forge/workspace.toml (in workspace root)
[[repos]]
id = "backend"
path = "./backend"          # relative to workspace root
base_branch = "main"

[[repos]]
id = "frontend"
path = "./frontend"
base_branch = "main"

[[repos]]
id = "shared"
path = "./shared-types"
base_branch = "develop"
```

**Priority**: `--repo` CLI flags override `workspace.toml`. If both are absent, single-repo mode from CWD.

**Loading order:**
1. If `--repo` flags present → use those
2. Else if `.forge/workspace.toml` exists in CWD → use those
3. Else if CWD is a git repo → single-repo mode
4. Else → `Error: current directory is not a git repo and no --repo flags or workspace.toml found`

---

## 6. Daemon & Executor Changes

### 6.1 ForgeDaemon Initialization

```python
class ForgeDaemon:
    def __init__(
        self,
        project_dir: str,                      # workspace root (where .forge/ lives)
        settings: ForgeSettings | None = None,
        repos: list[RepoConfig] | None = None,  # NEW — None = single-repo mode
    ):
        self._workspace_dir = project_dir
        self._repos: dict[str, RepoConfig] = {}

        if repos:
            for rc in repos:
                self._repos[rc.id] = rc
        else:
            # Single-repo backward compat — base_branch resolved later in
            # async _init_repos() since _get_current_branch is async.
            # Stored as None here, resolved before execute().
            self._repos["default"] = RepoConfig(
                id="default",
                path=os.path.abspath(project_dir),
                base_branch="",  # placeholder — set in _init_repos()
            )

    async def _init_repos(self):
        """Resolve base branches (async). Called once before execute()."""
        for repo_id, rc in list(self._repos.items()):
            if not rc.base_branch:
                branch = await _get_current_branch(rc.path) or "main"
                self._repos[repo_id] = RepoConfig(
                    id=rc.id, path=rc.path, base_branch=branch,
                )

        # Backward compat: _project_dir points to first/only repo
        self._project_dir = next(iter(self._repos.values())).path
```

### 6.2 Per-Repo Infrastructure Registry

```python
# Created during execute(), one per repo
self._worktree_managers: dict[str, WorktreeManager] = {}
self._merge_workers: dict[str, MergeWorker] = {}
self._pipeline_branches: dict[str, str] = {}  # repo_id → branch name

for repo_id, rc in self._repos.items():
    if len(self._repos) == 1 and repo_id == "default":
        # Single-repo: flat layout (backward compat)
        worktrees_dir = os.path.join(self._workspace_dir, ".forge", "worktrees")
    else:
        # Multi-repo: nested under repo_id
        worktrees_dir = os.path.join(self._workspace_dir, ".forge", "worktrees", repo_id)
    os.makedirs(worktrees_dir, exist_ok=True)
    self._worktree_managers[repo_id] = WorktreeManager(rc.path, worktrees_dir)
    pipeline_branch = f"forge/pipeline-{pipeline_id[:12]}"
    self._merge_workers[repo_id] = MergeWorker(rc.path, main_branch=pipeline_branch)
    self._pipeline_branches[repo_id] = pipeline_branch

    # Create pipeline branch in each repo
    await _run_git(
        ["branch", "-f", pipeline_branch, rc.base_branch],
        cwd=rc.path, check=True,
        description=f"create pipeline branch in {repo_id}",
    )
```

### 6.3 Worktree Path Helper (migration requirement)

The current codebase hardcodes worktree paths in three places inside `daemon_executor.py`:

| Location | Current code | Used for |
|----------|-------------|----------|
| `daemon_executor.py:_handle_merge_fast_path` (line 176) | `os.path.join(self._project_dir, ".forge", "worktrees", task_id)` | Merge-only retry |
| `daemon_executor.py:_prepare_worktree` fallback (line 210) | `os.path.join(self._project_dir, ".forge", "worktrees", task_id)` | Worktree reuse on retry |
| `daemon_executor.py:_resume_after_answer` (line 587) | `os.path.join(self._project_dir, ".forge", "worktrees", task_id)` | Question-answer resume |
| `api/routes/tasks.py:_cleanup_worktree` (line 51) | `os.path.join(project_dir, ".forge", "worktrees")` | API-driven worktree cleanup |
| `core/followup.py` (line 318) | `os.path.join(project_dir, ".forge", "worktrees", worktree_id)` | Follow-up worktree path |

All three must go through a central helper:

```python
def _worktree_path(self, repo_id: str, task_id: str) -> str:
    """Canonical worktree path for a task in a repo."""
    if len(self._repos) == 1 and repo_id == "default":
        # Single-repo backward compat: flat layout
        return os.path.join(self._workspace_dir, ".forge", "worktrees", task_id)
    # Multi-repo: nested under repo_id
    return os.path.join(self._workspace_dir, ".forge", "worktrees", repo_id, task_id)
```

**Implementation rule**: Search for every `os.path.join(.*".forge".*"worktrees"` in the codebase during implementation. Every hit must be replaced with `_worktree_path()` or `WorktreeManager` (which already constructs paths correctly). No raw path construction allowed.

Additionally, `daemon.py` has related constructions:
- Line ~654: `WorktreeManager(self._project_dir, f"{self._project_dir}/.forge/worktrees")` — this is a `WorktreeManager` constructor that needs to become per-repo (replaced by `self._worktree_managers[repo_id]`).
- Line ~834 (`retry_task`): raw `os.path.join(self._project_dir, ".forge", "worktrees")` — needs `_worktree_path()` or per-repo manager lookup.

**Exempt**: `core/integration.py` (line 144) also has `os.path.join(project_dir, ".forge", "worktrees")` for health-check worktrees. These are ephemeral, repo-scoped (receive the correct `project_dir` for their repo), and use the flat layout correctly. No migration needed.

All sites above must be updated during implementation.

### 6.4 Task Dispatch

**Architecture note**: The executor mixin (`TaskExecutorMixin`) currently receives `worktree_mgr` and `merge_worker` from the daemon. The current signature is:

```python
# Current signature (daemon_executor.py):
async def _execute_task(self, db, runtime, worktree_mgr, merge_worker,
                        task_id: str, agent_id: str, pipeline_id: str | None = None):
```

The executor derives `pipeline_branch` from `merge_worker._main` internally — no signature change needed there because the `MergeWorker` is already constructed with the pipeline branch as its `main_branch`.

For multi-repo, the **daemon** looks up the correct per-repo manager/worker before calling the executor. The executor's existing signature stays the same — it receives one `worktree_mgr` and one `merge_worker` per call, just as today. The change is in the daemon's dispatch logic:

```python
# In ForgeDaemon._dispatch_task() (daemon.py) — BEFORE calling executor:
task_row = await db.get_task(task_id)
repo_id = task_row.repo_id or "default"

if repo_id not in self._repos:
    raise ForgeError(f"Task {task_id} references unknown repo '{repo_id}'")

worktree_mgr = self._worktree_managers[repo_id]
merge_worker = self._merge_workers[repo_id]
# pipeline_branch is already embedded in merge_worker._main

# Pass the correct per-repo manager/worker to the executor
# (same signature as today — daemon just selects the right pair)
await self._execute_task(
    db, runtime, worktree_mgr, merge_worker,
    task_id, agent_id, pipeline_id,
)
```

### 6.5 Allowed Dirs for Cross-Repo Reading

The agent adapter receives `allowed_dirs` which controls what the agent can read. For multi-repo:

```python
# In _stream_agent or _run_agent:
effective_allowed_dirs = list(self._settings.allowed_dirs or [])
for rc in self._repos.values():
    if rc.path not in effective_allowed_dirs:
        effective_allowed_dirs.append(rc.path)
```

**What the agent reads**: The `allowed_dirs` paths point to the **main repo checkout** (on its base branch), not other tasks' worktrees. This means:
- The agent reads the **committed, base-branch state** of other repos — not in-progress work from sibling tasks.
- Cross-repo context comes primarily through `implementation_summary` in `completed_deps` (Section 8.2), which reflects what a completed sibling task actually did.
- If a frontend task depends on a backend task, and the backend task is already merged to the pipeline branch, the frontend agent sees the merged code in the backend repo's pipeline branch checkout. This is correct — the pipeline branch is the integration branch.
- If the backend task is NOT yet merged (race condition prevented by `depends_on`), the frontend agent would see stale base-branch code. The `depends_on` mechanism prevents this by ensuring the backend task completes first.

The agent can `Read`, `Glob`, `Grep` across all repos. Write isolation is enforced by **two layers**:

1. **CWD scoping (preventive)**: The agent's CWD is the worktree. Claude Code's `Edit`/`Write` tools resolve relative paths against CWD, so the agent naturally writes within its worktree. However, absolute paths can escape this.
2. **`_enforce_file_scope()` (corrective)**: After the agent finishes, this function diffs `HEAD` vs the pipeline branch, identifies files outside `task.files`, and reverts them via `git checkout`. This catches any writes the agent made outside its assigned files — whether via absolute paths, symlink traversal, or tool misconfiguration.

The corrective layer is the **actual enforcement**. CWD scoping is a convenience that reduces noise, not a security boundary. This is important because `allowed_dirs` grants read access to all repos, and an agent could theoretically construct absolute paths to other repos' worktrees.

### 6.6 Write Isolation & Scope Enforcement for Multi-Repo

Existing `_enforce_file_scope()` works unchanged because:
- `task.files` are relative paths (e.g., `src/api/auth.py`)
- The worktree is the task's repo, so relative paths resolve correctly
- The diff is computed within the worktree, so only that repo's changes are visible
- An agent writing to another repo's worktree would require knowing the absolute path AND that path would show up in `git diff` of the current worktree — it wouldn't, because it's a different git repo. Cross-repo writes silently fail (the other worktree is a different git working tree).

**No changes needed** to `_enforce_file_scope()` for multi-repo support.

### 6.7 Merge Lock

Currently: one `self._merge_lock` per daemon (serializes all merges).

For multi-repo: **keep a single lock.** Merges to different repos don't conflict in git, but the lock prevents race conditions in the event emission and DB update path. The overhead of serializing merges across repos is negligible (merges take <1 second; the bottleneck is agent execution at 30-120 seconds per task).

---

## 7. Planner Changes

### 7.1 Multi-Repo Snapshot

```python
# In daemon.py, during plan():
snapshots: dict[str, ProjectSnapshot] = {}
for repo_id, rc in self._repos.items():
    snapshots[repo_id] = gather_project_snapshot(rc.path)

# Format for planner
if len(self._repos) == 1:
    # Single-repo: exact current format (backward compat)
    snapshot_text = next(iter(snapshots.values())).format_for_planner()
else:
    # Multi-repo: labeled sections
    parts = []
    for repo_id, snap in snapshots.items():
        rc = self._repos[repo_id]
        parts.append(f"### Repo: {repo_id} ({rc.path})")
        parts.append(snap.format_for_planner())
        parts.append("")
    snapshot_text = "\n".join(parts)
```

### 7.2 Planner System Prompt Addition

When multi-repo, append to the system prompt:

```
## Multi-Repo Workspace

This workspace contains multiple repositories. Each task you create MUST include
a "repo" field specifying which repository it belongs to.

Available repos:
- "backend" — Python FastAPI backend
- "frontend" — Next.js frontend

Rules:
1. Every task MUST have a "repo" field matching one of the repo IDs above.
2. Task "files" are RELATIVE to the repo root (e.g., "src/api.py", not "backend/src/api.py").
3. A task can only modify files in its assigned repo.
4. If a frontend task needs a backend API, create the backend task first and add
   it to the frontend task's "depends_on" list.
5. Agents CAN read all repos (they have read access), but can only write in their
   assigned repo. Use depends_on to sequence cross-repo work.

Output schema with repo field:
{
  "tasks": [
    {
      "id": "task-1",
      "title": "Add /users API endpoint",
      "description": "...",
      "files": ["src/routes/users.py", "tests/test_users.py"],
      "repo": "backend",
      "depends_on": []
    },
    {
      "id": "task-2",
      "title": "Add login page",
      "description": "...",
      "files": ["src/pages/login.tsx", "src/api/client.ts"],
      "repo": "frontend",
      "depends_on": ["task-1"]
    }
  ]
}
```

### 7.3 Planner Validation

After parsing the TaskGraph, validate repo assignments:

```python
# In unified_planner.py, after _parse():
if self._repo_ids:  # set of valid repo IDs, None for single-repo
    for task in graph.tasks:
        if not task.repo:
            task.repo = "default"  # fallback for single-repo compat
        if task.repo not in self._repo_ids:
            return None, (
                f"Task '{task.id}' has repo='{task.repo}' but valid repos are: "
                f"{', '.join(sorted(self._repo_ids))}. "
                f"Fix the repo field for this task."
            )
```

If validation fails, the planner retries with the error as feedback (existing retry mechanism). Max 3 retries before giving up.

### 7.4 Planner CWD

For multi-repo, the planner's `cwd` is set to the **workspace root** (not any individual repo). This lets the planner use `Read`, `Glob`, `Grep` across all repos by specifying paths like `backend/src/models.py` or `frontend/src/components/`.

```python
options = ClaudeCodeOptions(
    cwd=self._workspace_dir,  # workspace root, not repo root
    ...
)
```

---

## 8. Cross-Repo Dependencies

### 8.1 How It Works (no changes needed)

The existing DAG scheduler handles cross-repo dependencies natively:

```
task-1 (backend, no deps) ──→ starts immediately
task-2 (frontend, depends_on: [task-1]) ──→ waits for task-1

When task-1 completes:
  - implementation_summary stored in DB
  - task-2 becomes ready for dispatch
  - task-2's agent receives task-1's summary via completed_deps
```

### 8.2 Cross-Repo Context Passing

When a frontend task depends on a backend task, the frontend agent needs to know what the backend agent built. The existing `completed_deps` mechanism (daemon_executor.py line 1090-1099) already handles this:

```python
# For each completed dependency:
completed_deps.append({
    "task_id": dep_task.id,
    "title": dep_task.title,
    "implementation_summary": dep_task.implementation_summary,
    "files_changed": dep_task.files or [],
})
```

The frontend agent also has **read access to the backend repo** (via `allowed_dirs`), so it can inspect the actual code the backend agent wrote. Combined with the summary, this gives the frontend agent full context.

### 8.3 Cross-Repo Dependency Failure

If task-1 (backend) fails after max retries:
1. task-1 marked as `ERROR`
2. task-2 (frontend, depends on task-1) transitions to `BLOCKED`
3. task-2 is NOT executed, NOT retried
4. Pipeline continues with other independent tasks
5. Final PR excludes both failed and blocked tasks
6. PR body shows failed + blocked tasks in separate sections

This is existing behavior — no changes needed.

---

## 9. PR Creation

### 9.1 Per-Repo PR Flow

After all tasks complete:

```python
# In on_final_approval_screen_create_pr (app.py):

# 1. Group completed tasks by repo
tasks_by_repo: dict[str, list[dict]] = {}
for t in task_summaries:
    repo_id = t.get("repo_id", "default")
    tasks_by_repo.setdefault(repo_id, []).append(t)

# 1b. Compute per-repo cost from task-level cost tracking.
# TaskRow already has cost_usd (agent_cost_usd + review_cost_usd).
# Sum per repo. Planner cost is NOT attributed to any repo —
# it's shown as a separate line or split evenly.
cost_per_repo: dict[str, float] = {}
for t in task_summaries:
    repo_id = t.get("repo_id", "default")
    cost_per_repo[repo_id] = cost_per_repo.get(repo_id, 0) + t.get("cost_usd", 0)

# 2. Determine which repos have changes
repos_with_changes = [
    repo_id for repo_id, tasks in tasks_by_repo.items()
    if any(t["state"] == "done" for t in tasks)
]

# 3. Push + create PR for each repo
pr_urls: dict[str, str] = {}
for repo_id in repos_with_changes:
    repo_config = repos[repo_id]
    pipeline_branch = pipeline_branches[repo_id]

    pushed = await push_branch(repo_config.path, pipeline_branch)
    if not pushed:
        # Log error, continue with other repos
        continue

    body = generate_pr_body(
        tasks=tasks_by_repo[repo_id],
        failed_tasks=failed_by_repo.get(repo_id),
        time=elapsed_str,
        cost=cost_per_repo.get(repo_id, 0),
        questions=all_questions,
        related_prs=pr_urls,  # PRs created so far (for cross-linking)
        repo_id=repo_id,
    )

    url = await create_pr(
        repo_config.path,
        title=f"Forge: {description} [{repo_id}]",
        body=body,
        base=repo_config.base_branch,
        head=pipeline_branch,
    )
    if url:
        pr_urls[repo_id] = url

# 4. Update earlier PRs with links to later PRs
for repo_id, url in pr_urls.items():
    other_prs = {k: v for k, v in pr_urls.items() if k != repo_id}
    if other_prs:
        await _add_related_prs_comment(repo_config.path, url, other_prs)
```

### 9.2 PR Body Format (multi-repo)

```markdown
## Summary

Built by Forge pipeline • 5 tasks • 8m 30s • $4.21

## Related PRs
- **frontend**: https://github.com/org/frontend/pull/89

## Tasks

- :white_check_mark: **Add /users API endpoint** — +120/-5, 4 files
  <details><summary>Details</summary>

  **What:** Create REST endpoint for user CRUD operations
  **Done:** Added FastAPI router with GET/POST/PUT/DELETE endpoints
  **Files:** `src/routes/users.py`, `src/models/user.py`, `tests/test_users.py`

  </details>

- :white_check_mark: **Add user model** — +45/-0, 2 files
  <details><summary>Details</summary>

  **What:** SQLAlchemy user model with validation
  **Done:** Created User model with email uniqueness constraint
  **Files:** `src/models/user.py`, `src/schemas/user.py`

  </details>

:robot: Built with [Forge](https://github.com/tarunms7/forge-orchestrator)
```

### 9.3 Cross-Linking PRs

After all PRs are created, add a comment to each PR linking to the related PRs:

```python
async def _add_related_prs_comment(
    project_dir: str, pr_url: str, related: dict[str, str]
) -> None:
    # Extract PR number from URL
    pr_number = pr_url.rstrip("/").split("/")[-1]
    links = "\n".join(f"- **{repo}**: {url}" for repo, url in related.items())
    comment = f"## Related Forge PRs\n\n{links}"
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "comment", pr_number, "--body", comment,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
```

### 9.4 Single-Repo PR (unchanged)

When `len(repos) == 1`, the PR flow is exactly as today. No "Related PRs" section, no repo labels in title. Zero visible difference.

---

## 10. Follow-Up & Interjection Changes

### 10.1 Follow-Ups (post-pipeline)

Follow-ups route questions to tasks, which are already repo-scoped. The follow-up executor needs to create worktrees in the correct repo:

The current `followup.py` (line 318) constructs worktree paths with raw `os.path.join(project_dir, ".forge", "worktrees", worktree_id)`. For multi-repo, this must be replaced:

```python
# In followup.py, _execute_task_followup():
task = await db.get_task(task_id)
repo_id = task.repo_id or "default"
repos = pipeline.get_repos()
repo_config = next(r for r in repos if r["id"] == repo_id)

# Compute worktree dir — respect single/multi layout
if len(repos) == 1 and repo_id == "default":
    worktrees_dir = os.path.join(pipeline.project_dir, ".forge", "worktrees")
else:
    worktrees_dir = os.path.join(pipeline.project_dir, ".forge", "worktrees", repo_id)

# Replace the raw os.path.join at line 318 with WorktreeManager
worktree_path = WorktreeManager(
    repo_config["path"], worktrees_dir,
).create(f"followup-{task_id}", base_ref=pipeline.branch_name)
```

### 10.2 Interjections (during pipeline)

Interjections are already task-scoped and delivered to the agent's existing worktree. No changes needed — the worktree is already in the correct repo because it was created during task dispatch.

---

## 11. Worktree Layout & Cleanup

### 11.1 Directory Structure

```
{workspace}/.forge/worktrees/
  {repo_id}/
    {task_id}/          # git worktree for this task in this repo
```

Single-repo equivalent:
```
{project_dir}/.forge/worktrees/
  {task_id}/            # current behavior (no repo_id nesting)
```

**Backward compatibility**: For single-repo mode, keep the flat layout (no `default/` subdirectory). Only use the nested layout when `len(repos) > 1`.

### 11.2 Cleanup on Success

After a task merges successfully:
```python
worktree_mgr = self._worktree_managers[repo_id]
worktree_mgr.remove(task_id)
```

Same as today, just uses the correct per-repo manager.

### 11.3 Cleanup on Pipeline Cancel

```python
# In _cleanup_all_pipeline_worktrees():
for repo_id, mgr in self._worktree_managers.items():
    for task_id in pipeline_task_ids:
        try:
            mgr.remove(task_id)
        except Exception:
            pass  # best-effort cleanup
```

### 11.4 Cleanup on Hard Failure (process crash)

Worktrees are git-managed. On next `forge run`, stale worktrees from crashed pipelines are detected:
- `git worktree list` in each repo shows orphaned worktrees
- `git worktree prune` cleans up references to deleted directories
- The `.forge/worktrees/` directory can be safely deleted entirely

---

## 12. Per-Repo Config

### 12.1 Repo-Specific Settings

Each repo can have its own `.forge/forge.toml` with build/test/lint commands:

```toml
# backend/.forge/forge.toml
[tests]
cmd = "pytest"

[lint]
cmd = "ruff check ."
fix_cmd = "ruff check --fix ."

[build]
cmd = "pip install -e ."
```

```toml
# frontend/.forge/forge.toml
[tests]
cmd = "npm test"

[lint]
cmd = "eslint src/"
fix_cmd = "eslint --fix src/"

[build]
cmd = "npm run build"
```

### 12.2 Config Loading

During pipeline setup, load config for each repo independently:

```python
repo_configs: dict[str, ProjectConfig] = {}
for repo_id, rc in self._repos.items():
    repo_configs[repo_id] = ProjectConfig.load(rc.path)
```

When executing review gates (lint, test, build) for a task, use that task's repo config:

```python
repo_id = task.repo_id
config = repo_configs[repo_id]
test_cmd = config.tests.cmd  # e.g., "pytest" for backend, "npm test" for frontend
```

### 12.3 Global vs Repo Settings

| Setting | Scope | Source |
|---------|-------|--------|
| `max_agents` | Global | `ForgeSettings` (env var / CLI) |
| `budget_limit_usd` | Global (pipeline-level) | `ForgeSettings` |
| `autonomy` | Global | `ForgeSettings` |
| `test_cmd` | Per-repo | `{repo}/.forge/forge.toml` |
| `lint_cmd` | Per-repo | `{repo}/.forge/forge.toml` |
| `build_cmd` | Per-repo | `{repo}/.forge/forge.toml` |
| `instructions` | Per-repo | `{repo}/.forge/forge.toml` |
| `base_branch` | Per-repo | `--repo` flag or `workspace.toml` |
| `review.max_retries` | Per-repo | `{repo}/.forge/forge.toml` |

---

## 13. Web API Changes

### 13.1 Pipeline Creation

```python
# POST /tasks
class CreateTaskRequest(BaseModel):
    description: str
    project_path: str              # workspace root
    repos: list[dict] | None = None  # NEW — [{"id": "backend", "path": "..."}]
    model_strategy: str = "auto"
    # ... existing fields
```

When `repos` is provided, validate and store in `PipelineRow.repos_json`.

### 13.2 Pipeline Status Response

Add repos info to pipeline status:

```python
# GET /tasks/{pipeline_id}
{
    "pipeline_id": "abc123",
    "status": "executing",
    "repos": [
        {"id": "backend", "path": "/abs/path/backend", "base_branch": "main"},
        {"id": "frontend", "path": "/abs/path/frontend", "base_branch": "main"}
    ],
    "tasks": [
        {"id": "task-1", "title": "...", "repo_id": "backend", "state": "done"},
        {"id": "task-2", "title": "...", "repo_id": "frontend", "state": "in_progress"}
    ]
}
```

### 13.3 Diff Endpoint

The diff endpoint needs to know which repo to diff against:

```python
# POST /tasks/{pipeline_id}/diff
# Currently: uses pipeline.project_dir
# Multi-repo: use task's repo path
task = await db.get_task(task_id)
repos = pipeline.get_repos()
repo = next(r for r in repos if r["id"] == task.repo_id)
# Run git diff in repo["path"]
```

### 13.4 Follow-Up Endpoint

Follow-up already receives `pipeline_id` and routes to tasks. Task's `repo_id` determines the repo. Minimal changes — just pass `repo_id` to worktree creation.

---

## 14. TUI Changes

### 14.1 Task Display

The pipeline screen task table adds a repo column when multi-repo:

```
  Tasks (2 repos)
  ────────────────────────────────────────────────
  [backend]  ✅ Add /users API         +120/-5
  [backend]  ✅ Add user model          +45/-0
  [frontend] 🔄 Add login page          ...
  [frontend] ⏳ Add dashboard           (waiting)
```

Single-repo: no repo column (exact current display).

### 14.2 Final Approval Screen

Shows per-repo breakdown:

```
  Pipeline Complete — 2 repos, 4 tasks, 8m 30s, $4.21

  backend (2 tasks, +165/-5)
    ✅ Add /users API          +120/-5   tests: 12/12
    ✅ Add user model           +45/-0   tests: 5/5

  frontend (2 tasks, +89/-3)
    ✅ Add login page           +55/-3   tests: 8/8
    ✅ Add dashboard            +34/-0   tests: 4/4

  [Create PRs]  [View Diff]  [Cancel]
```

"Create PRs" (plural) when multi-repo. Creates one PR per repo.

### 14.3 Diff Viewer

When user selects "View Diff", show a repo selector first if multi-repo:

```
  Select repo to view diff:
  > backend  (+165/-5, 4 files)
    frontend (+89/-3, 6 files)
```

Then show the diff for the selected repo. Single-repo: skip selector, show diff directly.

---

## 15. Failure Scenarios (Exhaustive)

### 15.1 Planning Failures

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| Planner assigns task to unknown repo | Validation rejects, retry with feedback | Auto-retry up to 3 times. If still wrong, pipeline fails with clear error. |
| Planner doesn't include `repo` field | Default to `"default"`. In multi-repo mode where `"default"` doesn't exist, validation rejects + retry. | Auto-retry with explicit instruction to include repo field. |
| Planner creates cross-repo file references | Validation checks `files` are relative (no `/` prefix, no `../`). If file path starts with another repo name, reject. | Auto-retry with feedback: "Task X has file 'backend/src/api.py' but is assigned to repo 'frontend'. Files must be relative to the task's repo." |
| Planner creates circular cross-repo deps | Existing cycle detection catches this (in `validate_plan`). | Auto-retry with feedback about the cycle. |
| Snapshot gathering fails for one repo | Log warning, include empty snapshot for that repo. Planner proceeds with partial info. | Planner can still read the repo via tools (Glob/Grep/Read). |

### 15.2 Execution Failures

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| Worktree creation fails in one repo | Task fails, other repos unaffected. | Task retries with backoff. If worktree dir exists, reuse it. |
| Agent writes to wrong repo | Unlikely — agent CWD is the worktree, so relative writes stay in-scope. If the agent constructs absolute paths to another repo, `_enforce_file_scope()` won't catch it (different git repo). However, such writes would land in the other repo's main checkout (not a worktree) and would show up as uncommitted changes, failing the "dirty working tree" check on the next pipeline run. | CWD scoping prevents most cases. Absolute-path escapes are caught by dirty-tree checks. |
| Merge conflict in one repo | Tier 1 (auto-rebase) → Tier 2 (agent resolution) → merge retry. Other repos unaffected. | Existing retry cascade. No changes. |
| Pipeline branch creation fails | `git branch -f` fails only if repo is in broken state. Fail the pipeline with error. | User must fix repo state manually. |
| One repo's test/lint/build command fails | Task review fails. Task retries with feedback. Other repos unaffected. | Existing review retry. Per-repo commands used. |
| Agent timeout in one repo | Task fails after timeout. Dependent tasks in all repos become BLOCKED. | Existing timeout handling. Consider: longer timeout for repos with slow builds. |

### 15.3 Merge Failures

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| Rebase conflict from concurrent tasks in same repo | Merge lock serializes merges. Tier 1 retry re-rebases. | Existing Tier 1 + Tier 2 resolution. |
| Pipeline branch diverged (stale ref) | `_resolve_ref()` snapshots before merge. If ref invalid, fallback to commit-count heuristic. | Already handled by `_get_diff_stats`. |
| Fast-forward fails after successful rebase | `git update-ref` fails only if concurrent modification. Merge lock prevents this. | Merge lock is the prevention. |

### 15.4 PR Creation Failures

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| Push fails for one repo | Log error. Continue creating PRs for other repos. Report which repos failed. | User can manually push and create PR. |
| PR creation fails for one repo | Same as push failure. Other repos get their PRs. | User uses `gh pr create` manually. |
| gh CLI not authenticated for one repo | `gh pr create` fails. Error logged. | User runs `gh auth login`. |
| Cross-link comment fails | PR was already created. Link is cosmetic. Log and continue. | User can manually add comments. |
| First PR created, second fails | First PR exists. Show URL to user. Report second failure. | User creates second PR manually. First PR body has placeholder for related PRs. |

### 15.5 Cleanup Failures

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| Worktree removal fails | `git worktree remove --force` handles most cases. If still fails, log warning. | User runs `git worktree prune` manually. |
| Stale worktrees from previous run | On startup, check for orphaned worktrees. Warn user but don't auto-delete. | User decides to clean up or keep. |
| `.forge/worktrees/` dir doesn't exist | Create it. `os.makedirs(exist_ok=True)`. | Auto-created. |

### 15.6 Configuration Failures

| Scenario | Behavior | Recovery |
|----------|----------|----------|
| Repo `.forge/forge.toml` has syntax error | Fall back to defaults for that repo. Log warning. | User fixes TOML syntax. |
| Repo `.forge/forge.toml` missing | Use defaults (no lint, no test, no build). | Expected — not all repos need config. |
| workspace.toml has invalid repo path | Fail fast at startup (Section 5.3 validation). | User fixes workspace.toml. |
| workspace.toml + CLI `--repo` conflict | CLI wins. workspace.toml ignored entirely. | Documented behavior. |

### 15.7 Edge Cases

| Scenario | Behavior |
|----------|----------|
| All tasks in one repo, other repos unused | Unused repos: no worktrees, no branches, no PRs. Clean. |
| 10+ repos in workspace | Works but slow snapshot gathering. Consider: parallel snapshot with `asyncio.gather`. Planner context may be too large — truncate file trees for large repos. |
| Repo is a submodule of another repo | Works if path resolves to a real git repo. Worktrees created independently. |
| Two repos share a git remote | Different local repos, same remote. PRs created against correct remote. `gh pr create` uses CWD to determine remote. |
| Repo on a different git hosting platform | Works if `gh` CLI is configured for that platform. Otherwise PR creation fails (gracefully). |
| Repo has no remote | `git push` fails. PR creation fails. Logged. Other repos unaffected. |
| Pipeline cancelled mid-execution | All active tasks cancelled. Worktrees cleaned up per-repo. Partial pipeline branches left (harmless). |
| User wants to retry one failed repo | Not supported in v1. Must re-run entire pipeline. Future: `forge retry --repo backend`. |
| Repo's default branch is renamed mid-pipeline | `base_branch` is captured at startup and stored in `repos_json`. Pipeline uses the captured value throughout. If the remote branch is renamed during execution, the push will fail — user re-runs with correct `base_branch`. |
| Repo has uncommitted submodule changes | Detected by `git status` during startup validation (dirty working tree check). Fails with clear message. |
| Two tasks in different repos modify a shared git submodule | Each repo has its own submodule checkout. Changes in repo A's submodule don't affect repo B's. If both modify the same submodule, the PRs are independent — reviewer handles coordination. |
| Agent installs dependencies that conflict across repos | Each agent runs in its own worktree. Dependencies are repo-local (venv, node_modules). No shared state between agents in different repos. |
| Workspace root is itself a git repo | Startup validation checks if any `--repo` path is the workspace root. If workspace root IS a git repo, it's treated as a regular repo (single-repo mode). If workspace root is a git repo AND `--repo` flags are given, the workspace repo is NOT automatically included — only explicit `--repo` paths are used. |

---

## 16. Performance Considerations

### 16.1 Snapshot Gathering

For N repos, gather snapshots in parallel:

```python
snapshots = await asyncio.gather(*(
    asyncio.to_thread(gather_project_snapshot, rc.path)
    for rc in self._repos.values()
))
```

Each snapshot takes ~1-2 seconds (git ls-files + file counting). For 5 repos: ~2 seconds parallel vs ~10 seconds serial.

### 16.2 Planner Context Size

Each repo's snapshot adds ~2-4K tokens to the planner prompt. For 5 repos: ~10-20K tokens additional context. This is within Opus's capacity but should be monitored.

For repos with 500+ files, truncate the file tree to top 3 directory levels. The planner can use `Glob`/`Read` to explore deeper.

### 16.3 Agent Parallelism

`max_agents` is a global cap across all repos. With 5 repos and `max_agents=5`, at most 5 agents run simultaneously across all repos. This is intentional — agents are the expensive resource (LLM calls), not repos.

---

## 17. Testing Strategy

### 17.1 Unit Tests

| Test | What it verifies |
|------|-----------------|
| `test_build_task_summaries_multi_repo` | Task summaries include `repo_id` |
| `test_generate_pr_body_multi_repo` | PR body groups tasks by repo, includes "Related PRs" |
| `test_task_graph_repo_validation` | Planner validation rejects unknown repo IDs |
| `test_repo_config_loading` | Per-repo config loaded correctly |
| `test_single_repo_backward_compat` | Single-repo mode unchanged |
| `test_workspace_toml_parsing` | workspace.toml parsed correctly |
| `test_cli_repo_flag_parsing` | `--repo name=path` parsed correctly |
| `test_cli_repo_validation` | Invalid paths/duplicates caught at startup |

### 17.2 Integration Tests

| Test | What it verifies |
|------|-----------------|
| `test_multi_repo_worktree_creation` | Worktrees created in correct repo subdirectories |
| `test_multi_repo_merge_isolation` | Merge in one repo doesn't affect another |
| `test_cross_repo_dependency_ordering` | Backend task completes before frontend task starts |
| `test_multi_repo_pr_creation` | One PR per repo, cross-linked |
| `test_partial_failure_isolation` | Failed repo doesn't block successful repos' PRs |

### 17.3 End-to-End Test

Create two test repos (backend + frontend), run a multi-repo pipeline, verify:
1. Tasks created with correct `repo_id`
2. Worktrees in correct directories
3. Merges in correct repos
4. Two PRs created with cross-links

---

## 18. Implementation Order

### Phase 1: Data Model (no behavior change)
1. Add `repo` field to `TaskDefinition` (models.py) — defaults to `"default"`
2. Add `repo_id` column to `TaskRow` (db.py) — auto-migrated
3. Add `repos_json` column to `PipelineRow` (db.py)
4. Add `RepoConfig` dataclass

**Verify**: All existing tests pass with no changes. Single-repo still works.

### Phase 2: CLI & Config
5. Add `--repo` flag to `forge run` and `forge tui`
6. Add startup validation (Section 5.3)
7. Add `workspace.toml` support
8. Add workspace detection logic (Section 5.5 loading order)

### Phase 3: Daemon Core
9. Update `ForgeDaemon.__init__` to accept `repos` parameter
10. Create per-repo infrastructure registry (WorktreeManagers, MergeWorkers)
11. Create per-repo pipeline branches during `execute()`
12. Route task dispatch through repo lookup (Section 6.3)
13. Set `allowed_dirs` to include all repo paths

### Phase 4: Planner
14. Multi-repo snapshot gathering (parallel)
15. Multi-repo system prompt addition
16. Repo field validation in planner output
17. Single-repo backward compat (no repo field = "default")

### Phase 5: Review & Merge
18. Per-repo config loading for review gates (test_cmd, lint_cmd, build_cmd)
19. Review gates use task's repo config
20. Merge uses task's repo MergeWorker

### Phase 6: PR Creation
21. Group tasks by repo for PR body
22. Per-repo push + PR creation
23. Cross-linking PRs with comments
24. Update `generate_pr_body` for multi-repo format

### Phase 7: TUI
25. Task table repo column (multi-repo only)
26. Final approval screen per-repo breakdown
27. Diff viewer repo selector

### Phase 8: Web API
28. Accept `repos` in pipeline creation endpoint
29. Return `repo_id` in task status responses
30. Route diff endpoint to correct repo

### Phase 9: Follow-Up
31. Follow-up executor uses task's `repo_id` for worktree creation
32. Follow-up question routing includes repo context

---

## 19. What We Are NOT Building

These are explicitly out of scope for v1:

| Feature | Why not |
|---------|---------|
| Single task spanning multiple repos | Git doesn't support atomic cross-repo commits. Would require a fundamentally different merge strategy. |
| Auto-discovery of repos (monorepo detection) | Too many edge cases (nested repos, submodules, unrelated dirs). Explicit `--repo` is clearer. |
| Per-repo agent limits | Over-engineering. Global `max_agents` is sufficient. |
| Per-repo budgets | Over-engineering. Pipeline-level budget is sufficient. |
| Retry single repo | Requires partial pipeline re-execution. Add in v2 if needed. |
| Cross-repo file references in tasks | Tasks must use relative paths within their repo. Cross-repo reading happens via `allowed_dirs` at runtime. |
| Shared types/contracts between repos | The planner can understand shared patterns and create tasks accordingly, but there's no formal contract system between repos. |

---

## 20. Success Criteria

This feature is done when:

1. `forge run "task" --repo backend=./b --repo frontend=./f` creates a pipeline that correctly assigns tasks to repos
2. Each task runs in the correct repo's worktree
3. Cross-repo dependencies execute in correct order with context passing
4. One PR per repo is created, with cross-links
5. `forge run "task"` (no `--repo`) behaves identically to current behavior
6. All existing tests pass without modification
7. New tests cover every scenario in Section 15
8. Failure in one repo does not cascade to other repos (unless via `depends_on`)
