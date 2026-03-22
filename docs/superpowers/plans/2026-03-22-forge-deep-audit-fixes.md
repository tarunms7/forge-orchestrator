# Forge Deep Audit — Full Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 97 findings from the comprehensive Forge audit — covering security, concurrency, reliability, UX, and code quality across every subsystem.

**Architecture:** Fixes are grouped by module to minimize merge conflicts. Each task is independently testable. Tasks within the same module are sequential; across modules they can run in parallel.

**Tech Stack:** Python 3.12+, asyncio, FastAPI, SQLAlchemy, Next.js 14, TypeScript, Zustand, Tailwind v4

## ⚠️ IMPORTANT: Line Numbers Are Approximate

Recent commits to `fix/pipeline-reliability` have shifted line numbers in several core files:
- `forge/core/daemon.py` — shifted +5 to +50 lines (more in later sections)
- `forge/core/daemon_executor.py` — shifted +2 to +35 lines
- `forge/core/daemon_helpers.py` — shifted +72 lines in the latter half
- `forge/storage/db.py` — shifted +50 lines

**DO NOT rely on line numbers. Use `grep` to find the exact code before editing.**

## Already Completed (skip these)

- **Task 6 (Replace `except Exception: pass` with logging)** — DONE in `fix/pipeline-reliability` PR. All 3 specific instances fixed. Still do Step 4 (grep for any remaining instances across the codebase) as a verification pass.
- **Task 13 partial (Staleness pruning)** — `prune_stale_lessons()` and confidence scoring already added. Still need: max lesson cap (500) and CLI dedup check in `forge lessons add`.

---

## Task Group A: Input Sanitization & Security (forge/merge/, forge/core/)

### Task 1: Add `sanitize_task_id()` and validate all task_id usage

Fixes: C1 (unsanitized task_id in branch names), H1 (path traversal via task_id/repo_id)

**Files:**
- Create: `forge/core/sanitize.py`
- Modify: `forge/merge/worktree.py:15-19`
- Modify: `forge/core/daemon_executor.py:191`
- Modify: `forge/core/daemon.py:320-321`
- Modify: `forge/core/daemon_helpers.py:770-771`
- Modify: `forge/core/followup.py:350`
- Create: `forge/core/sanitize_test.py`

- [ ] **Step 1: Create `forge/core/sanitize.py` with validation functions**

```python
"""Input sanitization for task IDs, repo IDs, and branch names."""

import re

_TASK_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_REPO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class UnsafeInputError(ValueError):
    """Raised when an input fails sanitization."""


def validate_task_id(task_id: str) -> str:
    """Validate a task_id is safe for use in paths and branch names.

    Raises UnsafeInputError if invalid.
    """
    if not task_id or not _TASK_ID_RE.match(task_id):
        raise UnsafeInputError(
            f"Invalid task_id: {task_id!r}. Must match [a-zA-Z0-9][a-zA-Z0-9_-]{{0,63}}"
        )
    if ".." in task_id or "/" in task_id or "\\" in task_id:
        raise UnsafeInputError(f"task_id contains path traversal: {task_id!r}")
    return task_id


def validate_repo_id(repo_id: str) -> str:
    """Validate a repo_id is safe for use in paths."""
    if not repo_id or not _REPO_ID_RE.match(repo_id):
        raise UnsafeInputError(
            f"Invalid repo_id: {repo_id!r}. Must match [a-z0-9][a-z0-9-]*"
        )
    if ".." in repo_id:
        raise UnsafeInputError(f"repo_id contains path traversal: {repo_id!r}")
    return repo_id
```

- [ ] **Step 2: Write failing tests for sanitize module**

```python
# forge/core/sanitize_test.py
import pytest
from forge.core.sanitize import validate_task_id, validate_repo_id, UnsafeInputError

class TestValidateTaskId:
    def test_valid_simple(self):
        assert validate_task_id("setup-api") == "setup-api"

    def test_valid_with_underscores(self):
        assert validate_task_id("task_1_setup") == "task_1_setup"

    def test_rejects_path_traversal(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("../../etc/passwd")

    def test_rejects_slash(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("task/sub")

    def test_rejects_empty(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("")

    def test_rejects_special_chars(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("task;rm -rf /")

    def test_rejects_too_long(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("a" * 65)

class TestValidateRepoId:
    def test_valid(self):
        assert validate_repo_id("my-repo") == "my-repo"

    def test_rejects_path_traversal(self):
        with pytest.raises(UnsafeInputError):
            validate_repo_id("..evil")

    def test_rejects_uppercase(self):
        with pytest.raises(UnsafeInputError):
            validate_repo_id("MyRepo")
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m pytest forge/core/sanitize_test.py -v`

- [ ] **Step 4: Wire `validate_task_id` into `WorktreeManager`**

In `forge/merge/worktree.py`, add import and call in `create()` and `remove()`:

```python
from forge.core.sanitize import validate_task_id

# In create() at the top:
task_id = validate_task_id(task_id)

# In remove() at the top:
task_id = validate_task_id(task_id)
```

- [ ] **Step 5: Wire `validate_task_id` into daemon_executor.py, daemon.py, daemon_helpers.py, followup.py**

