# Phase 3: Daemon Core — Multi-Repo Infrastructure

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `ForgeDaemon` to accept multiple repos, create per-repo infrastructure (worktree managers, merge workers, pipeline branches), route task dispatch by `repo_id`, and replace all hardcoded worktree paths with the new `_worktree_path()` helper.

**Architecture:** The daemon becomes multi-repo-aware by accepting an optional `repos: list[RepoConfig]` parameter. When absent (or a single `default` entry), behavior is identical to the current single-repo mode. When multiple repos are provided, each gets its own `WorktreeManager`, `MergeWorker`, and pipeline branch. Task dispatch resolves the correct repo infrastructure via `task.repo_id`.

**Tech Stack:** Python 3.12+, asyncio, pydantic-settings, claude-code-sdk

**Spec:** `docs/superpowers/specs/2026-03-21-multi-repo-workspace-design.md` (Sections 6, 15.2)

**Dependencies:** Phase 1 (data model) + Phase 2 (CLI & config) must be merged first.

**Verification:** `.venv/bin/python -m pytest forge/core/daemon_test.py forge/core/daemon_executor_test.py -x -v`

---

## File Map

| File | Responsibility | Changes |
|------|---------------|---------|
| `forge/core/daemon.py` | Main orchestration loop | Add `repos` param to `__init__`, `_init_repos()`, per-repo infra in `execute()`, `_worktree_path()` helper |
| `forge/core/daemon_executor.py` | Task execution pipeline | Replace 3 hardcoded worktree paths with `_worktree_path()` |
| `forge/core/daemon_helpers.py` | Shared helper functions | Add standalone `_worktree_path()` helper (if needed outside daemon) |
| `forge/merge/worktree.py` | Git worktree management | No signature change — per-repo instantiation only |
| `forge/merge/worker.py` | Merge/rebase operations | No signature change — per-repo instantiation only |
| `forge/storage/db.py` | Pipeline persistence | Store `repos_json` on PipelineRow during `execute()` |

---

## Chunk 1: ForgeDaemon Multi-Repo Init — Critical

Core initialization changes that make the daemon aware of multiple repos. Everything else depends on this.

### Task 1: Add `repos` Parameter to `ForgeDaemon.__init__`

**Files:**
- Modify: `forge/core/daemon.py:167-182` (`ForgeDaemon.__init__`)
- Test: `forge/core/daemon_test.py` (add new test class)

- [ ] **Step 1: Write failing tests for multi-repo init**

Add to `forge/core/daemon_test.py`:

```python
from forge.core.models import RepoConfig


class TestDaemonMultiRepoInit:
    """Tests for ForgeDaemon multi-repo initialization."""

    def test_daemon_init_with_repos(self, tmp_path):
        """Multi-repo init creates per-repo dicts keyed by repo id."""
        backend = tmp_path / "backend"
        frontend = tmp_path / "frontend"
        backend.mkdir()
        frontend.mkdir()
        # init bare git repos
        subprocess.run(["git", "init"], cwd=backend, capture_output=True)
        subprocess.run(["git", "init"], cwd=frontend, capture_output=True)

        repos = [
            RepoConfig(id="backend", path=str(backend), base_branch="main"),
            RepoConfig(id="frontend", path=str(frontend), base_branch="develop"),
        ]
        daemon = ForgeDaemon(str(tmp_path), repos=repos)

        assert "backend" in daemon._repos
        assert "frontend" in daemon._repos
        assert daemon._repos["backend"].path == str(backend)
        assert daemon._repos["frontend"].base_branch == "develop"

    def test_daemon_init_single_repo_default(self, tmp_path):
        """No repos param = single 'default' repo using project_dir."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        daemon = ForgeDaemon(str(tmp_path))

        assert "default" in daemon._repos
        assert len(daemon._repos) == 1
        assert daemon._repos["default"].path == str(tmp_path)
```

Run and confirm failures:
```bash
.venv/bin/python -m pytest forge/core/daemon_test.py::TestDaemonMultiRepoInit -x -v
```

- [ ] **Step 2: Update `ForgeDaemon.__init__` to accept `repos`**

In `forge/core/daemon.py`, modify `__init__` (line 167) to accept the new parameter and build `self._repos`:

