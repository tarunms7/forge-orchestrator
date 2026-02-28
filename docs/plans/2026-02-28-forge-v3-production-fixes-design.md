# Forge v3: Production Readiness Fixes

Date: 2026-02-28

## Problem Statement

Forge v2 runs end-to-end but has 7 production-readiness issues discovered during hands-on testing:

1. No live logs during task execution (UI shows nothing until complete)
2. Auth token lost on every page refresh (logout loop)
3. Task history page returns empty (reads from in-memory dict)
4. Database deleted on every `forge run` invocation
5. Dashboard metrics hardcoded to 0
6. Merge retry re-runs entire agent instead of just retrying merge
7. "View Full Diff" and "Push to GitHub" buttons are non-functional placeholders

## Design

### Section 1: Unified Persistent Database

**Problem**: `daemon.run()` calls `os.remove(db_path)` on every invocation, destroying all history. CLI and web server use separate DB instances.

**Solution**:

- Remove `os.remove(db_path)` from `daemon.run()`. Use a single persistent `forge_pipelines.db` file in the project's `.forge/` directory.
- Add `pipeline_id` column to `TaskRow` so tasks are associated with their pipeline.
- Both `forge run` (CLI) and `forge serve` (web) use the same DB file. The web server's `forge_db_url` parameter points to it.
- Pipeline isolation: each `forge run` creates a new pipeline record with a unique UUID. Old pipelines remain queryable.
- No migration needed for v3 (greenfield DB). Future versions can add Alembic if schema changes become frequent.

### Section 2: Auth Persistence

**Problem**: `authStore` uses plain Zustand with no persistence. Token lives in memory only. Refresh cookie has `path="/auth"` which prevents it from being sent to `/api/auth/refresh`.

**Solution**:

- Add Zustand `persist` middleware with `localStorage` storage to `authStore`. Persist only `token` and `user` fields (not methods).
- Fix cookie path from `path="/auth"` to `path="/"` in the backend's `POST /api/auth/login` and `POST /api/auth/refresh` endpoints.
- On app mount, `AuthGuard` checks for stored token. If expired, attempts refresh using the cookie. If refresh fails, redirects to login.
- `partialize` option ensures only serializable state is persisted (no functions).

### Section 3: Live Streaming Logs

**Problem**: `ClaudeAdapter.run()` calls `sdk_query()` without an `on_message` callback. No output reaches the UI until the agent completes.

**Solution**:

- Add `on_message` callback parameter to `ClaudeAdapter.run()` and `AgentRuntime.run_task()`.
- In `ForgeDaemon._execute_task()`, create a callback that emits `task:agent_output` events via the EventEmitter:
  ```python
  async def on_agent_message(msg):
      text = extract_text(msg)
      if text:
          await self._events.emit("task:agent_output", {
              "task_id": task_id, "line": text
          })
  ```
- The existing WebSocket bridge (`_bridge_events` in `routes/tasks.py`) already forwards events to connected clients.
- `taskStore.ts` already handles `task:agent_output` events and appends lines to the task's output array.
- `AgentCard.tsx` already renders the output array in a terminal-style scrolling div.
- Batch messages at 100ms intervals to prevent WebSocket flooding (accumulate lines, flush every 100ms).

**Message type extraction**: The `claude-code-sdk` emits various message types. Extract text from:
- `AssistantMessage` → concatenate text content blocks
- `ResultMessage` → final result text
- Skip `SystemMessage`, `ToolUseMessage`, `ToolResultMessage` (internal noise)

### Section 4: History Page & Dashboard Metrics

**Problem**: History endpoint reads from `request.app.state.pipelines` (always empty). Dashboard stats are hardcoded `"0"`.

**Solution**:

**History routes** (`forge/api/routes/history.py`):
- Rewrite to query `forge_db.list_pipelines(user_id=user_id)` instead of in-memory dict.
- `GET /api/history` returns list of pipelines with computed duration (`completed_at - created_at`).
- `GET /api/history/{pipeline_id}` returns pipeline detail with associated tasks (queried via `pipeline_id` column on `TaskRow`).
- Graceful fallback: if `forge_db` is `None` (test mode), return empty list / 404.

