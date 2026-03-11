# TUI Polish — Design Spec

> Make the TUI so good that people choose it over the web UI every time.

**Approach:** Surgical wiring. Extend existing widgets, wire missing events, add targeted new components. No layout restructuring. All existing happy paths unchanged.

**Scope:** 10 features across 10 modified files + 2 new files (~600–900 lines).

---

## Feature 1: Copy to Clipboard

**Problem:** Zero copy support anywhere in the TUI. Users can't extract agent output, diffs, or review text.

**Design:**

- **Key binding change:** The existing `c` binding (`action_view_chat`) is relocated to `t` (for "talk/thread"). This frees `c` for copy mode, which is more universally expected.
- `c` key enters **copy mode** — a temporary overlay on AgentOutput (like Vim visual mode)
- In copy mode:
  - Lines display with `○` (unselected) / `●` (selected) markers
  - `j/k` moves cursor, `space` toggles line selection
  - `Enter` copies selected lines to clipboard, exits mode
  - `Esc` exits without copying
  - Status bar at bottom shows: `── COPY MODE ── j/k: move │ space: toggle │ Enter: copy │ Esc: cancel ──` and selected line count
- `C` (shift+c) — instant copy of ALL currently visible output. No mode entry, immediate clipboard write.
- **Clipboard mechanism (no new dependencies):** A small helper function `copy_to_clipboard(text: str) -> bool` that tries two strategies in order:
  1. **Subprocess with platform tools:** `pbcopy` on macOS, `xclip -selection clipboard` on Linux, `clip` on Windows. Uses `subprocess.Popen` with `stdin=PIPE`. This gives true system clipboard access.
  2. **Textual built-in fallback:** `app.copy_to_clipboard(text)` which uses OSC 52 terminal escape sequences. Works in most modern terminals (iTerm2, kitty, alacritty, WezTerm) but not macOS Terminal.app.
- **Clipboard failure handling:** If both strategies fail, show a persistent notification: "Clipboard unavailable — install xclip or xsel". Do not crash. The helper returns `False` on failure so the caller can show the notification.

**Files:**
- New: `forge/tui/widgets/copy_overlay.py` — CopyOverlay widget
- Modify: `forge/tui/screens/pipeline.py` — relocate `c` → `t` for chat view, add `c`/`C` bindings for copy, mount CopyOverlay on AgentOutput

---

## Feature 2: Contracts Phase Display

**Problem:** The daemon emits `contracts:output` events during contract generation, but the TUI ignores them. Users stare at a hardcoded placeholder during the contracts phase.

**Design:**

- TuiState gets a new field: `contracts_output: list[str]`
- Three new event handlers in `_EVENT_MAP`:
  - `contracts:output` → `_on_contracts_output` — appends line to `contracts_output`
  - `pipeline:contracts_ready` → `_on_contracts_ready` — stores readiness flag
  - `pipeline:contracts_failed` → `_on_contracts_failed` — stores error, sets `error` field
- **Replace** the existing hardcoded contracts display in `pipeline.py` `_refresh_all()` (lines 356–361, the `elif state.phase == "contracts"` branch with placeholder text) with a streaming view that reads from `state.contracts_output` with header `"⚙ Contracts"`
- Pattern: identical to how `planner_output` displays during `phase == "planning"`

**Files:**
- Modify: `forge/tui/state.py` — 3 new handlers + `contracts_output` field
- Modify: `forge/tui/screens/pipeline.py` — replace hardcoded contracts branch in `_refresh_all` with streaming view

---

## Feature 3: Wire All Missing Event Handlers

**Problem:** 19 of 43 unique registered `TUI_EVENT_TYPES` have no handler in `TuiState._EVENT_MAP`. They are silently dropped.

**Note:** `TUI_EVENT_TYPES` in `bus.py` contains duplicate entries for `pipeline:pr_created` and `pipeline:pr_failed` (lines 34+58 and 35+59). Clean up these duplicates as part of this task.