Add `validate_task_id(task_id)` at the entry point of every function that uses `task_id` in a path or branch name.

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest forge/ -x -q`

- [ ] **Step 7: Commit**

```bash
git add forge/core/sanitize.py forge/core/sanitize_test.py forge/merge/worktree.py forge/core/daemon_executor.py forge/core/daemon.py forge/core/daemon_helpers.py forge/core/followup.py
git commit -m "security: add task_id/repo_id sanitization to prevent path traversal and branch injection"
```

---

### Task 2: Fix error message information leakage in API

Fixes: H4 (error messages leak internal paths/stderr)

**Files:**
- Modify: `forge/api/routes/tasks.py:503,676,682,810-811`
- Modify: `forge/api/routes/webhooks.py:347`
- Modify: `forge/api/routes/github.py:48`

- [ ] **Step 1: Create a safe error wrapper**

Add to `forge/api/routes/tasks.py` (or a shared utility):

```python
def _safe_error(exc: Exception, prefix: str = "Operation failed") -> str:
    """Return a user-safe error message without internal details."""
    logger.error("%s: %s", prefix, exc, exc_info=True)
    return prefix
```

- [ ] **Step 2: Replace all `str(exc)` in HTTP responses with safe messages**

- `tasks.py:810`: `detail=f"Failed to create PR: {pr_result.stderr}"` → `detail="Failed to create PR. Check server logs for details."`
- `github.py:48`: `detail=str(exc)` → `detail="GitHub operation failed"`
- `webhooks.py:347`: `str(exc)[:500]` posted to GitHub → truncate to just the exception type, not the full message

- [ ] **Step 3: Replace all `str(exc)` in WebSocket broadcasts with safe messages**

- `tasks.py:503`: `"error": str(exc)` → `"error": "Task execution failed"`
- `tasks.py:676`: `"error": str(pr_exc)` → `"error": "PR creation failed"`
- `tasks.py:682`: `"error": str(exc)` → `"error": "Execution error"`

- [ ] **Step 4: Run tests**

Run: `python -m pytest forge/api/ -x -q`

- [ ] **Step 5: Commit**

---

## Task Group B: Concurrency Fixes (forge/core/)

### Task 3: Add asyncio.Lock around `_active_tasks`

Fixes: C2 (race on `_active_tasks`), C3 (duplicate dispatch in `_on_task_answered`)

**Files:**
- Modify: `forge/core/daemon.py:214` (add `_active_tasks_lock`)
- Modify: `forge/core/daemon.py:1431-1470` (`_execution_loop_inner`)
- Modify: `forge/core/daemon_executor.py:696-738` (`_on_task_answered`)
- Modify: `forge/core/daemon_executor.py:762` (`_safe_execute_resume` finally)

- [ ] **Step 1: Add `_active_tasks_lock` to `ForgeDaemon.__init__`**

```python
# In __init__, after _merge_lock:
self._active_tasks_lock = asyncio.Lock()
```

- [ ] **Step 2: Wrap ONLY mutations (not iterations) in `_execution_loop_inner`**

**IMPORTANT: Do NOT hold the lock during iteration or `_handle_task_exception`.** The iteration at line ~1455 already creates a snapshot via `list(self._active_tasks.items())`. Only hold the lock briefly around mutations:

```python
# When inserting a new task:
async with self._active_tasks_lock:
    self._active_tasks[task_id] = atask

# When popping a done task:
async with self._active_tasks_lock:
    atask = self._active_tasks.pop(tid, None)
```

Do NOT wrap the entire `for tid, atask in list(self._active_tasks.items())` loop — that would deadlock because `_on_task_answered` (called via event handler) also needs the lock.

Also: `_active_tasks` is currently reinitialized to `{}` at the top of `_execution_loop_inner` (line ~1431). Move initialization to `__init__` to prevent losing references on re-entry.

- [ ] **Step 3: Wrap `_active_tasks` mutation in `_on_task_answered`**

The guard (line ~696) and insertion (line ~738) are 35+ lines apart. Lock both:

```python
# At line ~696:
async with self._active_tasks_lock:
    if task_id in self._active_tasks:
        logger.debug("task %s already active, skipping", task_id)
        return

# ... scheduler logic (doesn't need lock) ...

# At line ~731-738:
async with self._active_tasks_lock:
    atask = asyncio.create_task(...)
    self._active_tasks[task_id] = atask
```

Note: there's a TOCTOU gap between the check and insertion. This is acceptable because the scheduler provides additional gating (won't dispatch if no idle agent).

- [ ] **Step 4: Wrap `_active_tasks.pop` in `_safe_execute_resume` finally**

```python
finally:
    async with self._active_tasks_lock:
        self._active_tasks.pop(task_id, None)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest forge/core/daemon_pool_test.py forge/core/daemon_test.py -v`

- [ ] **Step 6: Commit**

---

### Task 4: Fix merge lock to cover full retry sequence

Fixes: C4 (merge lock doesn't protect full merge-review-merge)

**Files:**
- Modify: `forge/core/daemon_executor.py:991-1008`

- [ ] **Step 1: Restructure `_attempt_merge` to hold lock across retries**

The lock should be acquired ONCE at the top and held for the entire merge-review-retry sequence, not released and re-acquired between attempts.

```python
async def _attempt_merge(self, ...):
    async with self._merge_lock:
        result = await merge_worker.merge(task_id)
        if not result.success and result.needs_rebase:
            await self._rebase_worktree(...)
            result = await merge_worker.merge(task_id)
        return result