**Dashboard stats** (`GET /api/stats`):
- New endpoint that counts pipelines by status from `forge_db.list_pipelines(user_id)`.
- Returns: `{ total_runs, active, completed, failed }`.

**Frontend**:
- Dashboard (`page.tsx`): Replace hardcoded `STATS` with `useEffect` that fetches `/api/stats`. Also fetch last 5 pipelines for "Recent Activity" section.
- History page: Already calls `apiGet("/history", token)` and renders data. No frontend changes needed.

### Section 5: Smart Merge Retry

**Problem**: On merge failure, `_handle_retry()` resets task to `todo` and re-runs the entire agent from scratch (2-5 minutes wasted). The code was correct; only the merge conflicted.

**Solution**: 3-tier retry strategy:

**Tier 1: Merge-only retry (auto-rebase)**
When `merge_worker.merge()` fails with conflicts:
1. Stay in `merging` state (don't reset to `todo`).
2. Abort the failed rebase.
3. Fetch latest main and re-attempt rebase.
4. If rebase succeeds, fast-forward merge. Done.
5. If rebase fails again, escalate to Tier 2.
6. Cost: ~1 second.

**Tier 2: Agent fix-up (targeted conflict resolution)**
When Tier 1 fails:
1. Call Claude with a targeted prompt listing only the conflicting files and both sides of the diff.
2. Agent resolves conflicts in those files only (not a full re-run).
3. Re-attempt merge.
4. If this fails, escalate to Tier 3.

**Tier 3: Full re-run (existing behavior)**
For review failures and agent errors (not merge conflicts):
- Reset to `todo`, re-run agent from scratch.
- Only used when the code itself was wrong.

### Section 6: Create PR & Link to GitHub

**Problem**: "View Full Diff" and "Push to GitHub" buttons in `CompletionSummary` have placeholder onclick handlers.

**Solution**: Replace both buttons with a single "Create PR" button.

**Backend** (`POST /api/tasks/{pipeline_id}/pr`):
1. Look up pipeline's project dir from `forge_db`.
2. Create branch: `git checkout -b forge/pipeline-<id>`.
3. Push: `git push origin forge/pipeline-<id>`.
4. Create PR: `gh pr create --base main --head forge/pipeline-<id> --title "Forge: <description>" --body "<task summary>"`.
5. Parse PR URL from `gh` output.
6. Store PR URL on pipeline record (new `pr_url` column on `PipelineRow`).
7. Return `{ pr_url: "https://github.com/..." }`.

**Frontend** (`CompletionSummary.tsx`):
- Replace both placeholder buttons with one "Create PR" button.
- On click: call API, show spinner, on success show linked "View PR" button.
- If PR already exists (stored `pr_url`), show "View PR" link directly.

**Rationale**: GitHub's diff viewer is better than anything we'd build. A PR is the actual deliverable (user never merges without a reviewed PR). One button instead of two.

## Files to Create/Modify

| File | Action | Section |
|------|--------|---------|
| `forge/core/daemon.py` | Modify | 1, 3, 5 |
| `forge/storage/db.py` | Modify | 1, 4 |
| `forge/agents/adapter.py` | Modify | 3 |
| `forge/agents/runtime.py` | Modify | 3 |
| `forge/api/routes/history.py` | Modify | 4 |
| `forge/api/routes/tasks.py` | Modify | 4, 6 |
| `forge/api/routes/auth.py` | Modify | 2 |
| `forge/merge/worker.py` | Modify | 5 |
| `web/src/stores/authStore.ts` | Modify | 2 |
| `web/src/app/page.tsx` | Modify | 4 |
| `web/src/components/task/CompletionSummary.tsx` | Modify | 6 |

## Verification

After implementation:

```bash
# 1. Existing tests pass
.venv/bin/pytest forge/ -q

# 2. Start server, login, create task, execute
forge serve --port 8000
# Open http://localhost:8000, login, create task, execute
# Verify: live logs stream in AgentCard during execution
# Verify: refresh page -> still logged in
# Verify: history page shows past pipelines
# Verify: dashboard stats show real numbers
# Verify: on merge conflict, retries merge without re-running agent
# Verify: "Create PR" button creates a real GitHub PR

# 3. CLI still works
forge run "Create a hello world function" --project-dir /tmp/test-repo
# Verify: DB persists between runs
```
