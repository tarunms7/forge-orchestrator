"""Integration tests for resilient pipeline lifecycle flows.

Tests the key flows from the spec's Flow Matrix:
- Flow A: Full success path
- Flow B: Partial success path (some tasks fail)
- Flow C: Retry path (error/blocked -> todo -> execute)
- Flow F: Skip & Finish path
- Flow G/H: Quit + resume path
"""
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
