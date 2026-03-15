# Resilient Pipeline Lifecycle Design

**Date:** 2026-03-15
**Status:** Draft
**Scope:** Pipeline state machine overhaul, partial completion, quit/resume, dependency cascade, shortcut bar, follow-up/re-run fixes

---

## Problem Statement

Forge pipelines have dead-end states, invisible failures, and no recovery path when things go wrong:

1. **Partial completion is invisible** — when some tasks succeed and some fail, the TUI shows the same "Final Approval" screen with checkmarks for everything. The user doesn't know which tasks failed or why.
2. **Follow-up screen traps user** — pressing `f` focuses a TextArea with no escape binding. The `FollowUp` message has no handler. User is stuck.
3. **Re-run button is dead** — pressing `r` posts a `ReRun` message with no handler. Nothing happens.
4. **Quit kills everything** — pressing `q` twice exits the process. Pipeline stays `"executing"` in DB forever. Agent subprocesses die. No resume from TUI.
5. **No dependency cascade visibility** — when task-1 fails, task-3 (which depends on task-1) sits in TODO until pipeline timeout, then gets marked ERROR as if it failed on its own.
6. **No complexity-aware timeouts** — a 10-minute timeout applies equally to simple docs and massive platform abstractions. Complex tasks time out repeatedly.
7. **Shortcuts are not discoverable** — users don't know what keys are available on each screen.

---

## Design

### 1. Pipeline State Machine

#### States

| State | Description |
|-------|-------------|
| `planning` | Planner is generating the task graph |
| `planned` | Task graph ready, waiting for user approval |
| `executing` | Tasks are running |
| `partial_success` | **(NEW)** Some tasks DONE, some ERROR/BLOCKED. Execution loop exited. |
| `retrying` | **(NEW)** User chose "Retry Failed", daemon re-entered execution loop for ERROR tasks only |
| `complete` | All tasks DONE (or all non-cancelled tasks DONE) |
| `error` | Pipeline-level failure: planner crash, preflight fail, or ALL tasks ERROR with zero DONE |
| `interrupted` | **(NEW)** User quit TUI while tasks were running |
| `cancelled` | User cancelled during planning |

#### Transitions

```
planning → planned                 (plan generated)
planning → error                   (planner crash)
planning → cancelled               (user cancels)

planned → executing                (user approves plan)
planned → cancelled                (user cancels)

executing → complete               (all tasks DONE)
executing → partial_success        (mix of DONE + ERROR/BLOCKED, loop exits)
executing → error                  (ALL tasks ERROR, zero DONE)
executing → interrupted            (user quits TUI mid-execution)
executing → cancelled              (user cancels mid-execution)

partial_success → retrying         (user hits "Retry Failed")
partial_success → complete         (user hits "Skip Failed & Finish" — ERRORs → CANCELLED)
partial_success → interrupted      (user quits from partial_success screen)

retrying → complete                (retried tasks now all DONE)
retrying → partial_success         (some retried tasks failed again)
retrying → interrupted             (user quits during retry)

interrupted → executing            (user resumes from TUI history)
interrupted → partial_success      (user resumes, tasks already terminal mix)
interrupted → cancelled            (user cancels from history)

complete → (terminal)              PR creation happens here
error → (terminal)                 User can restart entire pipeline
cancelled → (terminal)
```

#### Rules

1. No state is a dead end except the 3 terminal states (`complete`, `error`, `cancelled`), and even those have "New Task" as an exit.
2. `partial_success` is the decision hub: retry, skip, create PR, follow-up, or quit and come back.
3. `interrupted` is always recoverable: DB has everything needed to resume.
4. PR creation can happen from `partial_success` without blocking other actions.

#### Determining `partial_success` vs `error` vs `complete`

When the execution loop exits (all tasks terminal):

```python
done_count = sum(1 for t in tasks if t.state == "done")
error_count = sum(1 for t in tasks if t.state in ("error", "blocked"))

if done_count == len(tasks):
    status = "complete"
elif done_count == 0:
    status = "error"
else:
    status = "partial_success"
```

CANCELLED tasks are excluded from the count (user explicitly skipped them).

---

### 2. Task-Level States & Dependency Cascade

#### New Task State: `BLOCKED`

Add `BLOCKED = "blocked"` to the `TaskState` enum.

When a task hits max retries and transitions to ERROR, immediately walk the dependency graph and mark all transitive dependents as BLOCKED.

#### Cascade Algorithm