**Currently handled (24 events):** `pipeline:phase_changed`, `pipeline:plan_ready`, `pipeline:cost_update`, `pipeline:error`, `pipeline:all_tasks_done`, `pipeline:pr_creating`, `pipeline:pr_created`, `pipeline:pr_failed`, `task:state_changed`, `task:agent_output`, `task:cost_update`, `task:review_update`, `task:merge_result`, `task:awaiting_approval`, `task:question`, `task:answer`, `task:resumed`, `task:auto_decided`, `planner:output`, `review:gate_started`, `review:gate_passed`, `review:gate_failed`, `review:llm_feedback`, `review:llm_output`.

**Missing (19 events to add):**

| Event | TuiState Field | UI Effect |
|-------|---------------|-----------|
| `pipeline:cost_estimate` | `cost_estimate: dict` | Shown on PlanApprovalScreen (Feature 5) |
| `pipeline:budget_exceeded` | `budget_exceeded: bool` | Warning in PipelineProgress bar |
| `contracts:output` | `contracts_output` | AgentOutput during contracts (Feature 2) |
| `pipeline:contracts_ready` | `contracts_ready: bool` | Phase transition indicator |
| `pipeline:contracts_failed` | `contracts_failed: str` | Error notification |
| `task:files_changed` | `task["files_changed"]` | File count in TaskList (Feature 4) |
| `pipeline:cancelled` | phase → `"cancelled"` | PipelineProgress status |
| `pipeline:paused` | phase → `"paused"` | PipelineProgress status |
| `pipeline:resumed` | phase → `"executing"` | PipelineProgress status |
| `pipeline:restarted` | phase → `"planning"` | Reset + restart |
| `pipeline:worktrees_cleaned` | no-op log | Debug visibility only |
| `pipeline:preflight_failed` | `preflight_error: str` | Error notification |
| `followup:task_started` | followup tracking | Followup task progress |
| `followup:task_completed` | followup tracking | Followup task completion |
| `followup:task_error` | followup tracking | Followup task error |
| `followup:agent_output` | followup output lines | Stream in AgentOutput if viewing followup |
| `slot:acquired` | no-op log | Debug visibility only |
| `slot:released` | no-op log | Debug visibility only |
| `slot:queued` | no-op log | Debug visibility only |

Every handler calls `_notify(field)` to trigger UI refresh. No-op handlers log at DEBUG level and return. Handlers that update phase use the existing `_on_phase_changed` pattern.

**Files:**
- Modify: `forge/tui/state.py` — add 19 handlers to `_EVENT_MAP`
- Modify: `forge/tui/bus.py` — remove duplicate `pipeline:pr_created` and `pipeline:pr_failed` entries

---

## Feature 4: Files Changed Indicator

**Problem:** The `task:files_changed` event carries file lists but the TUI never displays them.

**Design:**

- `task:files_changed` handler stores `task["files_changed"]: list[str]` in TuiState
- TaskList `format_task_line()` appends dim file count: `[#8b949e]N files[/]` after the task title (for all task states, not just errored tasks — seeing which files a completed task modified is also useful)
- When any task is selected, AgentOutput header can include file count summary. Full file list is visible in the diff view (`d` key) which already shows changed files.
- File count display respects the TaskList max-width — truncates title if needed to fit count

**Files:**
- Modify: `forge/tui/state.py` — `_on_files_changed` handler
- Modify: `forge/tui/widgets/task_list.py` — file count in `format_task_line()`

---

## Feature 5: Cost Estimate Before Execution

**Problem:** The planner computes a cost estimate but the TUI never shows it. Users approve plans blind.

**Design:**

- `pipeline:cost_estimate` event → `TuiState.cost_estimate: dict | None` with `{"min_usd": float, "max_usd": float}`
- PlanApprovalScreen reads `state.cost_estimate` and renders in the header summary:
  ```
  PLAN REVIEW  5 tasks · 2 low · 2 medium · 1 high
  💰 Estimated cost: $3.50 – $5.20
  ```
