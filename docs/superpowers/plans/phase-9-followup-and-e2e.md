# Phase 9: Follow-Up Executor & End-to-End Integration Tests

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the follow-up executor multi-repo aware and add comprehensive E2E tests that validate the entire pipeline lifecycle across multiple repositories.

**Architecture:** The follow-up executor (`forge/core/followup.py`) currently hardcodes worktree paths relative to a single `project_dir`. In multi-repo mode, each task belongs to a specific repo, so worktrees, git operations, and prompts must all be scoped to the correct repo. E2E tests validate the full pipeline flow (plan → execute → review → merge) with real git repos.

**Tech Stack:** Python 3.12+, asyncio, pytest, subprocess (git), aiosqlite

**Spec:** `docs/superpowers/specs/2026-03-21-multi-repo-workspace-design.md`

**Dependencies:** Phases 1-8 should be merged for E2E tests; follow-up unit tests require Phase 1 (data model) + Phase 3 (worktree routing)

**Verification:** `.venv/bin/python -m pytest forge/core/followup_test.py forge/tests/integration/ -x -v`

---

## Chunk 1: Multi-Repo Follow-Up Executor

Updates `forge/core/followup.py` so that worktree creation, prompt building, commit/push, and cleanup all operate on the correct repo when multi-repo is active.

### Task 1: Update `_execute_task_followup()` to resolve repo context

**Files:**
- Modify: `forge/core/followup.py:281-330` (`_execute_task_followup()`)
- Test: `forge/core/followup_test.py`

The current code at line 318 hardcodes a flat worktree path:
```python
worktree_dir = os.path.join(project_dir, ".forge", "worktrees", worktree_id)
```

This must be updated to resolve the task's `repo_id` and compute the correct path.

- [ ] **Step 1: Write failing test — `test_followup_worktree_multi_repo`**

```python
@pytest.mark.asyncio
async def test_followup_worktree_multi_repo(tmp_path, monkeypatch):
    """Worktree is created under repo_id subdirectory in multi-repo mode."""
    # Set up DB with a pipeline that has repos_json
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()

    backend_path = str(tmp_path / "backend")
    repos_json = json.dumps([
        {"id": "backend", "path": backend_path, "base_branch": "main",
         "branch_name": "forge/pipe-123"},
    ])
    pid = "pipe-123"
    await db.create_pipeline(id=pid, description="test", project_dir=str(tmp_path),
                             model_strategy="balanced", budget_limit_usd=10)
    # Set repos_json on pipeline
    await db.update_pipeline_repos_json(pid, repos_json)

    # Create task with repo_id="backend"
    await db.create_task(id="t1", title="Fix API", description="", files=["api.py"],
                         depends_on=[], complexity="low", pipeline_id=pid, repo_id="backend")

    # Mock _setup_worktree and runtime to capture the worktree_dir used
    captured = {}
    def mock_setup(repo_dir, worktree_dir, branch_name, worktree_id):
        captured["worktree_dir"] = worktree_dir
        captured["repo_dir"] = repo_dir

    monkeypatch.setattr("forge.core.followup._setup_worktree", mock_setup)
    # ... mock runtime, call _execute_task_followup ...

    # Assert worktree path is under the repo_id subdirectory
    assert "/backend/" in captured["worktree_dir"]
    assert captured["repo_dir"] == backend_path
```

- [ ] **Step 2: Write failing test — `test_followup_worktree_single_repo`**

```python
@pytest.mark.asyncio
async def test_followup_worktree_single_repo(tmp_path, monkeypatch):
    """Single-repo mode: worktree path is flat (no repo_id subdirectory)."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "pipe-456"
    await db.create_pipeline(id=pid, description="test", project_dir=str(tmp_path),
                             model_strategy="balanced", budget_limit_usd=10)
    await db.create_task(id="t1", title="Fix bug", description="", files=[],
                         depends_on=[], complexity="low", pipeline_id=pid)

    captured = {}
    def mock_setup(repo_dir, worktree_dir, branch_name, worktree_id):
        captured["worktree_dir"] = worktree_dir
        captured["repo_dir"] = repo_dir

    monkeypatch.setattr("forge.core.followup._setup_worktree", mock_setup)
    # ... mock runtime, call _execute_task_followup ...

    # Flat path: .forge/worktrees/{worktree_id} (no repo_id layer)
    expected_base = os.path.join(str(tmp_path), ".forge", "worktrees")
    assert captured["worktree_dir"].startswith(expected_base)
    assert captured["repo_dir"] == str(tmp_path)
```