```python
async def _cascade_blocked(self, db, failed_task_id, pipeline_id):
    """Mark all transitive dependents of a failed task as BLOCKED."""
    all_tasks = await db.list_tasks_by_pipeline(pipeline_id)
    newly_blocked = set()
    queue = [failed_task_id]

    while queue:
        current_id = queue.pop(0)
        for task in all_tasks:
            if task.id in newly_blocked:
                continue
            if task.state not in ("todo", "blocked"):
                continue  # Already running/done/error — don't touch
            if current_id in (task.depends_on or []):
                await db.update_task_state(task.id, "blocked")
                await db.update_task_retry_reason(
                    task.id, f"dependency {current_id} failed"
                )
                await self._emit("task:state_changed", {
                    "task_id": task.id,
                    "state": "blocked",
                    "error": f"Blocked: dependency {current_id} failed",
                }, db=db, pipeline_id=pipeline_id)
                newly_blocked.add(task.id)
                queue.append(task.id)  # Cascade transitively
```

**Where to call:** There are TWO code paths that mark a task as ERROR on max retries. Both must call `_cascade_blocked()`:

1. `_handle_retry()` in `daemon_merge.py` (line ~142) — agent/review failures
2. `_handle_merge_retry()` in `daemon_merge.py` (line ~210) — merge failures that exhaust retries

Both have the same pattern: `if task.retry_count >= max_retries: mark ERROR`. Add `await self._cascade_blocked(db, task_id, pipeline_id)` immediately after the ERROR state update in both branches.

#### BLOCKED Behavior

- BLOCKED counts as terminal for the execution loop exit condition (alongside DONE, ERROR, CANCELLED).
- When user hits "Retry Failed": all ERROR tasks reset to TODO, all BLOCKED tasks reset to TODO. The scheduler's `ready_tasks()` re-evaluates dependencies naturally — BLOCKED tasks won't dispatch until their dependency (now TODO, re-running) reaches DONE.
- BLOCKED is displayed as `⚠️` (distinct from ERROR `❌`).

#### Complexity-Scaled Timeouts

In `daemon_executor.py`, when creating the `AgentRuntime`, select timeout based on task complexity using a multiplier on the base `agent_timeout_seconds` setting:

```python
_COMPLEXITY_MULTIPLIERS = {
    "low": 1.0,
    "medium": 1.5,
    "high": 2.0,
}

# In _run_agent or _execute_task:
multiplier = _COMPLEXITY_MULTIPLIERS.get(task.complexity or "medium", 1.5)
timeout = int(self._settings.agent_timeout_seconds * multiplier)
runtime = AgentRuntime(adapter, timeout)
```

With default `agent_timeout_seconds=600`:
- low → 600s (10 min)
- medium → 900s (15 min)
- high → 1200s (20 min)

User override via `FORGE_AGENT_TIMEOUT_SECONDS` still works — it scales the base.

---

### 3. Quit & Resume

#### Graceful Quit Flow

When user presses `q` during an active pipeline:

**First press:**
- Notification: "Pipeline running. Press q again to quit (tasks will be saved)."
- Set `_force_quit = True`

**Second press:**
1. Cancel `self._daemon_task` (triggers the `finally` block in `_execution_loop` which calls `_shutdown_active_tasks()`)
2. Wait briefly (up to 2s) for active agent subprocesses to be killed
3. Reset any tasks in non-terminal in-flight states → TODO in DB. Specifically: IN_PROGRESS, IN_REVIEW, MERGING, AWAITING_INPUT, AWAITING_APPROVAL. All five states represent work that cannot continue without an active process.
4. Release all agents → IDLE
5. Update pipeline status → `"interrupted"`
6. Write event: `pipeline:interrupted` with summary of task states at time of quit
7. Exit TUI

**Why reset stuck tasks to TODO on quit (not on resume):** If the user never comes back, the DB is clean. No orphaned IN_PROGRESS tasks.

#### Resume Flow

User launches `forge tui`, opens pipeline history, selects an `interrupted` pipeline:

1. Reconstruct TUI state from DB events: replay all `pipeline_events` through `state.apply_event()`.
2. Push `PipelineScreen` with the reconstructed state (NOT read-only).
3. Show resume banner: "Resumed pipeline — 3/5 tasks done, 2 pending"
4. Re-enter daemon execution loop: `daemon.execute(graph, db, pipeline_id, resume=True)`
   - `resume=True` skips task/agent creation (they exist in DB)
   - Scheduler's `dispatch_plan()` picks up TODO tasks naturally
   - DONE tasks stay DONE — their work is already merged to the pipeline branch