```python
def __init__(
    self,
    project_dir: str,
    settings: ForgeSettings | None = None,
    event_emitter: EventEmitter | None = None,
    repos: list[RepoConfig] | None = None,
) -> None:
    from forge.config.project_config import ProjectConfig

    self._project_dir = project_dir
    self._settings = settings or ForgeSettings()
    self._state_machine = TaskStateMachine()
    self._events = event_emitter or EventEmitter()
    self._strategy = self._settings.model_strategy
    self._snapshot: ProjectSnapshot | None = None
    self._merge_lock = asyncio.Lock()
    self._project_config = ProjectConfig.load(project_dir)

    # Build per-repo lookup. Default: single repo at project_dir.
    if repos:
        self._repos: dict[str, RepoConfig] = {r.id: r for r in repos}
    else:
        self._repos = {
            "default": RepoConfig(
                id="default",
                path=project_dir,
                base_branch="main",  # resolved async in _init_repos()
            ),
        }
```

Import `RepoConfig` at the top of the file:
```python
from forge.core.models import RepoConfig
```

- [ ] **Step 3: Run tests and verify they pass**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py::TestDaemonMultiRepoInit -x -v
```

### Task 2: Async Base Branch Resolution via `_init_repos()`

**Files:**
- Modify: `forge/core/daemon.py` (add `_init_repos()` method after `__init__`)
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing test for async base branch resolution**

Add to the `TestDaemonMultiRepoInit` class in `forge/core/daemon_test.py`:

```python
@pytest.mark.asyncio
async def test_init_repos_resolves_base_branch(self, tmp_path):
    """_init_repos() resolves 'auto' base branches using git."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "develop"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, capture_output=True)

    repos = [RepoConfig(id="myrepo", path=str(repo), base_branch="auto")]
    daemon = ForgeDaemon(str(tmp_path), repos=repos)
    await daemon._init_repos()

    # 'auto' should resolve to the actual branch name
    assert daemon._repos["myrepo"].base_branch == "develop"
```

- [ ] **Step 2: Implement `_init_repos()`**

Add this method to `ForgeDaemon` after `__init__`:

```python
async def _init_repos(self) -> None:
    """Resolve 'auto' base branches and validate repo paths."""
    from forge.core.daemon_helpers import _get_current_branch

    resolved: dict[str, RepoConfig] = {}
    for repo_id, rc in self._repos.items():
        base = rc.base_branch
        if base == "auto" or (repo_id == "default" and base == "main"):
            base = await _get_current_branch(rc.path)
        resolved[repo_id] = RepoConfig(id=rc.id, path=rc.path, base_branch=base)
    self._repos = resolved
```

Note: `_get_current_branch` is at `forge/core/daemon_helpers.py:224`. It safely falls back to `"main"` on detached HEAD or empty repos.

- [ ] **Step 3: Call `_init_repos()` at the start of `execute()`**

In `forge/core/daemon.py`, at the beginning of the `execute()` method (line 588), add:

```python
await self._init_repos()
```

This must happen before any worktree manager or merge worker creation.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py::TestDaemonMultiRepoInit::test_init_repos_resolves_base_branch -x -v
```

---

## Chunk 2: Per-Repo Infrastructure — Critical

Creates worktree managers, merge workers, and pipeline branches for each repo during `execute()`.

### Task 3: Per-Repo WorktreeManager and MergeWorker Creation

**Files:**
- Modify: `forge/core/daemon.py` — `execute()` method (line 588+)
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing test for per-repo manager creation**

```python
class TestDaemonPerRepoInfra:
    """Tests for per-repo infrastructure creation in execute()."""

    @pytest.mark.asyncio
    async def test_daemon_init_with_repos_creates_managers(self, tmp_path):
        """Multi-repo init creates per-repo WorktreeManager and MergeWorker."""
        backend = tmp_path / "backend"
        frontend = tmp_path / "frontend"
        for d in (backend, frontend):
            d.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=d, capture_output=True)
            subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=d, capture_output=True)

        repos = [
            RepoConfig(id="backend", path=str(backend), base_branch="main"),
            RepoConfig(id="frontend", path=str(frontend), base_branch="main"),
        ]
        daemon = ForgeDaemon(str(tmp_path), repos=repos)

        # Mock the rest of execute() — we only need the infra setup part
        await daemon._init_repos()
        daemon._setup_per_repo_infra("test-pipeline-123")

        assert "backend" in daemon._worktree_managers
        assert "frontend" in daemon._worktree_managers
        assert "backend" in daemon._merge_workers
        assert "frontend" in daemon._merge_workers
```

- [ ] **Step 2: Implement `_setup_per_repo_infra()`**

Add to `ForgeDaemon` in `forge/core/daemon.py`:

```python
def _setup_per_repo_infra(self, pipeline_id: str) -> None:
    """Create WorktreeManager, MergeWorker, and pipeline branch per repo."""
    from forge.merge.worktree import WorktreeManager
    from forge.merge.worker import MergeWorker

    self._worktree_managers: dict[str, WorktreeManager] = {}
    self._merge_workers: dict[str, MergeWorker] = {}
    self._pipeline_branches: dict[str, str] = {}

    for repo_id, rc in self._repos.items():
        # Worktree dir: .forge/worktrees/ (single) or .forge/worktrees/<repo_id>/ (multi)
        if len(self._repos) == 1 and repo_id == "default":
            wt_dir = os.path.join(self._project_dir, ".forge", "worktrees")
        else:
            wt_dir = os.path.join(self._project_dir, ".forge", "worktrees", repo_id)

        self._worktree_managers[repo_id] = WorktreeManager(rc.path, wt_dir)
        self._merge_workers[repo_id] = MergeWorker(rc.path, rc.base_branch)

        # Pipeline branch name: forge/pipeline-<id>
        branch_name = f"forge/pipeline-{pipeline_id}"
        self._pipeline_branches[repo_id] = branch_name
```

- [ ] **Step 3: Create pipeline branches via `git branch -f` per repo**

Add a method and call it from `_setup_per_repo_infra()`:

```python
async def _create_pipeline_branches(self) -> None:
    """Create (or reset) pipeline branches in each repo."""
    for repo_id, rc in self._repos.items():
        branch_name = self._pipeline_branches[repo_id]
        proc = await asyncio.create_subprocess_exec(
            "git", "branch", "-f", branch_name, rc.base_branch,
            cwd=rc.path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
```

- [ ] **Step 4: Write test for pipeline branch creation**

```python
@pytest.mark.asyncio
async def test_pipeline_branches_created_per_repo(self, tmp_path):
    """git branch -f creates pipeline branch in each repo."""
    backend = tmp_path / "backend"
    backend.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=backend, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=backend, capture_output=True)

    repos = [RepoConfig(id="backend", path=str(backend), base_branch="main")]
    daemon = ForgeDaemon(str(tmp_path), repos=repos)
    await daemon._init_repos()
    daemon._setup_per_repo_infra("abc123")
    await daemon._create_pipeline_branches()

    # Verify the branch exists
    result = subprocess.run(
        ["git", "branch", "--list", "forge/pipeline-abc123"],
        cwd=backend, capture_output=True, text=True,
    )
    assert "forge/pipeline-abc123" in result.stdout
```

- [ ] **Step 5: Wire `_setup_per_repo_infra()` into `execute()`**

In the `execute()` method of `ForgeDaemon` (line 588), after `await self._init_repos()`, replace the existing single-repo WorktreeManager/MergeWorker creation with:

```python
self._setup_per_repo_infra(pipeline_id)
await self._create_pipeline_branches()
```

The existing code at approximately line 654 currently does:
```python
worktree_mgr = WorktreeManager(self._project_dir, f"{self._project_dir}/.forge/worktrees")
```

This single instantiation must be replaced by the per-repo dict lookup. Downstream code that passes `worktree_mgr` to `_execute_task()` must look up the correct manager using `task.repo_id` (or `"default"`).

- [ ] **Step 6: Run tests**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py::TestDaemonPerRepoInfra -x -v
```

---

## Chunk 3: Worktree Path Helper — Critical

Replaces all hardcoded `.forge/worktrees/<task_id>` paths with a single helper that handles both single-repo (flat) and multi-repo (nested) layouts.

### Task 4: Implement `_worktree_path()` Helper

**Files:**
- Modify: `forge/core/daemon.py` (add method to `ForgeDaemon`)
- Modify: `forge/core/daemon_helpers.py` (add standalone function)
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing tests for worktree path helper**

```python
class TestWorktreePath:
    """Tests for _worktree_path() helper."""

    def test_worktree_path_single_repo(self, tmp_path):
        """Single 'default' repo: flat layout without repo_id nesting."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        daemon = ForgeDaemon(str(tmp_path))  # no repos → single default

        path = daemon._worktree_path("default", "task-abc")
        expected = os.path.join(str(tmp_path), ".forge", "worktrees", "task-abc")
        assert path == expected

    def test_worktree_path_multi_repo(self, tmp_path):
        """Multi-repo: nested layout with repo_id directory."""
        backend = tmp_path / "backend"
        frontend = tmp_path / "frontend"
        backend.mkdir()
        frontend.mkdir()
        subprocess.run(["git", "init"], cwd=backend, capture_output=True)
        subprocess.run(["git", "init"], cwd=frontend, capture_output=True)

        repos = [
            RepoConfig(id="backend", path=str(backend), base_branch="main"),
            RepoConfig(id="frontend", path=str(frontend), base_branch="main"),
        ]
        daemon = ForgeDaemon(str(tmp_path), repos=repos)

        path = daemon._worktree_path("backend", "task-abc")
        expected = os.path.join(str(tmp_path), ".forge", "worktrees", "backend", "task-abc")
        assert path == expected
