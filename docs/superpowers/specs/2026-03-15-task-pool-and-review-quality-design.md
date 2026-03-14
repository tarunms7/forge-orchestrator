# Task Pool Execution & Review Quality Overhaul

**Date**: 2026-03-15
**Status**: Draft
**Scope**: Two independent fixes shipped together

---

## Problem 1: Parallelism Is Fake — `asyncio.gather` Blocks the Scheduler

### Root Cause

`daemon.py:778-781` dispatches tasks via `asyncio.gather`:

```python
results = await asyncio.gather(*[
    self._execute_task(..., task_id, agent_id, ...)
    for task_id, agent_id in dispatch_plan
], return_exceptions=True)
```

`gather` blocks until ALL tasks in the batch complete. If task1 fails review at T=30s and goes back to `"todo"`, it cannot be re-dispatched until task2 (still building at T=30s) finishes. The scheduler loop is stuck at `await asyncio.gather(...)`.

With `max_agents=2`, the entire batch IS the full pipeline — there's never a "next iteration" that could pick up the retried task while another runs.

### Solution: Continuous Task Pool

Replace `asyncio.gather` with a `dict[str, asyncio.Task]` tracking active tasks. The loop polls continuously — on each tick it reaps completed tasks, handles results, and dispatches new work into free slots. No batch boundaries.

**Data structure:**

```python
_active_tasks: dict[str, asyncio.Task] = {}  # task_id → asyncio.Task
```

**Loop structure (replaces lines 774-813):**

The existing loop body (lines 648-772) is preserved in full: watchdog timeout, pause flag check, task listing, question-timeout checker, all-parked termination check, pipeline pause tracking, backpressure check, and dispatch plan computation. The ONLY change is what happens after `dispatch_plan` is computed (lines 774-813). That section is replaced with:

```python
# 1. Reap completed tasks (non-blocking check)
done_ids = [
    tid for tid, atask in _active_tasks.items() if atask.done()
]
for tid in done_ids:
    atask = _active_tasks.pop(tid)
    exc = atask.exception() if not atask.cancelled() else None
    if exc:
        await self._handle_task_exception(tid, exc, db, worktree_mgr, pipeline_id)

# 2. All existing loop logic preserved:
#    - Watchdog timeout (lines 649-661)
#    - Pause flag check (lines 663-669)
#    - Task listing + status table (lines 671-672)
#    - Question timeout checker (lines 674-679)
#    - All-parked termination check (lines 681-712) — see Loop Exit below
#    - Pipeline pause tracking (lines 714-747)
#    - Backpressure check (lines 749-753)
#    - Dispatch plan computation (lines 755-759)

# 3. Guard: skip tasks already in the pool (race condition prevention)
#    After _handle_retry sets a task to "todo" and releases the agent,
#    the old coroutine may still be winding down. The scheduler sees the
#    task as TODO and includes it in dispatch_plan, but the old asyncio.Task
#    is still in _active_tasks. This guard prevents double-dispatch.
dispatch_plan = [
    (tid, aid) for tid, aid in dispatch_plan
    if tid not in _active_tasks
]

# 4. Cap to actual free slots.
#    IMPORTANT: The pool size (len(_active_tasks)) is the AUTHORITATIVE
#    constraint, not the DB agent state used by Scheduler.dispatch_plan().
#    The scheduler's DB-based slot count can be stale (e.g., agent released
#    in CancelledError path but task not yet reaped from pool). This cap
#    is NOT redundant — do NOT remove it even though dispatch_plan already
#    limits by max_agents internally.
available_slots = max(0, self._settings.max_agents - len(_active_tasks))
dispatch_plan = dispatch_plan[:available_slots]

# 5. Launch into pool
for task_id, agent_id in dispatch_plan:
    await db.assign_task(task_id, agent_id)
    await db.update_task_state(task_id, TaskState.IN_PROGRESS.value)
    atask = asyncio.create_task(
        self._safe_execute_task(db, runtime, worktree_mgr, merge_worker,
                                task_id, agent_id, pipeline_id=pipeline_id),
        name=f"forge-task-{task_id}",
    )
    _active_tasks[task_id] = atask

# 6. Wait efficiently — use asyncio.wait with short timeout when tasks active
if _active_tasks:
    _done, _pending = await asyncio.wait(
        _active_tasks.values(),
        timeout=self._settings.scheduler_poll_interval,
        return_when=asyncio.FIRST_COMPLETED,
    )
    # Don't process _done here — reap loop at top handles it uniformly
else:
    await asyncio.sleep(self._settings.scheduler_poll_interval)
```