5. Pipeline status → `"executing"` (or `"retrying"` if it was retrying when quit)

#### Resume from `partial_success`

If user quit while on the partial_success screen:

1. Pipeline status is `"interrupted"`
2. On resume: replay events → state shows partial_success data → push FinalApprovalScreen in partial mode directly
3. User picks up exactly where they left off: retry, skip, or create PR

#### Resume from `retrying`

Same as executing resume. Stuck retry tasks were reset to TODO on quit. On resume, execution loop re-enters, only previously-failed tasks are TODO, DONE tasks stay DONE.

#### Hard Kill Recovery (SIGKILL / Power Loss)

No cleanup runs. Pipeline stays `"executing"` in DB.

On next `forge tui`, detect orphaned pipelines (see Section 4 for full PID+token detection logic):
- For every pipeline with status `"executing"` or `"retrying"`, run the concurrent access check from Section 4 (PID alive + token verification).
- If detection determines no active executor → orphan detected.
- Show in history with `"interrupted"` badge (orange).
- On select: reset stuck tasks → TODO, release agents, update status → `"interrupted"`, then normal resume.

---

### 4. Orphan Detection & Concurrent Access Protection

#### Executor Tracking

Two new columns on `pipelines` table:

```sql
ALTER TABLE pipelines ADD COLUMN executor_pid INTEGER;
ALTER TABLE pipelines ADD COLUMN executor_token VARCHAR;
```

**Migration:** These columns are added via `ALTER TABLE` with NULL defaults. Existing pipelines get NULL for both columns, which is correct — NULL means "no active executor." The migration runs at DB initialization time in `db.py`'s `_ensure_schema()` method, same pattern used for previous schema additions (e.g. `paused_at`, `contracts_json`).

#### Lifecycle

```
Start execution:
  UPDATE pipelines SET executor_pid=os.getpid(), executor_token=uuid4() WHERE id=?

Each dispatch cycle (in execution loop, before dispatching):
  SELECT executor_token FROM pipelines WHERE id=?
  if token != my_token → emit "pipeline:taken_over", exit loop gracefully

Graceful quit / execution complete:
  UPDATE pipelines SET executor_pid=NULL, executor_token=NULL WHERE id=?
```

#### Concurrent Access

When a second TUI instance tries to resume a pipeline that has an active executor:
1. Read `executor_pid` and `executor_token` from DB
2. Check if PID is alive: `os.kill(pid, 0)`
3. If PID is alive, verify it's actually a Forge process by checking `executor_token` is non-NULL. If both conditions hold → show "Pipeline running in another session (PID {pid})" → read-only mode only.
4. If PID is dead, OR if PID is alive but token verification fails (PID reuse by unrelated process) → orphan flow (treat as interrupted).

**Why both PID and token are checked together:** `os.kill(pid, 0)` only confirms a process exists — the OS can reuse PIDs. A stale PID that now belongs to an unrelated process would have no knowledge of the `executor_token`. By requiring both PID alive AND token non-NULL, we avoid false positives from PID reuse. On hard kill, the token remains set but the PID is dead, correctly triggering orphan detection.

---

### 5. Partial Success Screen & User Actions

#### Screen Adaptation

The existing `FinalApprovalScreen` gains a `partial: bool` mode. No new screen class.

**How partial mode is triggered:** The `_on_all_tasks_done` handler in `state.py` checks the summary data:

```python
def _on_all_tasks_done(self, data: dict) -> None:
    summary = data.get("summary", {})
    error_count = summary.get("error", 0) + summary.get("blocked", 0)
    done_count = summary.get("done", 0)

    if done_count > 0 and error_count > 0:
        self.phase = "partial_success"
    elif done_count == 0:
        self.phase = "error"
    else:
        self.phase = "final_approval"
    self._notify("phase")
```

The `app.py` phase watcher pushes `FinalApprovalScreen` for both `"final_approval"` and `"partial_success"`, passing the mode.

#### Partial Mode Rendering

**Header:**
- Full success: `"Pipeline Complete — Final Approval"`
- Partial: `"Pipeline Partial — 4/5 Tasks Completed"`

**Task table:**
```
✅ Phase 1 — Reliability & Polish          +1584/-0, 1 file
✅ Phase 2 — Smarter Reviews               +2105/-0, 1 file
✅ Phase 3 — Team Features                 +1923/-0, 1 file
❌ Phase 4 — Platform                      timed out (5 attempts)
✅ Technical Debt plan                      +2086/-0, 1 file
```

