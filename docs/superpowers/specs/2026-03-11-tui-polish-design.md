# TUI Polish — Design Spec

> Make the TUI so good that people choose it over the web UI every time.

**Approach:** Surgical wiring. Extend existing widgets, wire missing events, add targeted new components. No layout restructuring. All existing happy paths unchanged.

**Scope:** 10 features across 9 modified files + 2 new files (~600–900 lines).

---

## Feature 1: Copy to Clipboard

**Problem:** Zero copy support anywhere in the TUI. Users can't extract agent output, diffs, or review text.

**Design:**

- `c` key enters **copy mode** — a temporary overlay on AgentOutput (like Vim visual mode)
- In copy mode:
  - Lines display with `○` (unselected) / `●` (selected) markers
  - `j/k` moves cursor, `space` toggles line selection
  - `Enter` copies selected lines to clipboard via `pyperclip`, exits mode
  - `Esc` exits without copying
  - Status bar at bottom shows: `── COPY MODE ── j/k: move │ space: toggle │ Enter: copy │ Esc: cancel ──` and selected line count
- `C` (shift+c) — instant copy of ALL currently visible output. No mode entry, immediate clipboard write.
- Clipboard library: `pyperclip` (cross-platform, handles macOS pbcopy / Linux xclip / Windows clip)

**Files:**
- New: `forge/tui/widgets/copy_overlay.py` — CopyOverlay widget
- Modify: `forge/tui/screens/pipeline.py` — add `c`/`C` bindings, mount CopyOverlay on AgentOutput

---

## Feature 2: Contracts Phase Display

**Problem:** The daemon emits `contracts:output` events during contract generation, but the TUI ignores them. Users stare at a blank screen during the contracts phase.

**Design:**

- TuiState gets a new field: `contracts_output: list[str]`
- Three new event handlers in `_EVENT_MAP`:
  - `contracts:output` → `_on_contracts_output` — appends line to `contracts_output`
  - `pipeline:contracts_ready` → `_on_contracts_ready` — stores readiness flag
  - `pipeline:contracts_failed` → `_on_contracts_failed` — stores error, sets `error` field
- When `phase == "contracts"`, PipelineScreen tells AgentOutput to display `contracts_output` lines with header `"⚙ Contracts"` (same pattern as planner output during planning phase)

**Files:**
- Modify: `forge/tui/state.py` — 3 new handlers + `contracts_output` field
- Modify: `forge/tui/screens/pipeline.py` — contracts view branch in `_refresh_all`

---

## Feature 3: Wire All Missing Event Handlers

**Problem:** 18 of 29 registered `TUI_EVENT_TYPES` are silently dropped — no handler in `TuiState._EVENT_MAP`.

**Design:**

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
| `pipeline:pr_created` | `pr_url: str` | Inline banner on FinalApproval |
| `pipeline:pr_failed` | `pr_error: str` | Error detail on FinalApproval |
| `pipeline:worktrees_cleaned` | no-op log | Debug visibility |
| `pipeline:preflight_failed` | `preflight_error: str` | Error notification |
| `followup:task_started` | followup tracking | Followup task progress |
| `followup:task_completed` | followup tracking | Followup task completion |
| `followup:task_error` | followup tracking | Followup task error |
| `followup:agent_output` | followup output lines | Stream in AgentOutput if viewing followup |

Every handler calls `_notify(field)` to trigger UI refresh. Handlers that update phase use the existing `_on_phase_changed` pattern.

**Files:**
- Modify: `forge/tui/state.py` — add all 18 handlers to `_EVENT_MAP`

---

## Feature 4: Files Changed Indicator

**Problem:** The `task:files_changed` event carries file lists but the TUI never displays them.

**Design:**

- `task:files_changed` handler stores `task["files_changed"]: list[str]` in TuiState
- TaskList `format_task_line()` appends dim file count: `[#8b949e]N files[/]` after the task title (only for tasks that have reported file changes)
- When an errored task is selected, AgentOutput header shows the full file list (one per line, scrollable)
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
- If no estimate is available (backwards compatibility), the cost line is omitted
- Uses amber color `#d29922` for the estimate line to draw attention without alarming

**Files:**
- Modify: `forge/tui/state.py` — `_on_cost_estimate` handler
- Modify: `forge/tui/screens/plan_approval.py` — render cost estimate in header

---

## Feature 6: Error Recovery UX

**Problem:** Errors show as a toast notification that disappears in seconds. No persistent error state, no retry option.

**Design:**

- **Error badge in TaskList:** Error tasks already show `✖` icon in red. Add a persistent `⚠` badge suffix so errors are visible even when scrolled past. Format: `✖ Task title ⚠ N files`
- **Error detail view:** When an errored task is selected, AgentOutput shows:
  1. Header: `✖ Task Title — ERROR` (red)
  2. Separator
  3. Error message from `task["error"]` (red text)
  4. Separator with label `── Last output ──`
  5. Last N lines of agent output (context for what went wrong)
  6. Action bar: `[r] retry  [s] skip  [Esc] dismiss`
- **Actions:**
  - `r` (retry) — calls daemon API to re-enqueue task, resets state to `todo`
  - `s` (skip) — marks task as `cancelled`, pipeline continues with remaining tasks
  - `Esc` — navigates away, error badge stays visible in TaskList
- Error message stored in `task["error"]` from `task:state_changed` event's `error` field