**`_safe_execute_task` wrapper (new method on daemon):**

Wraps `_execute_task` with `try/finally` to guarantee cleanup on cancellation or unhandled exceptions. This is critical because `asyncio.Task.cancel()` raises `CancelledError` inside the coroutine — without a handler, the agent slot and worktree leak.

```python
async def _safe_execute_task(
    self, db, runtime, worktree_mgr, merge_worker,
    task_id: str, agent_id: str, pipeline_id: str | None = None,
) -> None:
    """Wrapper ensuring cleanup on cancellation or crash.

    Guarantees agent release on ALL exit paths (normal return, exception,
    cancellation). The agent is released here rather than deferring to
    the reap loop, because a transient DB error in the reap loop would
    otherwise leak the agent permanently.
    """
    try:
        await self._execute_task(
            db, runtime, worktree_mgr, merge_worker,
            task_id, agent_id, pipeline_id=pipeline_id,
        )
    except asyncio.CancelledError:
        logger.info("Task %s was cancelled (shutdown)", task_id)
        raise  # Re-raise so asyncio marks the Task as cancelled
    except Exception:
        # Re-raise so atask.exception() captures it for the reap loop.
        raise
    finally:
        # Release agent on ALL exit paths. For normal completion,
        # _execute_task's own code paths typically release the agent
        # already (via _handle_retry, _attempt_merge, etc.), so this
        # is a safety net — release_agent is idempotent (IDLE→IDLE is
        # a no-op in the DB layer).
        try:
            await db.release_agent(agent_id)
        except Exception:
            logger.warning("Failed to release agent %s for task %s", agent_id, task_id)
```

**`_handle_task_exception` (extracted helper, replaces inline lines 783-813):**

```python
async def _handle_task_exception(
    self, task_id: str, exc: BaseException,
    db, worktree_mgr, pipeline_id: str | None,
) -> None:
    """Handle a task that raised an unhandled exception in the pool."""
    logger.error("Task %s raised unhandled exception: %s", task_id, exc, exc_info=exc)
    try:
        await db.update_task_state(task_id, TaskState.ERROR.value)
        await self._emit("task:state_changed", {
            "task_id": task_id, "state": "error", "error": str(exc),
        }, db=db, pipeline_id=pipeline_id or "")
    except Exception:
        logger.exception("Failed to mark crashed task %s as error", task_id)
    # Release agent — get agent_id from task record
    try:
        task_rec = await db.get_task(task_id)
        if task_rec and task_rec.assigned_agent:
            await db.release_agent(task_rec.assigned_agent)
    except Exception:
        pass
    try:
        worktree_mgr.remove(task_id)
    except Exception as cleanup_err:
        logger.warning("Failed to clean up worktree for task %s: %s", task_id, cleanup_err)

    # Pipeline-error detection: if all remaining tasks are now terminal,
    # emit pipeline:error so the web UI / CLI knows the pipeline is done.
    if pipeline_id:
        try:
            remaining = await db.list_tasks_by_pipeline(pipeline_id)
            terminal = (TaskState.DONE.value, TaskState.ERROR.value, TaskState.CANCELLED.value)
            if all(t.state in terminal for t in remaining):
                await self._emit("pipeline:error", {
                    "error": f"Pipeline failed: task {task_id} crashed",
                }, db=db, pipeline_id=pipeline_id)
        except Exception:
            logger.exception("Failed to check pipeline state after task %s crash", task_id)
```

**Loop exit conditions:**

The loop exits when:
1. **All tasks terminal** — the existing `all_parked` check (lines 681-712) still runs every tick. When all tasks are done/error/cancelled (and none awaiting approval), the loop breaks.
2. **Watchdog timeout** — existing check at lines 649-661.
3. **No dispatchable work and nothing running** — existing check at lines 761-768. With the pool, "nothing running" means `not dispatch_plan and not _active_tasks` (replaces the current `not any(t.state == IN_PROGRESS)` check, which relied on DB state). The pool size is the ground truth.

On loop exit, shutdown handling runs (see below).

**Shutdown handling:**

When the loop exits (all tasks terminal, watchdog timeout, or pipeline error):

```python
# Cancel all active tasks
for atask in _active_tasks.values():
    atask.cancel()
# Wait with grace period — _safe_execute_task handles CancelledError cleanup
if _active_tasks:
    await asyncio.gather(*_active_tasks.values(), return_exceptions=True)
_active_tasks.clear()
```