For dependency cascades:
```
✅ Task 1 — Auth module                     +340/-12, 4 files
❌ Task 2 — API endpoints                  review failed (5 attempts)
⚠️ Task 3 — Integration tests             blocked by Task 2
⚠️ Task 4 — E2E tests                     blocked by Task 2, Task 3
✅ Task 5 — Documentation                   +200/-0, 2 files
```

#### Key Bindings (Partial Mode)

| Key | Action | Visibility |
|-----|--------|------------|
| `Enter` | Create PR (for completed tasks only) | Shown until PR created |
| `r` | Retry Failed (reset ERROR+BLOCKED→TODO, re-enter execution) | Always in partial mode |
| `s` | Skip Failed & Finish (ERROR+BLOCKED→CANCELLED, status→complete) | Always in partial mode |
| `d` | View Diff (completed work only) | Always |
| `f` | Follow Up (focus follow-up input) | Always |
| `n` | New Task (abandon this pipeline, start fresh) | Always |
| `Escape` | Back to PipelineScreen | Always |

Full success mode: `r` and `s` are hidden (no failed tasks).

#### Action: Create PR from Partial Mode

1. Generate PR body with completed AND failed sections (see PR Body below).
2. Push branch + create PR.
3. Show PR URL inline on the screen.
4. **Stay on partial_success screen** — user still has retry/skip options.
5. Store `pr_url` on the pipeline record in DB.
6. Remove `Enter`/Create PR from the shortcut bar (PR already exists).

#### Action: Retry Failed

1. Reset all ERROR tasks → TODO.
2. Reset all BLOCKED tasks → TODO.
3. Pipeline status → `retrying`.
4. Pop FinalApprovalScreen → back to PipelineScreen.
5. Re-enter daemon execution loop: `daemon.execute(graph, db, pipeline_id, resume=True)`.
6. Scheduler picks up TODO tasks, dispatches them.

When retries finish:
- All retried tasks DONE → `complete` → FinalApprovalScreen (full mode).
- Some retried tasks still fail → `partial_success` → FinalApprovalScreen (partial mode again). User can retry again — no limit on manual retries.

#### Action: Skip Failed & Finish

1. Mark all ERROR and BLOCKED tasks as CANCELLED.
2. Pipeline status → `complete`.
3. FinalApprovalScreen switches to full-success mode (only completed tasks shown).

#### PR-While-Retrying Race Condition

**Case 1: PR not yet merged when retries finish.**
Retried tasks merge to the same pipeline branch. Push new commits to remote. The open PR auto-updates. Pipeline → `complete`, show "PR updated with retry results."