**Files:**
- Modify: `forge/tui/widgets/task_list.py` — `⚠` badge for error tasks
- Modify: `forge/tui/screens/pipeline.py` — `r`/`s` bindings, error detail rendering
- Modify: `forge/tui/widgets/agent_output.py` — error detail view mode
- Modify: `forge/tui/state.py` — store error message in task dict

---

## Feature 7: Review Auto-Transition

**Problem:** ReviewScreen doesn't auto-open when a task enters review. Users miss the review step entirely.

**Design:**

- In PipelineScreen's `_on_state_change` callback, when `task:state_changed` fires with `state: "in_review"`:
  1. Auto-select that task in the TaskList
  2. Push ReviewScreen (if not already on ReviewScreen)
- Guard: only auto-transitions if current screen is PipelineScreen (not if user is on settings, etc.)
- If multiple tasks enter review simultaneously, select the first one chronologically
- Review gate updates (`task:review_update`) stream into the review view in real-time — gate results appear as they complete
- When review completes and task moves to `awaiting_approval`, ReviewScreen shows approve/reject actions (already implemented)

**Files:**
- Modify: `forge/tui/screens/pipeline.py` — auto-transition logic in `_on_state_change`

---

## Feature 8: Fix Lint Auto-Fix Loop

**Problem:** `ruff check --fix` runs with `capture_output=True` and the diff is never shown to the agent. When ruff silently removes an unused import, the agent has no visibility into what changed, re-adds the import on retry, and loops infinitely.

**Design:**

- After `ruff check --fix` completes in `_gate1()`, run `git diff` in the worktree to capture what ruff changed
- Include the diff summary (capped at 20 lines) in the `task:review_update` event payload as `auto_fix_summary: str`
- When building retry feedback for the agent, prepend: `"Ruff auto-fixed the following changes (do not revert): \n{summary}"`
- This gives the agent explicit visibility into what the linter changed so it doesn't fight the auto-fixer

**Files:**
- Modify: `forge/core/daemon_review.py` — gate1 lint step: capture + surface ruff diff

---

## Feature 9: Pipeline History Navigation

**Problem:** Recent pipelines list on HomeScreen is read-only. Users can see past pipelines but can't open or inspect them.

**Design:**

- Replace the `Static` recent-list widget with a new `PipelineList` widget (follows TaskList's selection pattern)
- `j/k` or arrow keys navigate the list when pipeline list is focused
- `Tab` switches focus between prompt TextArea and PipelineList
- `Enter` on a pipeline:
  1. Loads pipeline state from DB (tasks, events, costs, diffs)
  2. Hydrates a TuiState from the stored events
  3. Pushes PipelineScreen in **read-only replay mode** — shows final task states, agent output, diffs, costs
  4. No re-execution possible from replay mode
- PipelineList reuses the same status icons/colors as the HomeScreen static list

**Files:**
- New: `forge/tui/widgets/pipeline_list.py` — PipelineList widget
- Modify: `forge/tui/screens/home.py` — replace Static with PipelineList, add Tab/Enter bindings
- Modify: `forge/tui/app.py` — load pipeline from DB, hydrate state for replay

---

## Feature 10: Logo Redesign

**Problem:** Current logo is 6 lines tall with a basic triangle shape. Too small and plain for a landing screen.

**Design:**

- Larger ASCII art: ~10-12 lines tall with a proper anvil/forge shape
- Block-letter "FORGE" text below the anvil — bold, fills width
- Keep the existing color scheme: orange `#f0883e` for the anvil, blue `#58a6ff` for the text
- Gray `#8b949e` subtitle: "multi-agent code orchestration"
- **Must be properly centered** on the HomeScreen — both horizontally and vertically within the available space
- The logo widget uses Textual's `Center` container and the `text-align: center` CSS property

**Files:**
- Modify: `forge/tui/widgets/logo.py` — new ASCII art, ensure proper centering

---

## Architecture Notes

**Event flow (unchanged):**
```
Daemon → EventEmitter → EmbeddedSource → EventBus → TuiState → Widgets
```

All 10 features plug into this existing pipeline. No new architectural patterns needed.

**New dependencies:**
- `pyperclip` — clipboard access (add to `pyproject.toml`)

**Testing:**
- Co-located `*_test.py` files for all modified modules
- Copy mode: test overlay mount/unmount, line selection, clipboard mock
- Event handlers: test each new handler sets the correct field
- Pipeline list: test navigation, selection, replay mode hydration
- Error recovery: test retry/skip actions update task state
- Lint loop fix: test that auto-fix diff is included in review feedback

---

## File Change Summary

| File | Change Level | Features |
|------|-------------|----------|
| `forge/tui/state.py` | Heavy | 2, 3, 4, 5, 6 |
| `forge/tui/screens/pipeline.py` | Heavy | 1, 2, 6, 7 |
| `forge/tui/widgets/copy_overlay.py` | New | 1 |
| `forge/tui/widgets/pipeline_list.py` | New | 9 |
| `forge/tui/widgets/task_list.py` | Moderate | 4, 6 |
| `forge/tui/widgets/agent_output.py` | Moderate | 2, 6 |
| `forge/tui/screens/plan_approval.py` | Light | 5 |
| `forge/tui/screens/home.py` | Moderate | 9 |
| `forge/tui/widgets/progress_bar.py` | Light | 3 (budget warning) |
| `forge/tui/widgets/logo.py` | Moderate | 10 |
| `forge/tui/app.py` | Light | 9 |
| `forge/core/daemon_review.py` | Light | 8 |