```

- [ ] **Step 2: Run tests**
- [ ] **Step 3: Commit**

---

### Task 5: Wire TaskStateMachine into all state transitions

Fixes: M25 (TaskStateMachine defined but never enforced), M26 (retry_task resets without checking)

**Files:**
- Modify: `forge/storage/db.py` (add state validation in `update_task_state`)
- Modify: `forge/core/daemon.py:1093-1122` (`retry_task`)
- Modify: `forge/core/state.py` (add retry transition)

- [ ] **Step 1: Add TODO→TODO self-transition and ERROR→TODO for retry**

In `forge/core/state.py`, update `_TRANSITIONS`:

```python
TaskState.ERROR: {TaskState.TODO},    # allow retry
TaskState.CANCELLED: {TaskState.TODO},  # allow retry of cancelled tasks
```

- [ ] **Step 2: Add state validation in `db.update_task_state`**

```python
async def update_task_state(self, task_id: str, new_state: str) -> None:
    task = await self.get_task(task_id)
    if task:
        from forge.core.state import TaskStateMachine
        from forge.core.models import TaskState
        try:
            current = TaskState(task.state)
            target = TaskState(new_state)
            if not TaskStateMachine.can_transition(current, target):
                logger.warning(
                    "Invalid state transition for %s: %s -> %s",
                    task_id, task.state, new_state,
                )
        except ValueError:
            pass  # unknown state, let it through for now
    # proceed with the update...
```

- [ ] **Step 3: Add state check in `retry_task`**

```python
task = await db.get_task(task_id)
if task and task.state not in (TaskState.ERROR.value, TaskState.CANCELLED.value):
    logger.warning("Cannot retry task %s in state %s", task_id, task.state)
    return
```

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

---

## Task Group C: Silent Failure Fixes (forge/core/)

### Task 6: Replace all `except Exception: pass` with logging

Fixes: H5 (agent slot leak), H6 (resume crash limbo), H7 (pipeline error swallowed)

**Files:**
- Modify: `forge/core/daemon.py:1359-1360`
- Modify: `forge/core/daemon.py:1394-1398`
- Modify: `forge/core/daemon_executor.py:754-760`

- [ ] **Step 1: Fix daemon.py:1359-1360 — agent release failure**

```python
except Exception:
    logger.exception("Failed to release agent for crashed task %s", task_id)
```

- [ ] **Step 2: Fix daemon.py:1394-1398 — pipeline error event failure**

```python
except Exception:
    logger.exception("Failed to emit pipeline:error event after execution loop crash")
```

- [ ] **Step 3: Fix daemon_executor.py:754-760 — resume crash recovery**

```python
except Exception:
    logger.exception(
        "Failed to restore task %s to AWAITING_INPUT after resume crash", task_id
    )