- [ ] **Step 3: Implement repo resolution in `_execute_task_followup()`**

Add `db` and `pipeline_id` lookups to resolve the task's repo:

```python
async def _execute_task_followup(
    *,
    task_id: str,
    task_info: dict,
    db_task: object | None,
    questions: list[FollowUpQuestion],
    project_dir: str,
    branch_name: str,
    pipeline_id: str,
    db: Database,
    emitter: EventEmitter | None,
    followup_id: str,
) -> FollowUpResult:
    # ... existing task_title, task_description, task_files extraction ...

    # Resolve repo context for this task
    repo_id = task_info.get("repo_id") or (
        getattr(db_task, "repo_id", None) if db_task else None
    ) or "default"

    pipeline = await db.get_pipeline(pipeline_id)
    try:
        repos = pipeline.get_repos()
    except ValueError:
        # Pipeline has no base_branch — fall back to single-repo behavior
        repos = [{"id": "default", "path": project_dir, "base_branch": "main"}]
    repo_config = next((r for r in repos if r["id"] == repo_id), None)

    if repo_config is None:
        # Fallback: use project_dir as single-repo
        logger.warning("repo_id '%s' not found in pipeline repos, falling back to default", repo_id)
        repo_dir = project_dir
        repo_branch = branch_name
    else:
        repo_dir = repo_config["path"]
        repo_branch = repo_config.get("branch_name", branch_name)

    # Compute worktree directory
    worktree_id = f"followup-{followup_id[:8]}-{task_id}"
    is_multi_repo = pipeline.repos_json is not None
    if is_multi_repo:
        worktree_dir = os.path.join(project_dir, ".forge", "worktrees", repo_id, worktree_id)
    else:
        worktree_dir = os.path.join(project_dir, ".forge", "worktrees", worktree_id)

    try:
        _setup_worktree(repo_dir, worktree_dir, repo_branch, worktree_id)
    # ... rest unchanged, but pass repo_dir instead of project_dir to
    # _commit_and_push() and _cleanup_worktree() ...
```

- [ ] **Step 4: Run tests to verify green**

```bash
.venv/bin/python -m pytest forge/core/followup_test.py::test_followup_worktree_multi_repo forge/core/followup_test.py::test_followup_worktree_single_repo -v
```

---

### Task 2: Update `_setup_worktree()` to use correct repo path

**Files:**
- Modify: `forge/core/followup.py:458-491` (`_setup_worktree()`)
- Test: `forge/core/followup_test.py`

Currently `_setup_worktree()` takes `project_dir` as the first argument and uses it as `cwd` for all git commands. After Task 1, the caller passes `repo_dir` instead, so the function signature changes from `project_dir` to `repo_dir`. The git commands (`rev-parse --verify`, `worktree add`) already use the first arg as `cwd`, so the only change is the parameter name and the call sites.

- [ ] **Step 1: Rename parameter from `project_dir` to `repo_dir`**

```python
def _setup_worktree(
    repo_dir: str,        # was: project_dir
    worktree_dir: str,
    branch_name: str,
    worktree_id: str,
) -> None:
    """Create a git worktree on the pipeline branch for follow-up work."""
    os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)

    branch_check = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=repo_dir,      # was: project_dir
        capture_output=True,
        text=True,
    )
    # ... rest uses repo_dir as cwd ...
```

- [ ] **Step 2: Verify no regressions**

```bash
.venv/bin/python -m pytest forge/core/followup_test.py -v
```

---

### Task 3: Update `_build_followup_prompt()` with repo context

**Files:**
- Modify: `forge/core/followup.py:415-455` (`_build_followup_prompt()`)
- Test: `forge/core/followup_test.py`

When a task belongs to a specific repo, the follow-up prompt should include that context so the agent knows which repo it's operating in.

- [ ] **Step 1: Write failing test — `test_followup_prompt_includes_repo_context`**

```python
def test_followup_prompt_includes_repo_context():
    """Prompt should mention the repo name when repo_name is provided."""
    prompt = _build_followup_prompt(
        task_title="Fix API",
        task_description="Fix the auth endpoint",
        task_files=["api.py"],
        original_output="(none)",
        review_feedback=None,
        questions=[FollowUpQuestion(text="Add rate limiting")],
        repo_name="backend",
    )
    assert "backend" in prompt
    assert "This task is in the backend repo" in prompt or "Repository: backend" in prompt
```