```

- [ ] **Step 2: Implement `_worktree_path()` on ForgeDaemon**

Add to `ForgeDaemon` in `forge/core/daemon.py`:

```python
def _worktree_path(self, repo_id: str, task_id: str) -> str:
    """Return the worktree directory for a task.

    Single-repo (default): .forge/worktrees/<task_id>  (flat, backward compat)
    Multi-repo:            .forge/worktrees/<repo_id>/<task_id>  (nested)
    """
    if len(self._repos) == 1 and repo_id == "default":
        return os.path.join(self._project_dir, ".forge", "worktrees", task_id)
    return os.path.join(self._project_dir, ".forge", "worktrees", repo_id, task_id)
```

- [ ] **Step 3: Add standalone helper in `daemon_helpers.py`**

For code outside `ForgeDaemon` that needs worktree paths (e.g., `followup.py`, `api/routes/tasks.py`), add to `forge/core/daemon_helpers.py`:

```python
def compute_worktree_path(
    project_dir: str, repo_id: str, task_id: str, *, multi_repo: bool = False,
) -> str:
    """Compute worktree path for a task.

    Single-repo: <project_dir>/.forge/worktrees/<task_id>
    Multi-repo:  <project_dir>/.forge/worktrees/<repo_id>/<task_id>
    """
    if not multi_repo or repo_id == "default":
        return os.path.join(project_dir, ".forge", "worktrees", task_id)
    return os.path.join(project_dir, ".forge", "worktrees", repo_id, task_id)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py::TestWorktreePath -x -v