- If the estimate contains only a single value (legacy `{"estimated_cost": float}`), display as `~$X.XX` instead of a range
- If no estimate is available (backwards compatibility), the cost line is omitted
- Uses amber color `#d29922` for the estimate line to draw attention without alarming
- PlanApprovalScreen constructor already accepts cost data — update it to read from `state.cost_estimate` dict instead of a single float

**Files:**
- Modify: `forge/tui/state.py` — `_on_cost_estimate` handler
- Modify: `forge/tui/screens/plan_approval.py` — render cost estimate range in header

---

## Feature 6: Error Recovery UX

**Problem:** Errors show as a toast notification that disappears in seconds. No persistent error state, no retry option.

**Design:**

- **Key binding change:** The existing `r` binding (`action_view_review`) is relocated to `v` (for "review view"). This frees `r` for retry, which is more intuitive for error recovery. The `r` binding is only active when the selected task is in error state; otherwise `r` does nothing (prevents accidental triggers).
- **Error badge in TaskList:** Error tasks already show `✖` icon in red. Add a persistent `⚠` badge suffix so errors are visible even when scrolled past. Format: `✖ Task title ⚠ N files`
- **Error detail view in AgentOutput:** When an errored task is selected, AgentOutput shows a **combined view** with both error info and file changes:
  1. Header: `✖ Task Title — ERROR` (red)
  2. Separator
  3. Error message from `task["error"]` (red text)
  4. If `task["files_changed"]` exists: file list (dim, shows what the task touched before failing)
  5. Separator with label `── Last output ──`
  6. Last 20 lines of agent output (context for what went wrong)
  7. Action bar: `[r] retry  [s] skip  [Esc] dismiss`
  - This resolves the overlap with Feature 4 — file info is integrated into the error detail view for errored tasks.