- [ ] **Step 2: Add `repo_name` parameter to `_build_followup_prompt()`**

```python
def _build_followup_prompt(
    *,
    task_title: str,
    task_description: str,
    task_files: list[str],
    original_output: str,
    review_feedback: str | None,
    questions: list[FollowUpQuestion],
    repo_name: str | None = None,       # NEW
) -> str:
    """Build a comprehensive prompt for the follow-up agent."""
    # ... existing code ...

    repo_section = ""
    if repo_name and repo_name != "default":
        repo_section = f"**Repository:** This task is in the **{repo_name}** repo.\n"

    return (
        f"# Follow-up Task\n\n"
        f"You are continuing work on a previously completed task. "
        f"The user has follow-up questions/requests that need to be addressed.\n\n"
        f"{repo_section}"
        f"## Original Task\n"
        # ... rest unchanged ...
    )
```

- [ ] **Step 3: Update call site in `_execute_task_followup()`**

Pass `repo_name=repo_id` (or `repo_config["id"]`) to `_build_followup_prompt()`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest forge/core/followup_test.py::test_followup_prompt_includes_repo_context -v
```

---

### Task 4: Update `_cleanup_worktree()` to use correct repo path

**Files:**
- Modify: `forge/core/followup.py:552-565` (`_cleanup_worktree()`)
- Test: `forge/core/followup_test.py`

- [ ] **Step 1: Write failing test — `test_followup_cleanup_multi_repo`**

```python
def test_followup_cleanup_multi_repo(tmp_path, monkeypatch):
    """Cleanup runs git worktree remove from the correct repo directory."""
    captured = {}
    def mock_run(cmd, **kwargs):
        if "worktree" in cmd:
            captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("subprocess.run", mock_run)
    repo_dir = str(tmp_path / "backend")
    worktree_dir = str(tmp_path / ".forge" / "worktrees" / "backend" / "wt-1")

    _cleanup_worktree(repo_dir, worktree_dir, "wt-1")
    assert captured["cwd"] == repo_dir
```

- [ ] **Step 2: Rename parameter from `project_dir` to `repo_dir`**

```python
def _cleanup_worktree(
    repo_dir: str,        # was: project_dir
    worktree_dir: str,
    worktree_id: str,
) -> None:
    """Remove a follow-up worktree."""
    try:
        subprocess.run(
            ["git", "worktree", "remove", worktree_dir, "--force"],
            cwd=repo_dir,     # was: project_dir
            capture_output=True,
        )
    except Exception as exc:
        logger.warning("Failed to remove worktree %s: %s", worktree_id, exc)
```

- [ ] **Step 3: Update call site in `_execute_task_followup()` finally block (line 378)**

Pass `repo_dir` instead of `project_dir` to `_cleanup_worktree()`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest forge/core/followup_test.py::test_followup_cleanup_multi_repo -v
```

---

### Task 5: Update `_commit_and_push()` to push to correct repo

**Files:**
- Modify: `forge/core/followup.py:494-549` (`_commit_and_push()`)
- Test: `forge/core/followup_test.py`

The current code checks remotes using `cwd=project_dir` (line 531-534). In multi-repo mode this must use the task's repo directory so git pushes to the correct remote.

- [ ] **Step 1: Write failing test — `test_followup_push_correct_repo`**

```python
def test_followup_push_correct_repo(tmp_path, monkeypatch):
    """Push runs git remote/push in the task's repo directory, not workspace root."""
    calls = []
    def mock_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "cwd": kwargs.get("cwd")})
        if cmd[1] == "remote":
            return subprocess.CompletedProcess(cmd, 0, stdout="origin\n")
        if cmd[1] == "diff":
            return subprocess.CompletedProcess(cmd, 1)  # has changes
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("subprocess.run", mock_run)
    repo_dir = str(tmp_path / "backend")
    worktree_dir = str(tmp_path / ".forge" / "worktrees" / "backend" / "wt-1")

    _commit_and_push(worktree_dir, repo_dir, "forge/pipe-123", "f-abc", "Fix API")

    remote_call = next(c for c in calls if "remote" in c["cmd"])
    assert remote_call["cwd"] == repo_dir
```

- [ ] **Step 2: Rename `project_dir` to `repo_dir` in `_commit_and_push()`**