`_safe_execute_task`'s `except CancelledError` handler releases the agent. `gather` with `return_exceptions=True` ensures we wait for all coroutines to finish their cleanup. No need for per-task exception checking here — the `CancelledError` path is clean, and any other exceptions were already handled by the reap loop during normal operation.

### Edge Cases

| Scenario | Handling |
|----------|----------|
| Task completes between reap and dispatch | Reaped next tick (at `asyncio.wait` timeout or `FIRST_COMPLETED`) |
| Task raises unhandled exception | `_safe_execute_task` re-raises; reap loop calls `_handle_task_exception` (marks ERROR, releases agent, cleans worktree) |
| Shutdown during active tasks | Cancel all → `_safe_execute_task` catches `CancelledError`, releases agent → `gather` waits for cleanup |
| Backpressure | Still checked before dispatch; running tasks continue unaffected |
| Task retried while others active | `_handle_retry` sets to "todo" → reaped next tick → scheduler sees free slot → dispatched immediately |
| Race: task in pool AND in dispatch_plan | `tid not in _active_tasks` guard prevents double-dispatch |
| Agent released but task still in pool | Can't happen — `_safe_execute_task` stays in pool until coroutine returns |
| Same task dispatched twice | Can't happen — scheduler only dispatches `TODO` tasks, we set `IN_PROGRESS` before launching, AND the `_active_tasks` guard catches edge cases |
| `_execute_task` raises before worktree setup | `_safe_execute_task` catches; reap loop marks ERROR and releases agent |
| Cancelled task in reap loop | `atask.cancelled()` → skipped (no ERROR, no cleanup). This is intentional: cancellation only happens during shutdown, where the shutdown handler does its own cleanup. If a future "cancel task" API is added, the reap loop must be updated to handle cancelled tasks explicitly. |

### Config Change

`max_agents` default stays at `2`. The existing comment correctly notes memory concerns on 16GB machines. Users who want more parallelism can set `FORGE_MAX_AGENTS=4` via environment variable. The pool design works with any `max_agents` value — the improvement is in continuous scheduling, not in slot count.

### Files Changed

- `forge/core/daemon.py` — replace `asyncio.gather` section with task pool (lines ~774-813), add `_safe_execute_task` wrapper, extract `_handle_task_exception` helper, update loop exit condition to check `_active_tasks`
- No changes to `forge/config/settings.py` (keep `max_agents=2`)

---

## Problem 2: LLM Reviewer Rubber-Stamps Code

### Root Cause

Two compounding issues:

1. **Sparse system prompt** — the current `REVIEW_SYSTEM_PROMPT` is 5 vague bullet points. No concrete checklist. The reviewer has no structured rubric to follow, so it does surface-level checks.

2. **Retry suppression** — the re-review prompt says: "Your PRIMARY job is to verify that the specific issues above were actually fixed... Do NOT invent new stylistic complaints — focus on the prior feedback." This turns the reviewer into a checkbox verifier on retries rather than a code reviewer.

3. **Missing separator** — `custom_review_focus` is concatenated directly without `"\n\n"` separator (line 102: `system_prompt += custom_review_focus`), which can cause the LLM to misparse the boundary between the base prompt and custom focus.

### Solution: Comprehensive Review Prompt + Full Review Every Time

**New `REVIEW_SYSTEM_PROMPT`:**

```
You are a senior code reviewer. Your job is to catch bugs, security issues,
and design problems that would cause production incidents. You are the last
line of defense before code ships.

You will receive a task specification and a git diff. Review the code
thoroughly and respond with EXACTLY one of:

PASS: <explanation covering what you verified>
FAIL: <specific issues with file paths and line references>

## Review Checklist (evaluate ALL categories)

1. CORRECTNESS
   - Does the code actually implement what the task spec requires?
   - Are there logic errors, off-by-one errors, or wrong conditions?
   - Are return values and error states handled correctly?
   - Do edge cases work (empty inputs, None values, boundary conditions)?

2. ERROR HANDLING
   - Are exceptions caught at the right level (not too broad, not missing)?
   - Do error paths clean up resources (files, connections, locks)?
   - Are error messages useful for debugging (not swallowed silently)?

3. SECURITY
   - Is user input validated/sanitized before use?
   - Are secrets handled safely (not logged, not in URLs, not hardcoded)?
   - Are file paths validated (no path traversal)?
   - Are permissions checked where needed?

4. CONCURRENCY & STATE
   - Are shared resources protected from race conditions?
   - Are async operations awaited properly?
   - Is mutable state handled safely across concurrent access?

5. DESIGN QUALITY
   - Is the code doing what it should at the right abstraction level?
   - Are functions/methods focused (single responsibility)?
   - Are there obvious performance issues (N+1 queries, unbounded loops)?

## Rules
- Be thorough. A missed bug in review means a production incident.
- Be specific. Reference exact file paths and line numbers.
- Do NOT pass code just because it "mostly works." If there are real issues, FAIL it.
- Do NOT nitpick pure style preferences (variable naming, import ordering) when
  no linter flags them. Focus on things that affect correctness and reliability.
- If a "Pipeline Task Context" section lists sibling tasks and their file scopes,
  do NOT fail for missing integration code that belongs to a sibling task's scope.
```

