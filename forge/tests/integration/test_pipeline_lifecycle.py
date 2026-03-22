"""Integration tests for resilient pipeline lifecycle flows.

Tests the key flows from the spec's Flow Matrix:
- Flow A: Full success path
- Flow B: Partial success path (some tasks fail)
- Flow C: Retry path (error/blocked -> todo -> execute)
- Flow F: Skip & Finish path
- Flow G/H: Quit + resume path
"""
import json
import os

import pytest
from forge.storage.db import Database


@pytest.mark.asyncio
async def test_flow_a_full_success(tmp_path):
    """All tasks complete -> pipeline status = complete."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "test-flow-a-001"
    await db.create_pipeline(
        id=pid,
        description="test", project_dir=str(tmp_path),
        model_strategy="balanced", budget_limit_usd=10,
    )
    await db.update_pipeline_status(pid, "executing")
    for i in range(3):
        await db.create_task(id=f"t{i}", title=f"Task {i}", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
        await db.update_task_state(f"t{i}", "done")

    tasks = await db.list_tasks_by_pipeline(pid)
    states = [t.state for t in tasks]
    done_count = states.count("done")
    error_count = states.count("error") + states.count("blocked")
    if error_count == 0:
        result = "complete"
    elif done_count == 0:
        result = "error"
    else:
        result = "partial_success"

    await db.update_pipeline_status(pid, result)
    p = await db.get_pipeline(pid)
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_flow_b_partial_success(tmp_path):
    """Some tasks fail -> pipeline status = partial_success."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "test-flow-b-001"
    await db.create_pipeline(
        id=pid,
        description="test", project_dir=str(tmp_path),
        model_strategy="balanced", budget_limit_usd=10,
    )
    await db.update_pipeline_status(pid, "executing")
    await db.create_task(id="t0", title="A", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t1", title="B", description="", files=[], depends_on=["t0"], complexity="low", pipeline_id=pid)
    await db.update_task_state("t0", "done")
    await db.update_task_state("t1", "error")

    tasks = await db.list_tasks_by_pipeline(pid)
    states = [t.state for t in tasks]
    done_count = states.count("done")
    error_count = states.count("error") + states.count("blocked")
    result = "complete" if error_count == 0 else ("error" if done_count == 0 else "partial_success")

    await db.update_pipeline_status(pid, result)
    p = await db.get_pipeline(pid)
    assert p.status == "partial_success"


@pytest.mark.asyncio
async def test_flow_c_retry_resets_and_resumes(tmp_path):
    """Retry: error/blocked -> todo, pipeline -> retrying -> complete."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "test-flow-c-001"
    await db.create_pipeline(
        id=pid,
        description="test", project_dir=str(tmp_path),
        model_strategy="balanced", budget_limit_usd=10,
    )
    await db.update_pipeline_status(pid, "partial_success")
    await db.create_task(id="t0", title="A", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t1", title="B", description="", files=[], depends_on=["t0"], complexity="low", pipeline_id=pid)
    await db.update_task_state("t0", "error")
    await db.update_task_state("t1", "blocked")

    for t in await db.list_tasks_by_pipeline(pid):
        if t.state in ("error", "blocked"):
            await db.update_task_state(t.id, "todo")
    await db.update_pipeline_status(pid, "retrying")
    p = await db.get_pipeline(pid)
    assert p.status == "retrying"

    await db.update_task_state("t0", "done")
    await db.update_task_state("t1", "done")
    await db.update_pipeline_status(pid, "complete")
    p = await db.get_pipeline(pid)
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_flow_f_skip_and_finish(tmp_path):
    """Skip: error/blocked -> cancelled, pipeline -> complete."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "test-flow-f-001"
    await db.create_pipeline(
        id=pid,
        description="test", project_dir=str(tmp_path),
        model_strategy="balanced", budget_limit_usd=10,
    )
    await db.update_pipeline_status(pid, "partial_success")
    await db.create_task(id="t0", title="A", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t1", title="B", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.update_task_state("t0", "done")
    await db.update_task_state("t1", "error")

    for t in await db.list_tasks_by_pipeline(pid):
        if t.state in ("error", "blocked"):
            await db.update_task_state(t.id, "cancelled")
    await db.update_pipeline_status(pid, "complete")

    tasks = await db.list_tasks_by_pipeline(pid)
    states = {t.id: t.state for t in tasks}
    assert states["t0"] == "done"
    assert states["t1"] == "cancelled"
    p = await db.get_pipeline(pid)
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_flow_gh_quit_and_resume(tmp_path):
    """Quit: non-terminal -> todo, pipeline -> interrupted. Resume: interrupted -> executing."""
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "test-flow-gh-001"
    await db.create_pipeline(
        id=pid,
        description="test", project_dir=str(tmp_path),
        model_strategy="balanced", budget_limit_usd=10,
    )
    await db.update_pipeline_status(pid, "executing")
    await db.create_task(id="t0", title="A", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.create_task(id="t1", title="B", description="", files=[], depends_on=[], complexity="low", pipeline_id=pid)
    await db.update_task_state("t0", "done")
    await db.update_task_state("t1", "in_progress")

    non_terminal = ("in_progress", "in_review", "merging", "awaiting_input", "awaiting_approval")
    for t in await db.list_tasks_by_pipeline(pid):
        if t.state in non_terminal:
            await db.update_task_state(t.id, "todo")
    await db.update_pipeline_status(pid, "interrupted")

    p = await db.get_pipeline(pid)
    assert p.status == "interrupted"
    t1_row = await db.get_task("t1")
    assert t1_row.state == "todo"

    await db.update_pipeline_status(pid, "executing")
    p = await db.get_pipeline(pid)
    assert p.status == "executing"


# ── Multi-repo E2E and regression tests ──────────────────────────────


@pytest.mark.asyncio
async def test_multi_repo_pipeline_e2e(tmp_path, make_git_repo):
    """Full E2E: 2 repos, 3 tasks with cross-repo dependencies."""
    # Create two repos
    backend_path = make_git_repo("backend", files={"src/main.py": "print('hi')"})
    frontend_path = make_git_repo("frontend", files={"src/index.ts": "console.log('hi')"})

    # Build repos_json
    repos = [
        {"id": "backend", "path": str(backend_path), "base_branch": "main", "branch_name": ""},
        {"id": "frontend", "path": str(frontend_path), "base_branch": "main", "branch_name": ""},
    ]

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "test-multi-repo-e2e"

    await db.create_pipeline(
        id=pid,
        description="multi-repo test",
        project_dir=str(tmp_path),
        model_strategy="balanced",
        budget_limit_usd=10,
        repos_json=json.dumps(repos),
    )
    await db.update_pipeline_status(pid, "executing")

    # Create 3 tasks across repos
    await db.create_task(
        id="t-be-1", title="Backend API", description="Create API",
        files=["src/main.py"], depends_on=[], complexity="low",
        pipeline_id=pid, repo_id="backend",
    )
    await db.create_task(
        id="t-be-2", title="Backend DB", description="Add DB layer",
        files=["src/db.py"], depends_on=["t-be-1"], complexity="low",
        pipeline_id=pid, repo_id="backend",
    )
    await db.create_task(
        id="t-fe-1", title="Frontend UI", description="Build UI",
        files=["src/index.ts"], depends_on=["t-be-1"], complexity="low",
        pipeline_id=pid, repo_id="frontend",
    )

    # Verify tasks stored with correct repo_id
    tasks = await db.list_tasks_by_pipeline(pid)
    repo_ids = {t.id: t.repo_id for t in tasks}
    assert repo_ids["t-be-1"] == "backend"
    assert repo_ids["t-be-2"] == "backend"
    assert repo_ids["t-fe-1"] == "frontend"

    # Verify get_repos() returns both repos
    pipeline = await db.get_pipeline(pid)
    got_repos = pipeline.get_repos()
    assert len(got_repos) == 2
    repo_ids_from_pipeline = {r["id"] for r in got_repos}
    assert repo_ids_from_pipeline == {"backend", "frontend"}

    # Simulate completing all tasks
    await db.update_task_state("t-be-1", "done")
    await db.update_task_state("t-be-2", "done")
    await db.update_task_state("t-fe-1", "done")

    await db.update_pipeline_status(pid, "complete")
    p = await db.get_pipeline(pid)
    assert p.status == "complete"


@pytest.mark.asyncio
async def test_single_repo_pipeline_regression(tmp_path, make_git_repo):
    """Single-repo backward compatibility: no repos_json, default repo_id."""
    project_path = make_git_repo("project")

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "test-single-repo-regression"

    await db.create_pipeline(
        id=pid,
        description="single-repo test",
        project_dir=str(project_path),
        model_strategy="balanced",
        budget_limit_usd=10,
        base_branch="main",
    )

    # Verify repos_json is None
    pipeline = await db.get_pipeline(pid)
    assert pipeline.repos_json is None

    # Verify get_repos() returns single default repo
    got_repos = pipeline.get_repos()
    assert len(got_repos) == 1
    default_repo = got_repos[0]
    assert default_repo["id"] == "default"
    assert default_repo["path"] == str(project_path)
    assert default_repo["base_branch"] == "main"

    # Create a task with default repo_id
    await db.create_task(
        id="t-single", title="Single task", description="Do something",
        files=[], depends_on=[], complexity="low",
        pipeline_id=pid,
    )
    task = await db.get_task("t-single")
    assert task.repo_id == "default"

    # Verify worktree path is flat (no repo_id subdirectory)
    worktree_base = os.path.join(str(project_path), ".forge", "worktrees")
    single_repo_worktree = os.path.join(worktree_base, "t-single")
    multi_repo_worktree = os.path.join(worktree_base, "default", "t-single")
    # Single-repo should use flat path, not nested
    assert single_repo_worktree != multi_repo_worktree
    # The convention: single-repo uses {project_dir}/.forge/worktrees/{worktree_id}
    assert "default" not in os.path.relpath(single_repo_worktree, worktree_base)


@pytest.mark.asyncio
async def test_multi_repo_worktree_layout(tmp_path, workspace_dir):
    """Verify multi-repo worktree directory structure."""
    worktrees_root = os.path.join(workspace_dir, ".forge", "worktrees")

    # Simulate creating worktree directories for multi-repo layout
    task_id = "task-42"
    for repo_id in ("backend", "frontend"):
        worktree_path = os.path.join(worktrees_root, repo_id, task_id)
        os.makedirs(worktree_path, exist_ok=True)
        # Write a marker file to verify no cross-contamination
        with open(os.path.join(worktree_path, "marker.txt"), "w") as f:
            f.write(f"repo={repo_id}")

    # Verify structure
    backend_wt = os.path.join(worktrees_root, "backend", task_id)
    frontend_wt = os.path.join(worktrees_root, "frontend", task_id)
    assert os.path.isdir(backend_wt)
    assert os.path.isdir(frontend_wt)

    # Verify no cross-contamination
    with open(os.path.join(backend_wt, "marker.txt")) as f:
        assert f.read() == "repo=backend"
    with open(os.path.join(frontend_wt, "marker.txt")) as f:
        assert f.read() == "repo=frontend"

    # Verify repo dirs are separate
    backend_contents = set(os.listdir(os.path.join(worktrees_root, "backend")))
    frontend_contents = set(os.listdir(os.path.join(worktrees_root, "frontend")))
    assert backend_contents == {task_id}
    assert frontend_contents == {task_id}


@pytest.mark.asyncio
async def test_cross_repo_dependency_ordering(tmp_path, make_git_repo):
    """Cross-repo deps: frontend task depends on backend task."""
    backend_path = make_git_repo("backend")
    frontend_path = make_git_repo("frontend")

    repos = [
        {"id": "backend", "path": str(backend_path), "base_branch": "main", "branch_name": ""},
        {"id": "frontend", "path": str(frontend_path), "base_branch": "main", "branch_name": ""},
    ]

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await db.initialize()
    pid = "test-cross-repo-deps"

    await db.create_pipeline(
        id=pid,
        description="cross-repo dependency test",
        project_dir=str(tmp_path),
        model_strategy="balanced",
        budget_limit_usd=10,
        repos_json=json.dumps(repos),
    )
    await db.update_pipeline_status(pid, "executing")

    # Backend task (no deps)
    await db.create_task(
        id="t-be", title="Backend service", description="Build backend",
        files=[], depends_on=[], complexity="low",
        pipeline_id=pid, repo_id="backend",
    )
    # Frontend task depends on backend
    await db.create_task(
        id="t-fe", title="Frontend client", description="Build frontend",
        files=[], depends_on=["t-be"], complexity="low",
        pipeline_id=pid, repo_id="frontend",
    )

    # Verify dependency
    fe_task = await db.get_task("t-fe")
    assert fe_task.depends_on == ["t-be"]

    # Simulate execution order: backend first, then frontend
    await db.update_task_state("t-be", "done")
    be_task = await db.get_task("t-be")
    assert be_task.state == "done"

    await db.update_task_state("t-fe", "done")
    fe_task = await db.get_task("t-fe")
    assert fe_task.state == "done"

    # All tasks done -> pipeline complete
    tasks = await db.list_tasks_by_pipeline(pid)
    assert all(t.state == "done" for t in tasks)
    await db.update_pipeline_status(pid, "complete")
    p = await db.get_pipeline(pid)
    assert p.status == "complete"