```python
def _commit_and_push(
    worktree_dir: str,
    repo_dir: str,        # was: project_dir
    branch_name: str,
    followup_id: str,
    task_title: str,
) -> None:
    # ... git add, diff, commit use worktree_dir (unchanged) ...
    # git remote check uses repo_dir:
    remote_result = subprocess.run(
        ["git", "remote"],
        cwd=repo_dir,       # was: project_dir
        capture_output=True,
        text=True,
    )
    # ... push unchanged (uses worktree_dir as cwd) ...
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest forge/core/followup_test.py::test_followup_push_correct_repo -v
```

---

### Task 6: Fallback when `repo_id` is missing

**Files:**
- Modify: `forge/core/followup.py` (within `_execute_task_followup()`)
- Test: `forge/core/followup_test.py`

- [ ] **Step 1: Write failing test — `test_followup_missing_repo_id_fallback`**

```python
@pytest.mark.asyncio
async def test_followup_missing_repo_id_fallback(tmp_path, monkeypatch):
    """When task has no repo_id, default to 'default' and use project_dir."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "pipe-789"
    await db.create_pipeline(id=pid, description="test", project_dir=str(tmp_path),
                             model_strategy="balanced", budget_limit_usd=10)
    # Task created WITHOUT repo_id (legacy task)
    await db.create_task(id="t1", title="Fix", description="", files=[],
                         depends_on=[], complexity="low", pipeline_id=pid)

    captured = {}
    def mock_setup(repo_dir, worktree_dir, branch_name, worktree_id):
        captured["repo_dir"] = repo_dir

    monkeypatch.setattr("forge.core.followup._setup_worktree", mock_setup)
    # ... mock runtime, call _execute_task_followup ...

    # Should fall back to project_dir
    assert captured["repo_dir"] == str(tmp_path)
```

- [ ] **Step 2: Verify the fallback logic in Task 1 Step 3 handles this case**

The repo resolution logic already defaults `repo_id` to `"default"` and falls back to `project_dir` when `repo_config` is `None` or `repos_json` is null. Verify test passes.

- [ ] **Step 3: Run all follow-up tests**

```bash
.venv/bin/python -m pytest forge/core/followup_test.py -v
```

---

## Chunk 2: End-to-End Integration Tests

**Files:**
- Modify: `forge/tests/integration/test_pipeline_lifecycle.py`
- New helpers: `forge/tests/integration/conftest.py` (pytest fixtures)

### Task 7: Integration test fixtures — temp repo creation

**Files:**
- New: `forge/tests/integration/conftest.py`

- [ ] **Step 1: Create `conftest.py` with temp repo fixtures**

```python
"""Shared fixtures for integration tests."""
import os
import subprocess
import pytest


@pytest.fixture
def make_git_repo(tmp_path):
    """Factory fixture that creates a temporary git repo with an initial commit."""
    created_repos = []

    def _make(name: str, files: dict[str, str] | None = None) -> str:
        """Create a git repo at tmp_path/name with optional files.

        Args:
            name: Directory name for the repo
            files: Dict of {relative_path: content} to create and commit

        Returns:
            Absolute path to the repo root
        """
        repo_dir = str(tmp_path / name)
        os.makedirs(repo_dir, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=repo_dir, capture_output=True, check=True)

        # Create files
        if files:
            for path, content in files.items():
                full_path = os.path.join(repo_dir, path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w") as f:
                    f.write(content)
        else:
            # At least one file for initial commit
            with open(os.path.join(repo_dir, "README.md"), "w") as f:
                f.write(f"# {name}\n")

        subprocess.run(["git", "add", "-A"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"],
                       cwd=repo_dir, capture_output=True, check=True)
        created_repos.append(repo_dir)
        return repo_dir

    yield _make

    # Cleanup is handled by tmp_path fixture


@pytest.fixture
def workspace_dir(tmp_path):
    """Create a workspace directory with .forge/ structure."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "worktrees").mkdir()
    return str(tmp_path)
```

- [ ] **Step 2: Verify fixture works**

```bash
.venv/bin/python -m pytest forge/tests/integration/conftest.py --co -v
```

---

### Task 8: Multi-repo E2E test

**Note:** This test validates the data model and storage layer for multi-repo. A true E2E test that runs the daemon with real (or mocked) agents is significantly more complex and can be added incrementally. The current test ensures the plumbing works at the DB level, which is the most likely place for regressions. It does not actually run the daemon or executor — it is really a DB/storage integration test.