**`custom_review_focus` separator fix** in `gate2_llm_review`:

```python
# Before (line 102):
system_prompt += custom_review_focus

# After:
system_prompt += "\n\n" + custom_review_focus
```

**Retry prompt change** in `_build_review_prompt`:

Replace lines 208-226 (the prior feedback section + the 4-point "PRIMARY job" instruction) with:

```
=== PRIOR REVIEW CONTEXT ===
A previous reviewer rejected this code with the following feedback:
---
{prior_feedback}
---

The developer has attempted to fix these issues.
Verify the specific issues above were addressed, AND do a full review of the
current code. If you find new genuine issues (bugs, security, error handling),
FAIL — regardless of whether they were in the prior feedback or not.
Prior feedback is context, not a ceiling on what you can flag.
```

**Delta diff context change** — replace the directive "Focus your review on these delta changes" with neutral context:

```
=== CHANGES SINCE LAST REVIEW (DELTA) ===
These are the changes the developer made in this retry attempt, shown for context.
The full diff above shows the complete current state.
```

**Retry loop mitigation** — a stricter reviewer increases the risk of infinite FAIL loops where the reviewer keeps finding new issues on each retry. Existing safeguards:

1. `max_retries` setting (default 5) already caps retry count in `_handle_retry`
2. `no_changes_on_retry` auto-pass — if the agent makes zero changes, the review auto-passes (the agent believes prior feedback was already addressed)

These are sufficient. The retry cap ensures eventual termination, and the auto-pass prevents the degenerate case where the agent has nothing left to fix but the reviewer keeps finding new issues. No additional mitigation needed.

### What Stays The Same

- `custom_review_focus` injection — contracts and template focus still append to system prompt (with proper separator now)
- `_parse_review_result` — 3-tier PASS/FAIL extraction is solid, no change
- Extra review pass logic — still works, no change
- Sibling context + file scope validation — no change
- Delta diff computation — still computed and passed, just not framed as a scope limiter
- `no_changes_on_retry` auto-pass — still sensible (agent made zero changes = feedback already addressed)

### Files Changed

- `forge/review/llm_review.py` — new `REVIEW_SYSTEM_PROMPT`, fix `custom_review_focus` separator, updated `_build_review_prompt` retry section + delta section
- No other files changed for this fix

---

## Summary of All Changes

| File | Change |
|------|--------|
| `forge/core/daemon.py` | Replace `asyncio.gather` batch with continuous task pool, add `_safe_execute_task` wrapper, extract `_handle_task_exception`, update loop exit condition |
| `forge/review/llm_review.py` | Comprehensive review prompt, fix separator, remove retry suppression |

## Testing Strategy

**Task pool:**
- Existing daemon tests still pass (behavior is equivalent for happy path)
- New test: task1 completes and goes to "todo" while task2 still running → verify task1 gets re-dispatched without waiting. **Test approach:** mock `_execute_task` with controllable `asyncio.Event` gates — each mock task awaits its own event, so the test can deterministically control completion order without relying on timing.
- New test: unhandled exception in one task doesn't affect others in pool
- New test: shutdown cancels all active tasks cleanly, agents released
- New test: `_active_tasks` guard prevents double-dispatch of task already in pool
- New test: `_safe_execute_task` handles `CancelledError` by releasing agent

**Review quality:**
- Existing `_parse_review_result` tests unchanged
- Verify `_build_review_prompt` output includes full checklist system prompt
- Verify retry prompt includes prior feedback as context without suppression language
- Verify delta diff section uses neutral framing
- Verify `custom_review_focus` gets `"\n\n"` separator