```

- [ ] **Step 4: Grep the entire codebase for remaining `except Exception: pass` or bare `except:` patterns**

Run: `grep -rn "except Exception:\s*$" forge/ --include="*.py"` and `grep -rn "except.*:\s*pass" forge/ --include="*.py"` to find ALL remaining instances.

- [ ] **Step 5: Fix all remaining instances (log instead of swallow)**
- [ ] **Step 6: Commit**

---

## Task Group D: Blocking Subprocess → Async (forge/core/, forge/agents/, forge/merge/, forge/api/)

### Task 7: Convert blocking subprocess calls to async in hot paths

Fixes: H11 (adapter.py blocking), H12 (followup.py blocking), H13 (API routes blocking), H14 (worktree.py no timeout)

**Files:**
- Modify: `forge/agents/adapter.py:574-626` (`_get_changed_files`)
- Modify: `forge/core/followup.py:500-607`
- Modify: `forge/merge/worktree.py` (all subprocess calls)
- Modify: `forge/api/routes/tasks.py` (all subprocess.run calls)

- [ ] **Step 1: Add timeout to ALL subprocess calls in `worktree.py`**

Every `subprocess.run(...)` gets `timeout=60`:

```python
subprocess.run(cmd, cwd=self._repo, check=True, capture_output=True, timeout=60)
```

- [ ] **Step 2: Convert `_get_changed_files` in adapter.py to async**

Replace `subprocess.run` with `asyncio.create_subprocess_exec`. **This changes the function signature from sync to async**, so update all callers:

Callers to update (grep for `_get_changed_files`):
- `forge/agents/adapter.py` — called from `run()` (already async, just add `await`)
- Any other callers found via grep

```python
async def _get_changed_files(worktree_path: str, ...) -> list[str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--name-only", "HEAD",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    ...
```

- [ ] **Step 3: Convert followup.py subprocess calls to async**

`_setup_worktree`, `_commit_and_push`, `_cleanup_worktree` — use `asyncio.create_subprocess_exec`.

- [ ] **Step 4: Convert API route subprocess calls to use `asyncio.to_thread`**

For the 11 `subprocess.run()` calls in `tasks.py`:

```python
result = await asyncio.to_thread(
    subprocess.run, ["git", "worktree", "prune"],
    cwd=project_path, capture_output=True, timeout=30,
)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest forge/ -x -q`

- [ ] **Step 6: Commit**

---

## Task Group E: EventEmitter & Resource Leaks (forge/core/)

### Task 8: Add `off()` method to EventEmitter and fix resource leaks

Fixes: H10 (no off() method), M (followup_store unbounded), M (pipeline_images unbounded), M (pending_graphs unbounded)

**Files:**
- Modify: `forge/core/events.py`
- Modify: `forge/core/daemon.py:1409-1416` (use off() instead of private access)
- Create: `forge/core/events_test.py`

- [ ] **Step 1: Add `off()` and `clear()` methods to EventEmitter**

```python
def off(self, event: str, handler: Callable) -> None:
    """Remove a handler for *event*."""
    handlers = self._handlers.get(event, [])
    try:
        handlers.remove(handler)
    except ValueError:
        pass

def clear(self, event: str | None = None) -> None:
    """Remove all handlers, or all handlers for a specific event."""
    if event is None:
        self._handlers.clear()
    else:
        self._handlers.pop(event, None)
```

- [ ] **Step 2: Write tests**

```python
# forge/core/events_test.py
import pytest
from forge.core.events import EventEmitter

@pytest.mark.asyncio
async def test_off_removes_handler():
    emitter = EventEmitter()
    calls = []
    async def handler(data): calls.append(data)
    emitter.on("test", handler)
    await emitter.emit("test", "a")
    emitter.off("test", handler)
    await emitter.emit("test", "b")
    assert calls == ["a"]

@pytest.mark.asyncio
async def test_off_nonexistent_handler_no_error():
    emitter = EventEmitter()
    async def handler(data): pass
    emitter.off("test", handler)  # should not raise

@pytest.mark.asyncio
async def test_clear_all():
    emitter = EventEmitter()
    async def handler(data): pass
    emitter.on("a", handler)
    emitter.on("b", handler)
    emitter.clear()
    assert len(emitter._handlers) == 0
```

- [ ] **Step 3: Replace private `_handlers` access in daemon.py with `off()`**

In `_cleanup_answer_handler()`:

```python
def _cleanup_answer_handler(self) -> None:
    handler = getattr(self, "_current_answer_handler", None)
    if handler:
        self._events.off("task:answer", handler)
        self._current_answer_handler = None
```

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

---

### Task 9: Add log rotation

Fixes: M (no log rotation, `.forge/forge.log` grows unbounded)

**Files:**
- Modify: `forge/core/logging_config.py:86-89,114-122`

- [ ] **Step 1: Replace `FileHandler` with `RotatingFileHandler`**

```python
from logging.handlers import RotatingFileHandler

# In configure_logging():
if log_file is not None:
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=3,
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

# In configure_tui_logging():
file_handler = RotatingFileHandler(
    log_file, maxBytes=10 * 1024 * 1024, backupCount=3,
)
```

- [ ] **Step 2: Run tests**
- [ ] **Step 3: Commit**

---

## Task Group F: API Auth & Middleware Fixes (forge/api/)

### Task 10: Unify `get_current_user` — delete duplicate implementations

Fixes: M (3 different get_current_user, tasks.py and followup.py don't honor single-user mode)

**Files:**
- Modify: `forge/api/routes/tasks.py:80-100` (delete local get_current_user)
- Modify: `forge/api/routes/followup.py:31-42` (delete local get_current_user)
- Modify: `forge/api/routes/diff.py`, `history.py`, `settings.py`, `templates.py`, `github.py` (fix imports)

- [ ] **Step 1: In `tasks.py`, delete the local `get_current_user` function and add import**

```python
from forge.api.security.dependencies import get_current_user
```

Remove the ~20-line duplicate function.

- [ ] **Step 2: In `followup.py`, same — delete local, add import**

- [ ] **Step 3: Update all files that import `get_current_user` from `tasks.py`**

Grep for `from forge.api.routes.tasks import get_current_user` and change to `from forge.api.security.dependencies import get_current_user`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest forge/api/ -x -q`

- [ ] **Step 5: Commit**

---

### Task 11: Add WebSocket connection limits and in-memory store cleanup

Fixes: M (no WS connection limit), M (followup_store unbounded), M (pipeline_images unbounded), M (pending_graphs unbounded), M (webhook rate limiter unbounded)

**Files:**
- Modify: `forge/api/ws/manager.py`
- Modify: `forge/api/app.py` (add periodic cleanup)
- Modify: `forge/api/routes/tasks.py` (add cleanup for pipeline_images, pending_graphs)
- Modify: `forge/api/routes/webhooks.py` (add cleanup for webhook rate limiter)

- [ ] **Step 1: Add per-user connection limit to WebSocket manager**

```python
MAX_CONNECTIONS_PER_USER = 10

async def connect(self, websocket, user_id, pipeline_id):
    user_connections = sum(
        1 for conns in self.active_connections.values()
        for ws, uid in conns if uid == user_id
    )
    if user_connections >= MAX_CONNECTIONS_PER_USER:
        await websocket.close(code=4002, reason="Too many connections")
        return False
    # proceed with connection...
```

- [ ] **Step 2: Add cleanup for followup_store, pipeline_images, pending_graphs**

Add a background task in `app.py` lifespan that runs every 30 minutes to prune entries older than 2 hours.

- [ ] **Step 3: Add cleanup for webhook rate limiter**

In `webhooks.py`, extend the existing 5-minute cleanup to also cap the dict size at 10,000 entries.

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

---

## Task Group G: Config & Validation Fixes (forge/config/)

### Task 12: Fix forge.toml values bypassing pydantic validators

Fixes: M (apply_project_config sets attributes directly, bypassing validators)

**Files:**
- Modify: `forge/config/project_config.py:295-324`

- [ ] **Step 1: Add range validation in `ProjectConfig` dataclasses**

Add `__post_init__` checks to `AgentConfig` and related dataclasses. This is simpler and more robust than using pydantic internals, and catches bad values at the source:

```python
@dataclass
class AgentConfig:
    max_parallel: int = 4
    max_turns: int = 200
    timeout_seconds: int = 300
    autonomy: str = "full"

    def __post_init__(self):
        if self.max_parallel < 1:
            self.max_parallel = 1
        if self.max_turns < 1:
            self.max_turns = 1
        if self.timeout_seconds < 30:
            self.timeout_seconds = 30
        if self.autonomy not in ("full", "suggest", "ask"):
            self.autonomy = "full"
```

This ensures values from `forge.toml` are always within valid ranges before they reach `apply_project_config`.

- [ ] **Step 2: Add tests for invalid forge.toml values**
- [ ] **Step 3: Run tests**
- [ ] **Step 4: Commit**

---

### Task 13: Add lesson pruning and dedup improvements

Fixes: M (lessons grow unbounded), M (imprecise substring dedup), M (CLI lessons add has no dedup)

**Files:**
- Modify: `forge/storage/db.py:1248-1263` (add max lesson cap + pruning)
- Modify: `forge/storage/db.py:1265-1285` (improve matching)
- Modify: `forge/cli/lessons.py:94-103` (add dedup check)

- [ ] **Step 1: Add max lesson cap with auto-pruning in `add_lesson()`**

After inserting the new lesson, check count and prune:

```python
MAX_LESSONS = 500

async def add_lesson(self, ...):
    # ... existing insert logic ...

    # Prune if over cap: delete lowest hit_count lessons
    count = await self._scalar("SELECT COUNT(*) FROM lessons")
    if count > MAX_LESSONS:
        excess = count - MAX_LESSONS
        await self._execute(
            "DELETE FROM lessons WHERE id IN ("
            "SELECT id FROM lessons ORDER BY hit_count ASC, created_at ASC LIMIT :excess"
            ")", {"excess": excess},
        )
```

- [ ] **Step 2: Add dedup check to `forge lessons add` CLI command**

```python
# In lessons.py add command:
existing = await db.find_matching_lesson(trigger, project_dir=project_dir)
if existing:
    click.echo(f"Similar lesson already exists: {existing.title}")
    if not click.confirm("Add anyway?"):
        return
```

- [ ] **Step 3: Run tests**
- [ ] **Step 4: Commit**

---

### Task 14: Fix file encoding and misc config issues

Fixes: M (spec_path no encoding), M (_classify_pipeline_result ignores BLOCKED), M (_detect_excluded_repos short name matching), M (_parse_forge_question brace matching), L (_extract_json greedy regex)

**Files:**
- Modify: `forge/core/daemon.py:548` (encoding)
- Modify: `forge/core/daemon.py:90-100` (classify blocked)
- Modify: `forge/core/daemon.py:70-87` (word boundary matching)
- Modify: `forge/core/daemon_helpers.py:91-106` (string-aware brace matching)
- Modify: `forge/core/claude_planner.py:198-208` (non-greedy regex)

- [ ] **Step 1: Add `encoding="utf-8"` to spec_path open**

```python
with open(spec_path, "r", encoding="utf-8") as f:
```

- [ ] **Step 2: Fix `_classify_pipeline_result` to handle "blocked"**

```python
def _classify_pipeline_result(task_states: list[str]) -> str:
    active_states = [s for s in task_states if s != "cancelled"]
    if not active_states:
        return "complete"
    done_count = sum(1 for s in active_states if s == "done")
    if done_count == len(active_states):
        return "complete"
    blocked_count = sum(1 for s in active_states if s == "blocked")
    if blocked_count + done_count == len(active_states) and done_count > 0:
        return "partial_success"
    if done_count == 0:
        return "error"
    return "partial_success"
```

- [ ] **Step 3: Fix `_detect_excluded_repos` to use word boundary matching**

```python
for repo_id in repo_ids:
    pattern = re.compile(r"\b" + re.escape(repo_id.lower()) + r"\b")
    if pattern.search(sentence_lower):
        excluded.add(repo_id)
```

- [ ] **Step 4: Fix `_parse_forge_question` brace matching to handle strings**

```python
# String-aware brace counter
in_string = False
escape_next = False
for i, ch in enumerate(json_text):
    if escape_next:
        escape_next = False
        continue
    if ch == "\\":
        escape_next = True
        continue
    if ch == '"' and not escape_next:
        in_string = not in_string
        continue
    if in_string:
        continue
    if ch == "{":
        brace_depth += 1
    elif ch == "}":
        brace_depth -= 1
        if brace_depth == 0:
            json_end = i + 1
            break
```

- [ ] **Step 5: Fix `_extract_json` to use string-aware brace matching instead of regex**

The regex approach fails for nested objects. Replace the greedy regex with the same string-aware brace counter from Step 4:

```python
def _extract_json(text: str) -> dict | None:
    # Try fenced code block first
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1).strip()
    else:
        # Find first { and use string-aware brace matching
        start = text.find("{")
        if start == -1:
            return None
        json_text = text[start:]

    # Use string-aware brace counter to find the end
    brace_depth = 0
    in_string = False
    escape_next = False
    json_end = -1
    for i, ch in enumerate(json_text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                json_end = i + 1
                break
    if json_end == -1:
        return None
    try:
        return json.loads(json_text[:json_end])
    except (json.JSONDecodeError, ValueError):
        return None
```

- [ ] **Step 6: Run tests**
- [ ] **Step 7: Commit**

---

## Task Group H: Worktree Lifecycle Fixes (forge/merge/, forge/core/, forge/cli/)

### Task 15: Fix zombie worktrees and cleanup issues

Fixes: H8 (zombie on timeout), M (non-atomic removal), M (forge clean misses multi-repo), M (pipeline branches never cleaned), L (gitignore race)

**Files:**
- Modify: `forge/agents/adapter.py:523-535` (cleanup on timeout)
- Modify: `forge/merge/worktree.py:93-110` (atomic removal)
- Modify: `forge/cli/clean.py:105-125` (multi-repo support)
- Modify: `forge/core/daemon.py:294-309` (pipeline branch cleanup)
- Modify: `forge/merge/worktree.py:21-34` (gitignore race)

- [ ] **Step 1: Add worktree cleanup in adapter.py timeout handler**

After `asyncio.TimeoutError` is caught, ensure the worktree is cleaned up:

```python
except asyncio.TimeoutError:
    logger.warning("Agent timed out for task in %s", worktree_path)
    # Cleanup will be handled by the caller via worktree_mgr.remove()
    return AgentResult(success=False, ...)
```

The caller (`_execute_single_task`) should ensure `worktree_mgr.remove(task_id)` runs in its `finally` block.

- [ ] **Step 2: Make worktree removal more robust — log branch deletion failures**

```python
def remove(self, task_id: str) -> None:
    path = self._task_path(task_id)
    if not os.path.exists(path):
        # Worktree already removed, just clean up branch
        branch = self._branch_name(task_id)
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=self._repo, capture_output=True, timeout=30,
        )
        return

    subprocess.run(
        ["git", "worktree", "remove", path, "--force"],
        cwd=self._repo, check=True, capture_output=True, timeout=60,
    )
    branch = self._branch_name(task_id)
    result = subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=self._repo, capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        logging.getLogger("forge").warning(
            "Failed to delete branch %s: %s", branch, result.stderr.decode()
        )
```

- [ ] **Step 3: Fix `forge clean` to find multi-repo worktrees**

In `clean.py`, scan for `.forge/worktrees/` in all known repo paths, not just the top-level workspace.

- [ ] **Step 4: Add pipeline branch cleanup on error in daemon.py**

In the `finally` block of the execution flow, clean up pipeline branches when the pipeline errors.

- [ ] **Step 5: Fix `.gitignore` race with atomic read-check-write**

Use a simple atomic pattern: read, check, write in a single open. Avoids `fcntl` (Unix-only). The race window is tiny and worst-case is a duplicate `.forge` entry (harmless).

```python
def _ensure_forge_gitignored(self) -> None:
    gitignore = os.path.join(self._repo, ".gitignore")
    entry = ".forge"
    try:
        with open(gitignore, "r", encoding="utf-8") as f:
            content = f.read()
        lines = {line.strip() for line in content.splitlines()}
        if entry in lines or f"/{entry}" in lines or f"{entry}/" in lines:
            return
    except FileNotFoundError:
        content = ""
    # Write atomically-ish: append only
    with open(gitignore, "a", encoding="utf-8") as f:
        f.write(f"\n{entry}\n")
```

- [ ] **Step 6: Run tests**
- [ ] **Step 7: Commit**

---

## Task Group I: Frontend Fixes (web/src/)

### Task 16: Fix Zustand re-render storm and REST/WS race

Fixes: H16 (REST hydration overwrites WS), H17 (all AgentCards re-render on every event)

**Files:**
- Modify: `web/src/stores/taskStore.ts:289-332` (merge instead of replace)
- Modify: `web/src/stores/taskStore.ts:356-708` (use immer or granular updates)
- Modify: `web/src/components/task/AgentCard.tsx` (use granular selectors)

- [ ] **Step 1: Fix `hydrateFromRest` to merge with existing WS state**

```typescript
hydrateFromRest: (pipeline: any) => {
    set((state) => {
        const existing = state.tasks;
        const restTasks = parseTasks(pipeline);
        // Merge: keep WS data if it's newer (has output), otherwise use REST
        const merged = { ...restTasks };
        for (const [id, existingTask] of Object.entries(existing)) {
            if (merged[id] && existingTask.output.length > (merged[id].output?.length || 0)) {
                merged[id] = { ...merged[id], output: existingTask.output };
            }
        }
        return { tasks: merged };
    });
},
```

- [ ] **Step 2: Add task-level selectors for AgentCard**

```typescript
// In taskStore.ts, add a selector:
export const useTask = (taskId: string) =>
    useTaskStore((s) => s.tasks[taskId]);

// In AgentCard.tsx, use:
const task = useTask(taskId);
```

This avoids re-rendering all cards when only one task changes.

- [ ] **Step 3: Run frontend build to verify**

Run: `cd web && npm run build`

- [ ] **Step 4: Commit**

---

### Task 17: Fix dashboard — remove hardcoded status, add error handling

Fixes: H18 (API errors silently swallowed), H19 (hardcoded system status)

**Files:**
- Modify: `web/src/app/page.tsx:61-66` (add error state)
- Modify: `web/src/app/page.tsx:209-233` (fetch real status or remove section)

- [ ] **Step 1: Add error state to dashboard**

```typescript
const [error, setError] = useState<string | null>(null);
const [loading, setLoading] = useState(true);

useEffect(() => {
    setLoading(true);
    Promise.all([
        apiGet("/tasks/stats").catch((e) => { setError("Failed to load stats"); return null; }),
        apiGet("/history?limit=5").catch((e) => { setError("Failed to load history"); return null; }),
    ]).then(([statsData, historyData]) => {
        if (statsData) setStats(statsData);
        if (historyData) setRecent(historyData.items || []);
        setLoading(false);
    });
}, []);
```

- [ ] **Step 2: Add loading skeleton instead of misleading zeros**

Replace hardcoded `0` defaults with a loading skeleton component.

- [ ] **Step 3: Replace hardcoded System Status with real `/health` check or remove**

Either fetch from the actual API backend health endpoint, or remove the hardcoded section entirely (it's worse than no status because it's always wrong).

- [ ] **Step 4: Run build**
- [ ] **Step 5: Commit**

---

### Task 18: Fix accessibility issues across frontend

Fixes: M (table rows not keyboard-navigable), M (expand/collapse divs not buttons), M (pagination no labels), M (ConfirmDialog no focus trap)

**Files:**
- Modify: `web/src/app/history/page.tsx:181,226`
- Modify: `web/src/components/task/ContractsPanel.tsx:382-389,565`
- Modify: `web/src/app/tasks/view/page.tsx:250` (ConfirmDialog)

- [ ] **Step 1: Make history table rows keyboard-accessible**

```tsx
<tr
    key={item.id}
    onClick={() => router.push(`/tasks/view?id=${item.id}`)}
    onKeyDown={(e) => e.key === "Enter" && router.push(`/tasks/view?id=${item.id}`)}
    tabIndex={0}
    role="link"
    className="recent-row"
>
```

- [ ] **Step 2: Add aria-labels to pagination buttons**

```tsx
<button className="page-btn" disabled={currentPage === 1} aria-label="Previous page">
    &#8249;
</button>
```

- [ ] **Step 3: Convert expand/collapse divs to buttons in ContractsPanel**

Replace `<div onClick={...}>` with `<button onClick={...} aria-expanded={expanded}>`.

- [ ] **Step 4: Add focus trap to ConfirmDialog**

Use a simple focus trap: on Tab at the last focusable element, loop back to the first.

- [ ] **Step 5: Run build**
- [ ] **Step 6: Commit**

---

### Task 19a: Fix styling consistency — unify CSS variables

Fixes: M (mixed CSS vars + Tailwind)

**Files:**
- Modify: `web/src/components/ErrorBoundary.tsx`
- Modify: `web/src/components/diff/DiffViewer.tsx`

- [ ] **Step 1: Replace hardcoded zinc colors with CSS variables**

In `ErrorBoundary.tsx`, replace `bg-zinc-950` → `bg-surface-0`, `text-zinc-100` → `text-text-primary`, `bg-zinc-800` → `bg-surface-2`.

In `DiffViewer.tsx`, replace `border-zinc-800` → use `var(--border)`, `bg-zinc-950` → `bg-surface-0`, `text-zinc-400` → `text-text-secondary`, etc.

- [ ] **Step 2: Run build, commit**

---

### Task 19b: Fix form validation and responsive layout

Fixes: M (no password validation), M (history table overflow), M (settings health check)

**Files:**
- Modify: `web/src/app/register/page.tsx`
- Modify: `web/src/app/history/page.tsx`
- Modify: `web/src/app/settings/page.tsx:530`

- [ ] **Step 1: Add password minimum length to registration**

```tsx
const [passwordError, setPasswordError] = useState("");
// On submit: if (password.length < 8) { setPasswordError("..."); return; }
```

- [ ] **Step 2: Add `overflow-x-auto` wrapper around history table**

```tsx
<div className="overflow-x-auto"><table>...</table></div>
```

- [ ] **Step 3: Fix health check URL**

```tsx
const apiBase = process.env.NEXT_PUBLIC_API_URL || "/api";
fetch(`${apiBase.replace('/api', '')}/health`)
```

- [ ] **Step 4: Run build, commit**

---

### Task 19c: Fix WebSocket/state edge cases

Fixes: M (cost_update drift), M (unnecessary WS reconnect on token change), M (notification permission on mount)

**Files:**
- Modify: `web/src/stores/taskStore.ts:614-632`
- Modify: `web/src/hooks/useWebSocket.ts:99`
- Modify: `web/src/app/tasks/view/page.tsx:486`

**Note:** This task modifies `taskStore.ts` — run AFTER Task 16.

- [ ] **Step 1: Fix cost_update to replace total instead of accumulate**

```typescript
costUsd: agent_cost_usd || review_cost_usd || existing.costUsd,
```

- [ ] **Step 2: Use ref for token in useWebSocket**

```typescript
const tokenRef = useRef(token);
tokenRef.current = token;
// In the effect body, use tokenRef.current. Remove token from deps.
```

- [ ] **Step 3: Defer notification permission to user action**

Remove `Notification.requestPermission()` from the `useEffect`. Add an "Enable notifications" button that calls it on click.

- [ ] **Step 4: Run build, commit**

---

## Task Group J: CLI & TUI Fixes (forge/cli/, forge/tui/)

### Task 20: Fix SIGTERM handling, traceback display, branch cleanup

Fixes: M (SIGTERM not handled in `forge serve`), L (CLI exception swallows traceback), L (forge fix leaves orphan branch), L (Rich markup injection gap)

**Files:**
- Modify: `forge/cli/main.py:120-122` (show traceback in verbose mode)
- Modify: `forge/cli/main.py:232-238` (add SIGTERM handler)
- Modify: `forge/cli/fix.py:167-183` (cleanup branch on failure)
- Modify: `forge/tui/widgets/agent_output.py:73-83` (escape non-matched text)

- [ ] **Step 1: Fix CLI exception to show traceback hint**

```python
except Exception as e:
    click.echo(f"Forge failed: {e}", err=True)
    if verbose:
        import traceback
        traceback.print_exc()
    else:
        click.echo("Run with --verbose for full traceback", err=True)
    raise SystemExit(1)
```

- [ ] **Step 2: Add SIGTERM handler to `forge serve`**

```python
signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

def _shutdown(sig, frame):
    if frontend_proc:
        frontend_proc.terminate()
        try:
            frontend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            frontend_proc.kill()
    raise SystemExit(0)
```

- [ ] **Step 3: Add branch cleanup on `forge fix` failure**

```python
except Exception as exc:
    click.echo(f"Forge failed: {exc}", err=True)
    click.echo(f"Note: branch '{branch_name}' was created. Run 'git checkout {original_branch}' to return.", err=True)
    raise SystemExit(1)
```

- [ ] **Step 4: Fix Rich markup injection in `_render_markdown`**

Escape the full `inner` text FIRST, then apply markdown-to-Rich substitutions on the already-escaped text. Since `_escape` turns `[` into `\[`, the regex patterns need to work on the escaped text:

```python
# In _render_markdown, for bullet lines:
inner = _escape(stripped[2:])  # escape ALL text first — now safe from injection
# Re-apply bold: **text** was escaped to **text**, still matchable
inner = re.sub(r'\*\*(.+?)\*\*', lambda m: f"[bold]{m.group(1)}[/]", inner)
# Re-apply inline code: `text` was escaped to `text`, still matchable
inner = re.sub(r'`([^`]+)`', lambda m: f"[#79c0ff]{m.group(1)}[/]", inner)
return f"  . {inner}"
```

The key insight: `_escape` only escapes `[` and `]`. Markdown markers (`**`, backtick) are unaffected, so they still match. But any Rich markup `[red]...[/]` in the source text has been escaped to `\[red\]...\[/\]` and won't be interpreted.

- [ ] **Step 5: Run tests**
- [ ] **Step 6: Commit**

---

## Task Group K: Test Quality & CI (tests/, .github/)

### Task 21: Fix broken/useless tests

Fixes: L (tests that test nothing), L (hardcoded version), L (flaky timing), L (DB connection leaks)

**Files:**
- Modify: `forge/api/routes/history_test.py:287-317` (rewrite to test actual code)
- Modify: `forge/api/app_test.py:18,57` (read version dynamically)
- Modify: `forge/core/daemon_pool_test.py` (replace sleep with events)
- Modify: `forge/tests/integration/test_pipeline_lifecycle.py` (add db.close())

- [ ] **Step 1: Fix history_test.py — make tests test actual endpoint**

Replace dict-construction tests with actual endpoint tests using `httpx.AsyncClient`.

- [ ] **Step 2: Fix hardcoded version in app_test.py**

```python
from importlib.metadata import version as pkg_version
expected_version = pkg_version("forge-orchestrator")
assert data["version"] == expected_version
```

- [ ] **Step 3: Fix timing-dependent tests in daemon_pool_test.py**

Replace `asyncio.sleep(0.2)` with `asyncio.Event` synchronization:

```python
dispatched = asyncio.Event()
# In the mock: dispatched.set()
await asyncio.wait_for(dispatched.wait(), timeout=5.0)
```

- [ ] **Step 4: Add `db.close()` to integration test cleanup**

Add `finally: await db.close()` or use a proper fixture with `yield`.

- [ ] **Step 5: Run tests**
- [ ] **Step 6: Commit**

---

### Task 22: Add CI/CD pipeline

Fixes: C5 (no CI at all)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create comprehensive CI workflow**

```yaml
name: CI
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: pip install -e ".[dev]"
      - name: Lint
        run: ruff check forge/
      - name: Format check
        run: ruff format --check forge/
      - name: Tests
        run: python -m pytest forge/ -x -q --timeout=60
      # Type checking: add mypy step when mypy is configured in pyproject.toml

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm ci
      - run: npm run build
```

- [ ] **Step 2: Add ruff configuration to pyproject.toml**

```toml
[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM"]
```

- [ ] **Step 3: Run CI locally to verify**

```bash
ruff check forge/
python -m pytest forge/ -x -q
```

- [ ] **Step 4: Commit**

---

## Task Group L: Remaining Medium/Low Fixes

### Task 23: Fix remaining medium issues

**Files:** Various

- [ ] **Step 1: Fix `_effective_max_agents` override on resume** (`daemon.py:1432`)

Store the calculated value and don't overwrite on loop entry.

- [ ] **Step 2: Fix `_sanitize_branch_name` not applied to task branches** (`worktree.py:19`)

Already handled by Task 1 (validate_task_id). The task_id itself is validated, so the branch name `forge/{task_id}` is safe.

- [ ] **Step 3: Fix question protocol negative remaining** (`adapter.py:191`)

```python
remaining = max(0, self._question_limit - questions_asked)
```

- [ ] **Step 4: Fix conventions.py duplicate append** (`conventions.py:99`)

Check if convention already exists in file before appending.

- [ ] **Step 5: Fix `forge status` Rich markup for status text** (`status.py:96-102`)

```python
from forge.tui.widgets.agent_output import _escape
# Or inline: status_text = p['status'].replace('[', '\\[')
```

- [ ] **Step 6: Fix `_execution_loop_inner` duplicate `_detect_excluded_repos` call** (`daemon.py:525,670`)

Store the result of the first call and reuse it.

- [ ] **Step 7: Fix `_worktree_path` silent fallback for unknown repo_id** (`daemon.py:317-321`)

```python
rc = self._repos.get(repo_id)
if rc is None and len(self._repos) > 1:
    logger.error("Unknown repo_id %s, available: %s", repo_id, list(self._repos.keys()))
    raise ValueError(f"Unknown repo_id: {repo_id}")
repo_path = rc.path if rc else self._workspace_dir
```

- [ ] **Step 8: Fix duplicate `except Exception: raise` no-op** (`daemon.py:1310-1313`)

Remove the useless try/except that just re-raises.

- [ ] **Step 9: Run full test suite**

Run: `python -m pytest forge/ -x -q`

- [ ] **Step 10: Commit**

---

### Task 24: Add encoding="utf-8" to all file opens

Fixes: M (spec_path no encoding) and proactive fix for all similar patterns

- [ ] **Step 1: Grep for `open(` without `encoding` across entire codebase**

Run: `grep -rn "open(" forge/ --include="*.py" | grep -v "encoding" | grep -v "_test.py" | grep -v "__pycache__"`

- [ ] **Step 2: Add `encoding="utf-8"` to every text-mode `open()` call found**
- [ ] **Step 3: Run tests**
- [ ] **Step 4: Commit**