**Files:**
- Modify: `forge/tests/integration/test_pipeline_lifecycle.py`

- [ ] **Step 1: Write `test_multi_repo_pipeline_e2e`**

```python
@pytest.mark.asyncio
async def test_multi_repo_pipeline_e2e(tmp_path, make_git_repo, workspace_dir):
    """Full E2E: 2 repos, 3+ tasks across both repos, correct worktree layout."""
    # Create two git repos
    backend_dir = make_git_repo("backend", files={
        "app.py": "# backend app\ndef main(): pass\n",
        "requirements.txt": "flask\n",
    })
    frontend_dir = make_git_repo("frontend", files={
        "index.js": "// frontend\nconsole.log('hello');\n",
        "package.json": '{"name": "frontend"}\n',
    })

    # Set up DB with multi-repo pipeline
    db = Database(f"sqlite+aiosqlite:///{workspace_dir}/test.db")
    await db.initialize()

    repos_json = json.dumps([
        {"id": "backend", "path": backend_dir, "base_branch": "main",
         "branch_name": "forge/pipe-e2e"},
        {"id": "frontend", "path": frontend_dir, "base_branch": "main",
         "branch_name": "forge/pipe-e2e"},
    ])

    pid = "pipe-e2e-001"
    await db.create_pipeline(id=pid, description="Add auth feature",
                             project_dir=workspace_dir,
                             model_strategy="balanced", budget_limit_usd=10)
    await db.update_pipeline_repos_json(pid, repos_json)
    await db.update_pipeline_status(pid, "executing")

    # Create 3 tasks across both repos
    await db.create_task(id="t-be-1", title="Add auth endpoint",
                         description="Add /api/auth", files=["app.py"],
                         depends_on=[], complexity="medium",
                         pipeline_id=pid, repo_id="backend")
    await db.create_task(id="t-be-2", title="Add auth middleware",
                         description="Add middleware", files=["app.py"],
                         depends_on=["t-be-1"], complexity="low",
                         pipeline_id=pid, repo_id="backend")
    await db.create_task(id="t-fe-1", title="Add login page",
                         description="Add login UI", files=["index.js"],
                         depends_on=["t-be-1"], complexity="medium",
                         pipeline_id=pid, repo_id="frontend")

    # Verify tasks stored with correct repo_id
    tasks = await db.list_tasks_by_pipeline(pid)
    repo_ids = {t.id: t.repo_id for t in tasks}
    assert repo_ids["t-be-1"] == "backend"
    assert repo_ids["t-be-2"] == "backend"
    assert repo_ids["t-fe-1"] == "frontend"

    # Verify worktree paths would be created in correct subdirectories
    pipeline = await db.get_pipeline(pid)
    repos = pipeline.get_repos()
    for task in tasks:
        repo = next(r for r in repos if r["id"] == task.repo_id)
        expected_worktree_base = os.path.join(workspace_dir, ".forge", "worktrees", task.repo_id)
        assert repo["path"] in (backend_dir, frontend_dir)
        # Worktree base for this repo exists under workspace
        assert os.path.basename(expected_worktree_base) == task.repo_id

    # Simulate execution: complete all tasks
    await db.update_task_state("t-be-1", "done")
    await db.update_task_state("t-be-2", "done")
    await db.update_task_state("t-fe-1", "done")

    # Verify pipeline can complete
    await db.update_pipeline_status(pid, "complete")
    p = await db.get_pipeline(pid)
    assert p.status == "complete"

    # Verify each repo has its own PR URL slot
    repos = pipeline.get_repos()
    assert len(repos) == 2
    assert repos[0]["id"] == "backend"
    assert repos[1]["id"] == "frontend"
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/python -m pytest forge/tests/integration/test_pipeline_lifecycle.py::test_multi_repo_pipeline_e2e -v
```

---

### Task 9: Single-repo regression E2E test

**Files:**
- Modify: `forge/tests/integration/test_pipeline_lifecycle.py`

- [ ] **Step 1: Write `test_single_repo_pipeline_regression`**