```

### Task 5: Replace Hardcoded Worktree Paths in `daemon_executor.py`

**Files:**
- Modify: `forge/core/daemon_executor.py` (lines 176, 210, 587)
- Test: `forge/core/daemon_executor_test.py`

- [ ] **Step 1: Grep for all hardcoded worktree path constructions**

Run this search to find all occurrences:

```bash
grep -rn 'os.path.join.*".forge".*"worktrees"' forge/
```

Expected matches (at minimum):
- `forge/core/daemon_executor.py:176` — `_handle_merge_fast_path()`
- `forge/core/daemon_executor.py:210` — `_prepare_worktree()`
- `forge/core/daemon_executor.py:587` — `_answer_agent_question()`
- `forge/core/daemon.py:654` — `execute()` (WorktreeManager instantiation)

Also check for paths constructed differently:
```bash
grep -rn '\.forge/worktrees' forge/
```

Fix ALL occurrences. Do not fix one and leave the rest.

- [ ] **Step 2: Replace line 176 in `_handle_merge_fast_path()`**

Current (line 176):
```python
worktree_path = os.path.join(self._project_dir, ".forge", "worktrees", task_id)
```

Replace with:
```python
worktree_path = self._worktree_path(repo_id, task_id)
```

The `repo_id` parameter must be threaded through from `_execute_task()`. Update the method signature to accept `repo_id: str`.

- [ ] **Step 3: Replace line 210 in `_prepare_worktree()`**

Current (line 210):
```python
wt = os.path.join(self._project_dir, ".forge", "worktrees", task_id)
```

Replace with:
```python
wt = self._worktree_path(repo_id, task_id)
```

Update method signature to accept `repo_id: str`.

- [ ] **Step 4: Replace line 587 in `_answer_agent_question()`**

Current (line 587):
```python
worktree_path = os.path.join(self._project_dir, ".forge", "worktrees", task_id)
```

Replace with:
```python
worktree_path = self._worktree_path(repo_id, task_id)
```

Update method signature to accept `repo_id: str`.

- [ ] **Step 5: Thread `repo_id` through `_execute_task()`**

`_execute_task()` (line 67) currently has this signature:
```python
async def _execute_task(
    self, db, runtime, worktree_mgr, merge_worker,
    task_id: str, agent_id: str, pipeline_id: str | None = None,
) -> None:
```

Add `repo_id: str = "default"` parameter and pass it to all internal methods that construct worktree paths.

- [ ] **Step 6: Verify no hardcoded paths remain**

```bash
grep -rn 'os.path.join.*".forge".*"worktrees"' forge/core/daemon_executor.py
```

Expected: zero matches.

- [ ] **Step 7: Run executor tests**

```bash
.venv/bin/python -m pytest forge/core/daemon_executor_test.py -x -v
```

---

## Chunk 4: Task Dispatch Routing — Critical

Updates `_dispatch_task()` to route each task to the correct repo's infrastructure.

### Task 6: Update `_dispatch_task()` for Multi-Repo

**Files:**
- Modify: `forge/core/daemon.py` — `_dispatch_task()` and callers in `execute()`
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing tests for dispatch routing**

```python
class TestDispatchTaskRouting:
    """Tests for _dispatch_task() multi-repo routing."""

    @pytest.mark.asyncio
    async def test_dispatch_task_routes_to_correct_repo(self, tmp_path):
        """task.repo_id selects the correct WorktreeManager and MergeWorker."""
        backend = tmp_path / "backend"
        backend.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=backend, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=backend, capture_output=True)

        repos = [RepoConfig(id="backend", path=str(backend), base_branch="main")]
        daemon = ForgeDaemon(str(tmp_path), repos=repos)
        await daemon._init_repos()
        daemon._setup_per_repo_infra("test-pipe")

        # Verify _get_repo_infra returns correct managers
        wt_mgr, merge_w, branch = daemon._get_repo_infra("backend")
        assert wt_mgr is daemon._worktree_managers["backend"]
        assert merge_w is daemon._merge_workers["backend"]

    @pytest.mark.asyncio
    async def test_dispatch_task_unknown_repo_raises(self, tmp_path):
        """Unknown repo_id raises ForgeError."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        daemon = ForgeDaemon(str(tmp_path))

        with pytest.raises(ForgeError, match="Unknown repo"):
            daemon._get_repo_infra("nonexistent")
```

- [ ] **Step 2: Implement `_get_repo_infra()` helper**

Add to `ForgeDaemon`:

```python
def _get_repo_infra(
    self, repo_id: str,
) -> tuple[WorktreeManager, MergeWorker, str]:
    """Look up per-repo infrastructure for a task's repo_id.

    Returns (worktree_manager, merge_worker, pipeline_branch).
    Raises ForgeError if repo_id is not found.
    """
    if repo_id not in self._repos:
        raise ForgeError(
            f"Unknown repo '{repo_id}'. Available: {sorted(self._repos.keys())}"
        )
    return (
        self._worktree_managers[repo_id],
        self._merge_workers[repo_id],
        self._pipeline_branches[repo_id],
    )
```

- [ ] **Step 3: Update dispatch in `execute()` to use `_get_repo_infra()`**

In the `execute()` method, where tasks are dispatched (currently passing a single `worktree_mgr` and `merge_worker`), change to:

```python
repo_id = task.repo if hasattr(task, 'repo') else "default"
wt_mgr, merge_worker, _branch = self._get_repo_infra(repo_id)

await self._execute_task(
    db, runtime, wt_mgr, merge_worker,
    task_id=task.id, agent_id=agent_id,
    pipeline_id=pipeline_id, repo_id=repo_id,
)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py::TestDispatchTaskRouting -x -v
```

---

## Chunk 5: Allowed Dirs & Pipeline Storage — Important

Cross-repo reading requires `allowed_dirs` to include all repo paths. Pipeline metadata must persist repo config.

### Task 7: Union of All Repo Paths in `allowed_dirs`

**Files:**
- Modify: `forge/core/daemon.py` — `execute()` method, SDK call site
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing test for allowed_dirs union**

```python
class TestAllowedDirs:
    """Tests for allowed_dirs multi-repo union."""

    def test_allowed_dirs_union(self, tmp_path):
        """All repo paths appear in effective allowed_dirs."""
        backend = tmp_path / "backend"
        frontend = tmp_path / "frontend"
        backend.mkdir()
        frontend.mkdir()
        subprocess.run(["git", "init"], cwd=backend, capture_output=True)
        subprocess.run(["git", "init"], cwd=frontend, capture_output=True)

        repos = [
            RepoConfig(id="backend", path=str(backend), base_branch="main"),
            RepoConfig(id="frontend", path=str(frontend), base_branch="main"),
        ]
        daemon = ForgeDaemon(str(tmp_path), repos=repos)

        allowed = daemon._build_allowed_dirs()
        assert str(backend) in allowed
        assert str(frontend) in allowed
```

- [ ] **Step 2: Implement `_build_allowed_dirs()`**

Add to `ForgeDaemon` (per spec Section 6.5):

```python
def _build_allowed_dirs(self) -> list[str]:
    """Build allowed_dirs including all repo paths for cross-repo reading."""
    effective = list(self._settings.allowed_dirs or [])
    for rc in self._repos.values():
        if rc.path not in effective:
            effective.append(rc.path)
    return effective
```

- [ ] **Step 3: Use `_build_allowed_dirs()` when constructing SDK calls**

In the `execute()` method and anywhere `allowed_dirs` is passed to `sdk_query()` or agent runtime, replace the current single-dir logic with:

```python
allowed_dirs = self._build_allowed_dirs()
```

- [ ] **Step 4: Run test**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py::TestAllowedDirs -x -v
```

### Task 8: Store `repos_json` in PipelineRow

**Files:**
- Modify: `forge/core/daemon.py` — `execute()` method where PipelineRow is created/updated
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing test for repos_json storage**

```python
class TestReposJsonStorage:
    """Tests for repos_json persistence in PipelineRow."""

    @pytest.mark.asyncio
    async def test_repos_json_stored_in_pipeline(self, tmp_path):
        """PipelineRow.repos_json is set when multi-repo."""
        import json

        backend = tmp_path / "backend"
        backend.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=backend, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=backend, capture_output=True)

        repos = [RepoConfig(id="backend", path=str(backend), base_branch="main")]
        daemon = ForgeDaemon(str(tmp_path), repos=repos)
        await daemon._init_repos()
        daemon._setup_per_repo_infra("pipe-001")

        repos_json = daemon._build_repos_json()
        parsed = json.loads(repos_json)

        assert len(parsed) == 1
        assert parsed[0]["id"] == "backend"
        assert parsed[0]["path"] == str(backend)
        assert parsed[0]["base_branch"] == "main"
        assert "branch_name" in parsed[0]
```

- [ ] **Step 2: Implement `_build_repos_json()`**

Add to `ForgeDaemon`:

```python
def _build_repos_json(self) -> str:
    """Serialize repo configs + pipeline branches for PipelineRow storage."""
    import json

    entries = []
    for repo_id, rc in self._repos.items():
        entries.append({
            "id": rc.id,
            "path": rc.path,
            "base_branch": rc.base_branch,
            "branch_name": self._pipeline_branches.get(repo_id, ""),
        })
    return json.dumps(entries)
```

- [ ] **Step 3: Wire into `execute()` when creating PipelineRow**

In `execute()`, when the PipelineRow is created or updated, set:

```python
pipeline_row.repos_json = self._build_repos_json() if len(self._repos) > 1 else None
```

For single-repo, `repos_json` stays `None` — backward compat with existing `project_dir`/`base_branch`/`branch_name` fields.

- [ ] **Step 4: Run test**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py::TestReposJsonStorage -x -v
```

---

## Chunk 6: Backward Compatibility & Failure Scenarios

Ensures single-repo behavior is unchanged and all failure modes from spec Section 15.2 are handled.

### Task 9: Single-Repo Backward Compatibility Verification

**Files:**
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Verify existing tests still pass**

No code changes — just run the full existing test suite to confirm nothing is broken:

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py -x -v
```

All existing tests must pass without modification. The key invariant: when `repos` is `None` (or has a single `"default"` entry), the daemon behaves identically to the pre-multi-repo version:
- Single `WorktreeManager` at `.forge/worktrees/`
- Single `MergeWorker` with auto-detected base branch
- Flat worktree layout (no `repo_id` nesting)
- `PipelineRow.repos_json` is `None`

### Task 10: Failure Scenario Coverage (Spec Section 15.2)

**Files:**
- Test: `forge/core/daemon_test.py`

The following failure scenarios from the spec must be addressed in daemon core:

- [ ] **Step 1: Dirty working tree rejection**

The `_init_repos()` method should check for uncommitted changes in each repo:

```python
async def _init_repos(self) -> None:
    """Resolve base branches and validate repo state."""
    from forge.core.daemon_helpers import _get_current_branch

    resolved: dict[str, RepoConfig] = {}
    for repo_id, rc in self._repos.items():
        # Check for dirty working tree
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=rc.path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if stdout.strip():
            raise ForgeError(
                f"Repo '{repo_id}' at {rc.path} has uncommitted changes. "
                "Commit or stash before running forge."
            )

        # Resolve base branch
        base = rc.base_branch
        if base == "auto":
            base = await _get_current_branch(rc.path)
        resolved[repo_id] = RepoConfig(id=rc.id, path=rc.path, base_branch=base)
    self._repos = resolved
```

- [ ] **Step 2: Partial failure isolation**

In `execute()`, when a task fails, only tasks with `depends_on` pointing to the failed task should be marked `BLOCKED`. Tasks in other repos without that dependency continue normally. This is already handled by the existing dependency graph logic — verify with a test:

```python
@pytest.mark.asyncio
async def test_partial_failure_does_not_block_other_repos(self, ...):
    """A failed task in repo A doesn't block independent tasks in repo B."""
    # ... setup two repos, two tasks (one per repo, no dependency) ...
    # Fail task in repo A, verify task in repo B still completes