- **Actions — daemon interaction mechanism:**
  - `r` (retry): Emits `task:retry` event through the EventBus → daemon's `retry_task(task_id)` method resets the task state to `todo` in DB, clears the worktree, and re-adds the task to the scheduler queue. The `task:state_changed` event fires with `state: "todo"`, and the task re-enters the execution loop on the next scheduler tick. If `retry_task()` doesn't exist yet, create it in `forge/core/daemon.py` (it's a simple state reset + re-enqueue).
  - `s` (skip): Emits `task:skip` event → daemon's `cancel_task(task_id)` method (already exists) marks the task as `cancelled` in DB. Pipeline continues with remaining tasks. Dependent tasks that reference the skipped task should also be cancelled (scheduler already handles this via `depends_on` resolution).
  - `Esc` — navigates away (selects next non-error task), error badge stays visible in TaskList
- Error message stored in `task["error"]` from `task:state_changed` event's `error` field (already emitted by daemon when tasks fail)

**Files:**
- Modify: `forge/tui/widgets/task_list.py` — `⚠` badge for error tasks
- Modify: `forge/tui/screens/pipeline.py` — relocate `r` → `v` for review view, add `r`/`s` bindings for error actions, error detail rendering in AgentOutput
- Modify: `forge/tui/widgets/agent_output.py` — error detail view mode with combined error + files + output
- Modify: `forge/tui/state.py` — store error message in task dict
- Modify: `forge/core/daemon.py` — add `retry_task(task_id)` method if not present

---

## Feature 7: Review Auto-Transition

**Problem:** ReviewScreen doesn't auto-open when a task enters review. Users miss the review step entirely.

**Design:**

- In PipelineScreen's `_on_state_change` callback, when `task:state_changed` fires with `state: "in_review"`:
  1. Auto-select that task in the TaskList
  2. Push ReviewScreen (if not already on ReviewScreen)
- **Guards:**
  - Only auto-transitions if current screen is PipelineScreen (not if user is on settings, plan approval, etc.)
  - If the user is actively typing in ChatThread (has focus on an input widget), **do not auto-transition**. Instead, show a notification: "Task X entered review — press `v` to view." This prevents interrupting user input.
- If multiple tasks enter review simultaneously, select the first one chronologically
- Review gate updates (`task:review_update`) stream into the review view in real-time — gate results appear as they complete. ReviewScreen already renders gates from `state.review_gates[tid]`, so no changes to `review.py` are needed — just ensure `_refresh_all` is called when review gates update.
- When review completes and task moves to `awaiting_approval`, ReviewScreen shows approve/reject actions (already implemented)

**Files:**
- Modify: `forge/tui/screens/pipeline.py` — auto-transition logic in `_on_state_change`, input-focus guard

---

## Feature 8: Fix Lint Auto-Fix Loop

**Problem:** `ruff check --fix` runs with `capture_output=True` and the diff is never shown to the agent. When ruff silently removes an unused import, the agent has no visibility into what changed, re-adds the import on retry, and loops infinitely.

**Design:**

- In `_gate1()` (daemon_review.py lines 538–555), after `ruff check --fix` runs and **before** the `git add -A` + `git commit`:
  1. Run `_run_git(["diff"], cwd=worktree_path)` to capture what ruff changed (unstaged diff)
  2. Store the diff output (capped at 30 lines) as `auto_fix_diff: str`
  3. Proceed with the existing `git add -A` + `git commit` flow
- Include `auto_fix_diff` in the `GateResult.details` when the gate passes with auto-fixes applied. The details string becomes: `"Lint clean (auto-fixed: {summary})"` instead of just `"Lint clean"`
- The retry feedback mechanism is in `_run_review()` (the caller), which already includes `gate.details` in the feedback sent to the agent. So the auto-fix diff naturally flows into the agent's retry context without needing a separate code path.
- Cap at 30 lines to avoid flooding agent context with large diffs

**Files:**
- Modify: `forge/core/daemon_review.py` — gate1 lint step: capture unstaged diff before commit, include in GateResult details

---

## Feature 9: Pipeline History Navigation

**Problem:** Recent pipelines list on HomeScreen is read-only. Users can see past pipelines but can't open or inspect them.

**Design:**

- Replace the `Static` recent-list widget with a new `PipelineList` widget (follows TaskList's selection pattern)
- `j/k` or arrow keys navigate the list when pipeline list is focused
- `Tab` switches focus between prompt TextArea and PipelineList
- `Enter` on a pipeline:
  1. Calls `db.get_pipeline(pipeline_id)` to get pipeline metadata
  2. Calls `db.list_tasks_by_pipeline(pipeline_id)` to get all tasks
  3. Calls `db.list_events(pipeline_id)` to get all stored events
  4. Creates a fresh `TuiState` and replays events via `state.apply_event()` in chronological order — this hydrates the full state (tasks, outputs, diffs, review gates, costs) exactly as it existed
  5. Pushes PipelineScreen with a `read_only=True` flag
- **Read-only mode enforcement:**
  - PipelineScreen checks `self._read_only` flag
  - Retry (`r`), skip (`s`), approve actions are disabled (bindings removed or no-op)
  - A dim banner at top: `"📖 Viewing pipeline from {date} — press Esc to return"`
  - `Esc` pops back to HomeScreen
- **DB methods needed:** `db.get_pipeline()`, `db.list_tasks_by_pipeline()`, and `db.list_events()` — verify these exist. If `list_events` doesn't exist, add it (simple `SELECT * FROM events WHERE pipeline_id = ? ORDER BY created_at`).
- PipelineList reuses the same status icons/colors as the HomeScreen static list

**Files:**
- New: `forge/tui/widgets/pipeline_list.py` — PipelineList widget
- Modify: `forge/tui/screens/home.py` — replace Static with PipelineList, add Tab/Enter bindings
- Modify: `forge/tui/screens/pipeline.py` — add `read_only` flag, disable mutation bindings, show banner
- Modify: `forge/tui/app.py` — load pipeline from DB, hydrate state, push in replay mode
- Possibly modify: `forge/storage/db.py` — add `list_events()` if missing

---

## Feature 10: Logo Redesign

**Problem:** Current logo is 6 lines tall with a basic triangle shape. Too small and plain for a landing screen.

**Design:**

- Larger ASCII art: ~10-12 lines tall with a proper anvil/forge shape
- Block-letter "FORGE" text below the anvil — bold, fills width
- Keep the existing color scheme: orange `#f0883e` for the anvil, blue `#58a6ff` for the text
- Gray `#8b949e` subtitle: "multi-agent code orchestration"
- **Must be properly centered** on the HomeScreen — both horizontally and vertically within the available space
- Centering approach: use Textual's `content-align: center middle` on the logo container, with each line of the ASCII art left-padded to produce a centered block. The `ForgeLogo` widget sets `text-align: center` in its CSS.
- The exact ASCII art will be designed during implementation — the spec defines scale and constraints, not the pixel art. Implementer should produce 2-3 options for review.

**Files:**
- Modify: `forge/tui/widgets/logo.py` — new ASCII art, ensure proper centering
- Modify: `forge/tui/screens/home.py` — verify logo centering in the home layout

---

## Binding Summary

Updated PipelineScreen key bindings after Features 1, 6, and 7:

| Key | Before | After |
|-----|--------|-------|
| `o` | Output view | Output view (unchanged) |
| `c` | Chat view | **Copy mode** (Feature 1) |
| `t` | — | **Chat view** (relocated from `c`) |
| `d` | Diff view | Diff view (unchanged) |
| `r` | Review view | **Retry errored task** (Feature 6, only when error task selected) |
| `v` | — | **Review view** (relocated from `r`) |
| `s` | — | **Skip errored task** (Feature 6, only when error task selected) |
| `C` | — | **Copy all** (Feature 1) |
| `g` | Toggle DAG | Toggle DAG (unchanged) |
| `j/k` | Navigate tasks | Navigate tasks (unchanged) |

---

## Architecture Notes

**Event flow (unchanged):**
```
Daemon → EventEmitter → EmbeddedSource → EventBus → TuiState → Widgets
```

All 10 features plug into this existing pipeline. No new architectural patterns needed.

**New dependencies:**
- None. Clipboard uses subprocess (`pbcopy`/`xclip`/`clip`) + Textual's built-in `copy_to_clipboard()` as fallback.

**Testing:**
- Co-located `*_test.py` files for all modified modules
- Copy mode: test overlay mount/unmount, line selection, clipboard mock (mock subprocess.Popen), failure handling
- Event handlers: test each new handler sets the correct field
- Pipeline list: test navigation, selection, replay mode hydration via event replay
- Error recovery: test retry/skip actions update task state, test binding guards (r/s only active on error tasks)
- Lint loop fix: test that auto-fix diff is included in GateResult details
- Key binding changes: test that `t` opens chat, `v` opens review, `c` enters copy mode

---

## File Change Summary

| File | Change Level | Features |
|------|-------------|----------|
| `forge/tui/state.py` | Heavy | 2, 3, 4, 5, 6 |
| `forge/tui/screens/pipeline.py` | Heavy | 1, 2, 6, 7, 9 (read-only flag) |
| `forge/tui/widgets/copy_overlay.py` | New | 1 |
| `forge/tui/widgets/pipeline_list.py` | New | 9 |
| `forge/tui/widgets/task_list.py` | Moderate | 4, 6 |
| `forge/tui/widgets/agent_output.py` | Moderate | 2, 6 |
| `forge/tui/screens/plan_approval.py` | Light | 5 |
| `forge/tui/screens/home.py` | Moderate | 9, 10 |
| `forge/tui/widgets/progress_bar.py` | Light | 3 (budget warning) |
| `forge/tui/widgets/logo.py` | Moderate | 10 |
| `forge/tui/app.py` | Moderate | 9 (replay mode) |
| `forge/tui/bus.py` | Light | 3 (remove duplicates) |
| `forge/core/daemon_review.py` | Light | 8 |
| `forge/core/daemon.py` | Light | 6 (retry_task method) |
| `forge/storage/db.py` | Light | 9 (list_events if missing) |