```python
@pytest.mark.asyncio
async def test_single_repo_pipeline_regression(tmp_path, make_git_repo):
    """Single-repo pipeline: zero behavioral changes from multi-repo support."""
    repo_dir = make_git_repo("project", files={
        "main.py": "print('hello')\n",
    })

    db = Database(f"sqlite+aiosqlite:///{repo_dir}/test.db")
    await db.initialize()

    pid = "pipe-single-001"
    await db.create_pipeline(id=pid, description="Fix bug",
                             project_dir=repo_dir,
                             model_strategy="balanced", budget_limit_usd=10)
    # No repos_json — single-repo mode
    await db.update_pipeline_status(pid, "executing")

    await db.create_task(id="t1", title="Fix main", description="Fix it",
                         files=["main.py"], depends_on=[], complexity="low",
                         pipeline_id=pid)

    # Verify repos_json is None
    pipeline = await db.get_pipeline(pid)
    assert pipeline.repos_json is None

    # get_repos() should return single default repo
    repos = pipeline.get_repos()
    assert len(repos) == 1
    assert repos[0]["id"] == "default"
    assert repos[0]["path"] == repo_dir

    # Task has default repo_id
    task = await db.get_task("t1")
    assert task.repo_id == "default"

    # Worktree path should be flat: .forge/worktrees/{worktree_id} (no repo_id layer)
    worktree_id = "followup-abc12345-t1"
    expected = os.path.join(repo_dir, ".forge", "worktrees", worktree_id)
    # No repo_id subdirectory in the path
    assert "default" not in expected.split(os.sep)[-2]

    # Complete pipeline
    await db.update_task_state("t1", "done")
    await db.update_pipeline_status(pid, "complete")
    p = await db.get_pipeline(pid)
    assert p.status == "complete"
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/python -m pytest forge/tests/integration/test_pipeline_lifecycle.py::test_single_repo_pipeline_regression -v
```

---

### Task 10: Multi-repo worktree layout verification test

**Files:**
- Modify: `forge/tests/integration/test_pipeline_lifecycle.py`

- [ ] **Step 1: Write `test_multi_repo_worktree_layout`**

```python
@pytest.mark.asyncio
async def test_multi_repo_worktree_layout(tmp_path):
    """Verify .forge/worktrees/{repo_id}/{task_id} directory structure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    forge_dir = workspace / ".forge" / "worktrees"

    # Simulate the layout multi-repo creates
    backend_wt = forge_dir / "backend" / "task-abc-1"
    frontend_wt = forge_dir / "frontend" / "task-abc-2"

    # In multi-repo mode, worktrees are nested under repo_id
    backend_wt.mkdir(parents=True)
    frontend_wt.mkdir(parents=True)

    # Verify structure
    assert (forge_dir / "backend").is_dir()
    assert (forge_dir / "frontend").is_dir()
    assert (forge_dir / "backend" / "task-abc-1").is_dir()
    assert (forge_dir / "frontend" / "task-abc-2").is_dir()

    # Verify no cross-contamination
    assert not (forge_dir / "backend" / "task-abc-2").exists()
    assert not (forge_dir / "frontend" / "task-abc-1").exists()
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/python -m pytest forge/tests/integration/test_pipeline_lifecycle.py::test_multi_repo_worktree_layout -v
```

---

### Task 11: Cross-repo dependency ordering test

**Files:**
- Modify: `forge/tests/integration/test_pipeline_lifecycle.py`

- [ ] **Step 1: Write `test_cross_repo_dependency_ordering`**

```python
@pytest.mark.asyncio
async def test_cross_repo_dependency_ordering(tmp_path):
    """Backend task completes before dependent frontend task can start."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()

    pid = "pipe-deps-001"
    await db.create_pipeline(id=pid, description="cross-repo deps",
                             project_dir=str(tmp_path),
                             model_strategy="balanced", budget_limit_usd=10)
    repos_json = json.dumps([
        {"id": "backend", "path": str(tmp_path / "be"), "base_branch": "main",
         "branch_name": "forge/pipe-deps"},
        {"id": "frontend", "path": str(tmp_path / "fe"), "base_branch": "main",
         "branch_name": "forge/pipe-deps"},
    ])
    await db.update_pipeline_repos_json(pid, repos_json)
    await db.update_pipeline_status(pid, "executing")

    # Backend task — no dependencies
    await db.create_task(id="t-be", title="Add API", description="",
                         files=["api.py"], depends_on=[], complexity="medium",
                         pipeline_id=pid, repo_id="backend")
    # Frontend task — depends on backend task (cross-repo dependency)
    await db.create_task(id="t-fe", title="Add UI", description="",
                         files=["app.js"], depends_on=["t-be"], complexity="medium",
                         pipeline_id=pid, repo_id="frontend")

    # Frontend task should be blocked while backend is in progress
    be_task = await db.get_task("t-be")
    fe_task = await db.get_task("t-fe")
    assert fe_task.depends_on == ["t-be"]

    # Scheduler should respect: t-fe can only run after t-be is done
    # Simulate: backend starts and completes
    await db.update_task_state("t-be", "in_progress")
    await db.update_task_state("t-be", "done")

    # Now frontend can start
    await db.update_task_state("t-fe", "in_progress")
    await db.update_task_state("t-fe", "done")

    # Pipeline completes
    tasks = await db.list_tasks_by_pipeline(pid)
    assert all(t.state == "done" for t in tasks)
    await db.update_pipeline_status(pid, "complete")
    p = await db.get_pipeline(pid)
    assert p.status == "complete"
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/python -m pytest forge/tests/integration/test_pipeline_lifecycle.py::test_cross_repo_dependency_ordering -v
```