```

- [ ] **Step 3: Unknown repo_id on task**

Already covered by `test_dispatch_task_unknown_repo_raises` in Task 6. The `_get_repo_infra()` method raises `ForgeError` for any `repo_id` not in `self._repos`.

- [ ] **Step 4: Run full test suite**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py forge/core/daemon_executor_test.py -x -v
```

---

## Final Verification

After all chunks are complete:

```bash
# Full test suite for modified modules
.venv/bin/python -m pytest forge/core/daemon_test.py forge/core/daemon_executor_test.py -x -v

# Verify no hardcoded worktree paths remain in executor
grep -rn 'os.path.join.*".forge".*"worktrees"' forge/core/daemon_executor.py
# Expected: zero matches

# Verify daemon.py only uses _worktree_path() (except the helper definition itself)
grep -rn 'os.path.join.*".forge".*"worktrees"' forge/core/daemon.py
# Expected: only in _worktree_path() definition and _setup_per_repo_infra()
```

---

## Test Summary

| Test Name | Location | Validates |
|-----------|----------|-----------|
| `test_daemon_init_with_repos` | `daemon_test.py::TestDaemonMultiRepoInit` | Multi-repo init creates per-repo dicts |
| `test_daemon_init_single_repo_default` | `daemon_test.py::TestDaemonMultiRepoInit` | No repos = single 'default' repo |
| `test_init_repos_resolves_base_branch` | `daemon_test.py::TestDaemonMultiRepoInit` | Async base branch resolution |
| `test_worktree_path_single_repo` | `daemon_test.py::TestWorktreePath` | Flat layout without repo_id nesting |
| `test_worktree_path_multi_repo` | `daemon_test.py::TestWorktreePath` | Nested layout with repo_id |
| `test_dispatch_task_routes_to_correct_repo` | `daemon_test.py::TestDispatchTaskRouting` | task.repo_id lookup |
| `test_dispatch_task_unknown_repo_raises` | `daemon_test.py::TestDispatchTaskRouting` | ForgeError on bad repo_id |
| `test_pipeline_branches_created_per_repo` | `daemon_test.py::TestDaemonPerRepoInfra` | git branch -f per repo |
| `test_allowed_dirs_union` | `daemon_test.py::TestAllowedDirs` | All repo paths in allowed_dirs |
| `test_repos_json_stored_in_pipeline` | `daemon_test.py::TestReposJsonStorage` | PipelineRow.repos_json set correctly |

---

## Commit Strategy

One commit per chunk:

1. `feat: add multi-repo init and base branch resolution to ForgeDaemon`
2. `feat: add per-repo WorktreeManager, MergeWorker, and pipeline branches`
3. `refactor: replace hardcoded worktree paths with _worktree_path() helper`
4. `feat: add multi-repo task dispatch routing via _get_repo_infra()`
5. `feat: add allowed_dirs union and repos_json pipeline storage`
6. `test: add backward compat and failure scenario coverage`