**Case 2: PR already merged when retries finish.**
Before pushing retry commits, check: `gh pr view {pr_url} --json state`. If merged:
1. The retried tasks already merged their work to the pipeline branch (e.g. `forge/roadmap-implementation-plans`).
2. The pipeline branch now has commits beyond what was in the merged PR.
3. Rebase the pipeline branch onto `main` (which now contains the merged PR's squash commit) to get a clean diff.
4. Create a NEW follow-up PR from the pipeline branch → `main`. Title: `"Forge: [original title] — retry results"`.
5. The follow-up PR's diff contains ONLY the retried tasks' work, not the already-merged work.
6. Show new PR URL. Store it on the pipeline record (replaces the old `pr_url`).

**Case 3: User merged PR, quit, resumed, retried.**
Same as Case 2 — detect merged PR, rebase pipeline branch, create follow-up PR.

**Case 4: User created PR, didn't retry, didn't merge, quit.**
On resume: partial_success screen shows existing PR URL + retry/skip options. No data loss.

#### PR Body (Partial Mode)

```markdown
## Summary
Built by Forge pipeline - 5 tasks - 4/5 completed - 12m 30s - $6.57

## Completed Tasks
- ✅ **Phase 1 — Reliability & Polish** — +1584/-0, 1 file
- ✅ **Phase 2 — Smarter Reviews** — +2105/-0, 1 file
- ✅ **Phase 3 — Team Features** — +1923/-0, 1 file
- ✅ **Technical Debt plan** — +2086/-0, 1 file

## Failed Tasks (not included in this PR)
- ❌ **Phase 4 — Platform** — timed out after 5 attempts

🤖 Built with [Forge](https://github.com/tarunms7/forge-orchestrator)
```

---

### 6. Follow-Up & Re-Run Button Fixes

#### Fix: Escape from FollowUpTextArea

Add escape binding to `FollowUpTextArea` in `forge/tui/widgets/followup_input.py`:

```python
class FollowUpTextArea(TextArea):
    BINDINGS = [
        Binding("ctrl+u", "clear_input", "Clear", show=False, priority=True),
        Binding("escape", "unfocus", "Back", show=False, priority=True),
    ]

    def action_unfocus(self) -> None:
        """Return focus to the parent screen."""
        self.screen.focus()
```

Flow: `f` → textarea focused → Escape → back to screen bindings → Escape → pop screen.

#### Fix: Follow-Up Handler

Add `on_final_approval_screen_follow_up` to `app.py`:

1. Receive the follow-up prompt text from the message.
2. Create a NEW task in DB:
   - `id`: `{pipeline_prefix}-followup-{n}` (incrementing counter)
   - `title`: first 80 chars of the prompt
   - `description`: full prompt text
   - `depends_on`: all currently DONE task IDs (follow-up builds on completed work)
   - `complexity`: `"medium"`
   - `state`: `TODO`
3. Pipeline status → `executing`.
4. Pop FinalApprovalScreen → PipelineScreen.
5. Re-enter daemon execution loop with `resume=True`.
6. Scheduler dispatches the follow-up task (dependencies already satisfied).
7. On completion → normal flow (complete or partial_success).

Edge cases:
- Empty follow-up → `FollowUpInput.submit()` already guards with `if text:`.
- Follow-up from partial mode → depends only on DONE tasks, not ERROR/BLOCKED.
- Multiple rapid follow-ups → each creates a separate task, queued and dispatched by scheduler.
- Follow-up task fails → normal retry → if max retries exceeded → partial_success.

#### Fix: Re-Run Handler

Add `on_final_approval_screen_rerun` to `app.py`. Same logic as "Retry Failed" action:

1. Reset ERROR → TODO, BLOCKED → TODO.
2. Pipeline status → `retrying`.
3. Pop FinalApprovalScreen → PipelineScreen.
4. Re-enter execution loop.

In full success mode, `r` binding is hidden — no failed tasks to retry.

---

### 7. Universal Shortcut Bar

#### Widget: `ShortcutBar`

A reusable widget pinned to the bottom of every screen. Takes a list of `(key, label)` tuples and renders them in bold bright cyan.

```python
class ShortcutBar(Static):
    """Persistent bottom bar showing available shortcuts."""

    DEFAULT_CSS = """
    ShortcutBar {
        dock: bottom;
        height: auto;
        max-height: 2;
        background: $surface;
        padding: 0 1;
    }
    """

    shortcuts: reactive[list[tuple[str, str]]] = reactive(list)

    def render(self) -> Text:
        parts = []
        for key, label in self.shortcuts:
            parts.append(f"[bold bright_cyan][{key}][/] {label}")
        return Text.from_markup("  ".join(parts))
```

#### Per-Screen Shortcuts

**HomeScreen:**
```
[Ctrl+S] Submit Task  [↑↓] History  [Enter] Resume Selected  [q] Quit
```

**PlanApprovalScreen:**
```
[Enter] Approve Plan  [↑↓] Scroll  [Esc] Cancel
```

**PipelineScreen (executing):**
```
[d] View Diff  [↑↓] Select Task  [q] Quit (tasks saved)
```

**PipelineScreen (awaiting input):**
```
[Enter] Answer Question  [d] View Diff  [↑↓] Select Task  [q] Quit (tasks saved)
```

**FinalApprovalScreen (full success):**
```
[Enter] Create PR  [d] View Diff  [f] Follow Up  [n] New Task  [Esc] Back
```

**FinalApprovalScreen (partial mode):**
```
[Enter] Create PR (completed only)  [r] Retry Failed  [s] Skip & Finish  [d] View Diff  [f] Follow Up  [Esc] Back
```

**FinalApprovalScreen (follow-up textarea focused):**
```
[Ctrl+S] Submit Follow-up  [Esc] Cancel Input
```

**FinalApprovalScreen (PR already created, partial):**
```
[r] Retry Failed  [s] Skip & Finish  [d] View Diff  [f] Follow Up  [n] New Task  [Esc] Back
```

**DiffScreen:**
```
[↑↓] Scroll  [Esc] Back
```

**ReviewScreen:**
```
[↑↓] Scroll  [Esc] Back
```

#### Dynamic Updates

The shortcut bar updates reactively when:
- Textarea gains/loses focus → swap to textarea-specific shortcuts
- PR is created → remove Create PR, show PR URL in header
- Pipeline state transitions → update available actions
- Task retries complete → switch from partial to full mode

---

### 8. Files Changed

| File | Change |
|------|--------|
| `forge/core/models.py` | Add `BLOCKED = "blocked"` to TaskState enum |
| `forge/core/daemon.py` | Execution loop exit: differentiate complete/partial_success/error. Emit `pipeline:interrupted` on quit. Add `executor_pid`/`executor_token` management. |
| `forge/core/daemon_merge.py` | After marking task ERROR in BOTH `_handle_retry()` and `_handle_merge_retry()`, call `_cascade_blocked()` |
| `forge/core/daemon_executor.py` | Complexity-scaled timeout: `_COMPLEXITY_MULTIPLIERS` dict, select timeout from `task.complexity` |
| `forge/storage/db.py` | Add `executor_pid`, `executor_token` columns to pipelines. Add `BLOCKED` handling in task queries. |
| `forge/tui/state.py` | `_on_all_tasks_done`: check summary for errors, set phase to `partial_success` when mixed. Add `"partial_success"` and `"retrying"` phase handling. |
| `forge/tui/app.py` | Add `on_final_approval_screen_follow_up` handler. Add `on_final_approval_screen_rerun` handler. Graceful quit: cancel daemon, reset tasks, write interrupted status. Resume flow: reconstruct state, re-enter execution. Phase watcher: handle `partial_success`. |
| `forge/tui/screens/final_approval.py` | Accept `partial` mode flag. Render ✅/❌/⚠️ per task. Show/hide bindings based on mode. Add `s` binding for Skip & Finish. Dynamic shortcut bar updates. |
| `forge/tui/widgets/followup_input.py` | Add `escape` → `action_unfocus` binding to `FollowUpTextArea` |
| `forge/tui/widgets/shortcut_bar.py` | **(NEW)** Reusable `ShortcutBar` widget |
| `forge/tui/pr_creator.py` | `generate_pr_body`: accept `failed_tasks` parameter, render ❌ section |
| `forge/tui/bus.py` | Add `"pipeline:interrupted"` to `TUI_EVENT_TYPES` |
| `forge/config/settings.py` | No changes (complexity multipliers are hardcoded in executor, base timeout stays user-configurable) |

---

### 9. Complete Flow Matrix

| Flow | Start State | User Action | End State | Screen |
|------|-------------|-------------|-----------|--------|
| A | executing | all succeed | complete | FinalApproval (full) |
| B | executing | mix done+error | partial_success | FinalApproval (partial) |
| C | partial_success | Retry Failed | retrying → complete or partial_success | PipelineScreen → FinalApproval |
| D | partial_success | Create PR | partial_success (PR exists) | FinalApproval (partial, PR shown) |
| E | partial_success | Create PR + Retry | retrying → complete (PR updated or new PR) | PipelineScreen → FinalApproval |
| F | partial_success | Skip & Finish | complete | FinalApproval (full) |
| G | executing | quit | interrupted | TUI exits |
| H | interrupted | resume | executing | PipelineScreen |
| I | partial_success | quit | interrupted | TUI exits |
| J | interrupted (was partial) | resume | partial_success | FinalApproval (partial) |
| K | retrying | quit | interrupted | TUI exits |
| L | interrupted (was retrying) | resume | retrying/executing | PipelineScreen |
| M | complete | Follow Up | executing | PipelineScreen → FinalApproval |
| N | partial_success | Follow Up | executing | PipelineScreen → FinalApproval |
| O | executing | all fail | error | PipelineScreen (error summary) |
| P | executing | task fails | cascade: dependents BLOCKED | PipelineScreen (live) |
| Q | hard kill | — | orphaned "executing" | — |
| R | orphaned | resume from history | interrupted → executing | PipelineScreen |
| S | any | second TUI instance | read-only | "Running in another session" |
| T | partial_success | Create PR → quit → PR merged externally → resume → Retry | retrying → complete (follow-up PR) | PipelineScreen → FinalApproval |
| U | FinalApproval | press f → type text → Escape | partial_success (textarea unfocused) | FinalApproval (screen bindings restored) |
| V | FinalApproval | press f → type text → Ctrl+S | executing (follow-up task created) | PipelineScreen |

Every non-terminal state has a forward action and a back/escape action. No dead ends.