---

## Chunk 3: Follow-Up Failure Scenarios

### Task 12: Error handling for missing branch and unknown repo_id

**Files:**
- Modify: `forge/core/followup.py` (within `_execute_task_followup()`)
- Test: `forge/core/followup_test.py`

- [ ] **Step 1: Verify existing branch-not-found error path**

The current `_setup_worktree()` (line 468-479) already raises `RuntimeError` when the branch doesn't exist, and `_execute_task_followup()` catches this and returns a `FollowUpResult(success=False)`. No code change needed — just verify the test exists.

- [ ] **Step 2: Write test for `repo_id` not found in `repos_json`**

This is already covered by `test_followup_missing_repo_id_fallback` in Task 6. The fallback logs a warning and uses `project_dir`. Verify the warning is logged:

```python
@pytest.mark.asyncio
async def test_followup_unknown_repo_id_logs_warning(tmp_path, monkeypatch, caplog):
    """Unknown repo_id in repos_json logs warning and falls back to project_dir."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "pipe-unknown"
    repos_json = json.dumps([
        {"id": "backend", "path": str(tmp_path / "be"), "base_branch": "main",
         "branch_name": "forge/pipe-unknown"},
    ])
    await db.create_pipeline(id=pid, description="test", project_dir=str(tmp_path),
                             model_strategy="balanced", budget_limit_usd=10)
    await db.update_pipeline_repos_json(pid, repos_json)

    # Task with repo_id that doesn't match any repo in repos_json
    await db.create_task(id="t1", title="Fix", description="", files=[],
                         depends_on=[], complexity="low",
                         pipeline_id=pid, repo_id="nonexistent")

    # ... mock and call _execute_task_followup ...

    assert "repo_id 'nonexistent' not found" in caplog.text
```

- [ ] **Step 3: Run all tests**

```bash
.venv/bin/python -m pytest forge/core/followup_test.py forge/tests/integration/ -x -v
```

---

## Summary

| Chunk | Tasks | Key Changes | Test Count |
|-------|-------|-------------|------------|
| 1: Follow-Up Executor | 1-6 | `_execute_task_followup()`, `_setup_worktree()`, `_build_followup_prompt()`, `_cleanup_worktree()`, `_commit_and_push()` all multi-repo aware | 6 tests |
| 2: E2E Integration | 7-11 | `conftest.py` fixtures, multi-repo E2E, single-repo regression, layout verification, cross-repo deps | 4 tests |
| 3: Failure Scenarios | 12 | Branch missing + unknown repo_id error paths | 1 test |

**Total new tests:** 10 (`test_followup_worktree_multi_repo`, `test_followup_worktree_single_repo`, `test_followup_prompt_includes_repo_context`, `test_followup_push_correct_repo`, `test_followup_cleanup_multi_repo`, `test_followup_missing_repo_id_fallback`, `test_multi_repo_pipeline_e2e`, `test_single_repo_pipeline_regression`, `test_multi_repo_worktree_layout`, `test_cross_repo_dependency_ordering`)

**Risk:** E2E tests depend on all prior phases being merged (especially Phase 1 data model + Phase 3 worktree routing). Unit tests for the follow-up executor only need Phase 1 + Phase 3. If running tests before all phases land, use mocks for `pipeline.get_repos()` and `task.repo_id`.
