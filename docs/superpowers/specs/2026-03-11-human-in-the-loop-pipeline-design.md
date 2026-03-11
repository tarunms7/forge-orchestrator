# Human-in-the-Loop Pipeline & TUI Overhaul

**Date:** 2026-03-11
**Status:** Approved

## Problem

Forge agents run fully autonomous with no way for humans to provide input during execution. Agents assume instead of asking. The TUI shows no review output, no PR creation stage, no final approval. The UI is pale with no colors or status indicators. Nothing feels like a real, usable product.

## Design Decisions

| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Primary surface | TUI-first | Forge's identity lives in the terminal |
| Interaction model | Decision Queue + Chat | Reliable, DB-backed, proven SDK path |
| SDK pattern | `query()` + `resume=session_id` | Stateless per turn, crash-recoverable, no persistent connections |
| Autonomy | 3-level setting (full/balanced/supervised) | Users pick their comfort level |
| Question detection | Structured JSON in agent output | `FORGE_QUESTION:` marker parsed by daemon |

## Architecture

### Agent Execution Loop

```
START (sdk_query)
  → WORKING (on_message streams tool calls to TUI)
  → Agent decides:
      ├─ Confident → CONTINUES → COMPLETE → review gates
      └─ Uncertain → outputs FORGE_QUESTION JSON
                   → query() returns
                   → question saved to DB
                   → task.state = "awaiting_input"
                   → execution slot released
                   → user answers in TUI
                   → new query(resume=session_id)
                   → agent picks up where it left off
```

### Key Principle: Stateless Resume

Each turn is an independent `sdk_query()` call. No bidirectional streaming, no `ClaudeSDKClient`, no persistent connections. The `resume=session_id` parameter on `ClaudeCodeOptions` tells the SDK to continue the prior conversation. If a session expires, fall back to a fresh `query()` with conversation history injected into the prompt.

### Slot Management

Paused agents (state=`awaiting_input`) release their execution slot. A slot represents a live SDK subprocess (~300-500MB RAM). Paused tasks are just DB rows — no memory, no CPU. This means `max_agents=2` running + N agents paused simultaneously. Memory stays bounded.

## 7-Stage Pipeline

| # | Stage | Type | What User Sees |
|---|-------|------|----------------|
| 1 | 🧠 Planning | Auto | Live tool activity streaming (Read, Grep, Bash calls) |
| 2 | ✋ Plan Approval | Human | Task graph, cost estimate. Approve / Edit / Cancel |
| 3 | 📋 Contracts | Auto | "Generating contracts..." with progress |
| 4 | ⚡ Execution | Auto+Human | Tasks running with live output + chat threads for questions |
| 5 | 🔍 Review | Auto | Per-task gate results: Build → Lint → Test → LLM Review |
| 6 | ✅ Final Approval | Human | Full diff summary, stats. Create PR / View Diff / Re-run |
| 7 | 🚀 PR Created | Auto | Push branch, generate title+body, gh pr create, show link |

## Edge Cases (13 resolved)

### 1. Multiple agents paused simultaneously
Questions sorted by task dependency order (upstream first), then by time. TUI shows `🔔 N DECISIONS` badge. `Tab` cycles through them.

### 2. Paused agent slot management
Paused agents release execution slots. Slot = live SDK process. When answered, task re-queues for a slot.

### 3. Chatty agent (too many questions)
Per-task question limit (configurable, default: 3 per execution cycle). System prompt tells agent: "You have N questions remaining." After limit, agent must proceed with best judgment. Resets on each retry.

### 4. User never answers
Question timeout (configurable, default: 30 minutes). After timeout, agent auto-proceeds with best judgment. Logs: "No response received — proceeding with [choice]."

### 5. Session ID expiration
SDK sessions persist on disk. If `resume` fails, fall back to fresh `query()` with full conversation history in prompt.

### 6. Ambiguous answer / follow-up needed
Agent can ask follow-up questions (counted against question limit). Chat thread shows full back-and-forth.

### 7. Cross-task question dependencies
Handled by existing task dependency graph + contracts. Dependent tasks don't start until dependencies complete.

### 8. Pipeline timeout vs paused time
Paused time does NOT count against pipeline timeout. Timer pauses when only paused tasks remain.

### 9. Autonomy level vs question impact
System prompt gives clear guidelines per level. See Agent System Prompt section.

### 10. User changes answer after submitting
Before agent resumes (while `awaiting_input`), user can edit. Once agent is running, answer is locked — cancel and retry instead.

### 11. "Let agent decide" propagation
Two flavors: skip this question, or "auto-decide all" for this task (temporary full autonomy via `Ctrl+A`).

### 12. Crash during answer submission
Answer written to DB first, then `query()` called. On restart, daemon sees `awaiting_input` + answer present → auto-triggers resume.

### 13. Bad output after human guidance
Normal review pipeline catches it. Q&A pair included in review context so LLM reviewer knows human intent.

## Database Schema

All models live in `forge/storage/db.py` using SQLAlchemy 2.0 `Mapped[]` columns. The project uses `_add_missing_columns()` for auto-migration (no Alembic) — new columns are automatically added on startup via schema introspection.

### TaskRow — forge/storage/db.py (modified)

Add `AWAITING_INPUT = "awaiting_input"` to `TaskState` enum in `forge/core/models.py`. This is distinct from the existing `AWAITING_APPROVAL` (which is for plan approval, not agent questions).

```python
# New columns on TaskRow:
session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
questions_asked: Mapped[int] = mapped_column(default=0)
questions_limit: Mapped[int] = mapped_column(default=3)
```

### TaskQuestionRow — forge/storage/db.py (new model)

```python
class TaskQuestionRow(Base):
    __tablename__ = "task_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    suggestions: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)  # JSON array
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    answered_by: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)  # human|agent_auto|timeout
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)  # JSON
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.now(timezone.utc).isoformat())
    answered_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
```

Add `TaskQuestionRow` to the `_ALL_MODELS` tuple so `_add_missing_columns` picks it up.

### PipelineRow — forge/storage/db.py (modified)

`pr_url` already exists. Add:

```python
# New columns (paused: bool already exists, keep it):
paused_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)  # ISO datetime
paused_duration: Mapped[float] = mapped_column(default=0.0)  # cumulative seconds
```

`paused_duration` is computed: when `pipeline:paused` fires, set `paused_at = now()`. When any task resumes, add `(now - paused_at)` to `paused_duration` and clear `paused_at`.

### ForgeSettings — forge/config/settings.py (modified)

```python
# New fields on ForgeSettings (Pydantic, env-based, global):
autonomy: str = "balanced"  # full | balanced | supervised
question_limit: int = 3     # per task per execution cycle
question_timeout: int = 1800 # seconds before auto-decide (30m)
auto_pr: bool = False        # skip final approval, auto-create PR
```

These are global defaults. Per-pipeline override is out of scope for v1.

## Agent System Prompt — Question Protocol

Injected into every agent's system prompt based on autonomy level:

```
## Human Interaction Protocol

You are working within Forge, a multi-agent orchestrator.
Autonomy level: {autonomy} | Questions remaining: {remaining}

### When to ask questions:
IF autonomy == "supervised":
    Ask when uncertain about ANY implementation choice.
IF autonomy == "balanced":
    Ask ONLY for high-impact decisions:
    - Architecture patterns (which auth strategy, which ORM)
    - Ambiguous requirements (spec says X but codebase does Y)
    - Destructive changes (deleting files, dropping columns)
    Do NOT ask about: naming, formatting, minor style choices
IF autonomy == "full":
    NEVER ask questions. Make your best judgment.

### How to ask:
When you need human input, output EXACTLY this JSON block
as your final message, then STOP:

FORGE_QUESTION:
{
  "question": "Which ORM pattern should I follow?",
  "context": "Found 2 patterns in codebase: ...",
  "suggestions": ["SQLAlchemy 2.0", "Raw SQL"],
  "impact": "high"
}

### Rules:
- You have {remaining} questions left. Use them wisely.
- ALWAYS provide 2-3 concrete suggestions.
- ALWAYS explain what you found that led to the question.
- NEVER ask open-ended "what should I do?" questions.
- If you hit 0 remaining, proceed with best judgment.
```

## Question Detection & Resume

### Detection (daemon_executor.py)

```python
async def _execute_task(task, ...):
    result = await sdk_query(prompt, options, on_message=on_msg)

    # Check if agent output ends with a question
    question = _parse_forge_question(result.text)
    if question:
        q_record = await db.create_task_question(
            task_id=task.id,
            pipeline_id=pipeline_id,
            question=question["question"],
            suggestions=question.get("suggestions"),
            context=question.get("context"),
        )
        task.state = "awaiting_input"
        task.session_id = result.session_id
        task.questions_asked += 1
        await db.update_task(task)
        await emit("task:question", {"task_id": task.id, "question": q_record})
        return  # release execution slot

    # Normal completion → proceed to review
    ...
```

### Resume (daemon_executor.py)

```python
async def _resume_task(task, answer: str):
    result = await sdk_query(
        prompt=answer,
        options=ClaudeCodeOptions(
            resume=task.session_id,
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            permission_mode="acceptEdits",
            max_turns=25,
        ),
        on_message=on_msg,
    )
    task.state = "running"

    # May ask another question or complete
    question = _parse_forge_question(result.text)
    if question and task.questions_asked < task.questions_limit:
        # Same question handling as above
        ...
    else:
        # Proceed to review
        ...
```

### Timeout Handler

```python
async def _check_question_timeouts():
    """Periodic task checking for expired questions."""
    expired = await db.get_expired_questions(timeout=settings.question_timeout)
    for q in expired:
        q.answer = "Proceed with your best judgment."
        q.answered_by = "timeout"
        await db.update_question(q)
        await _resume_task(q.task, q.answer)
```

## EventBus — New Events

### Question Events
- `task:question` — agent asked a question (payload: task_id, question record)
- `task:answer` — user submitted answer (payload: task_id, answer text)
- `task:resumed` — agent resumed with answer (payload: task_id)
- `task:auto_decided` — timeout or budget hit (payload: task_id, reason)

### Pipeline Events
- `pipeline:all_tasks_done` — show final approval screen
- `pipeline:pr_creating` — push + PR creation in progress
- `pipeline:pr_created` — PR URL available (payload: pr_url)
- `pipeline:pr_failed` — push or gh failed (payload: error)

### Review Events
- `review:gate_started` — gate beginning (payload: task_id, gate_name)
- `review:gate_passed` — gate succeeded (payload: task_id, gate_name, details)
- `review:gate_failed` — gate failed (payload: task_id, gate_name, error)
- `review:llm_feedback` — LLM reviewer comments (payload: task_id, comments)

### Slot Events
- `slot:acquired` — task got execution slot
- `slot:released` — task paused or completed, slot freed
- `slot:queued` — task waiting for available slot
- `pipeline:paused` — all slots idle, questions pending

## Answer Submission Flow

The TUI runs in embedded mode (in-process). Answer submission path:

1. User types answer in `ChatThread` widget input → presses Enter
2. Widget emits Textual message `ChatThread.AnswerSubmitted(task_id, answer_text)`
3. `ForgeApp` handler writes answer to DB via `Database.answer_question(question_id, answer, "human")`
4. Handler emits `task:answer` event on EventBus
5. Handler calls `daemon.resume_task(task_id)` which acquires an execution slot and calls `sdk_query(resume=...)`

For the web API (future): add `POST /api/tasks/{task_id}/questions/{question_id}/answer` route. Out of scope for this TUI-first spec.

## TUI Layout

Textual uses character-based units, not pixels. All widths below are in Textual units (roughly 1 unit = 1 character width).

### Execution Screen (2-panel)

**Left panel (min-width: 30, max-width: 45):**
- Phase banner: stage name + 7-segment progress bar + stats (time, cost, tasks done)
- Task list: color-coded status badges (DONE=green ✓, WORKING=blue ●, INPUT=orange ◆, REVIEW=purple ◎, QUEUED=gray ○, ERROR=red ✗)
- Per-task: name, description, live activity line (current tool call or review gate)
- Bottom bar: decision count badge + keyboard shortcut hints

**Right panel (flex):**
- Header: task name + status + view toggle shortcuts (o=output, d=diff, r=review, c=chat)
- Content area switches based on view:
  - **Output view**: streaming agent tool calls and thinking
  - **Chat view**: work log + agent question + suggestion chips + input bar
  - **Diff view**: scrollable unified diff for task's changes
  - **Review view**: gate result cards (Build → Lint → Test → LLM) with details

### Final Approval Screen

- Centered success state with summary stats (lines +/-, files, time, cost, questions)
- Task summary table: per-task changes, test counts, review status
- Action buttons: Create PR (Enter), View Diff (d), Re-run (r), Cancel (Esc)

### Settings Screen (modal overlay)

- Autonomy selector: 3 radio cards (Full / Balanced / Supervised) with descriptions
- Question limits: per-task count and timeout with +/- controls
- Pipeline settings: max agents, max retries
- Completion toggles: require final approval, auto-create PR

## Keyboard Navigation

### Global (always active)
| Key | Action |
|-----|--------|
| ↑↓ | Navigate task list |
| Enter | Select / confirm |
| Tab | Jump to next 🔔 task |
| Esc | Back / cancel |
| q | Quit (with confirmation if running) |
| s | Open settings |
| ? | Show help |

### Execution Screen
| Key | Action |
|-----|--------|
| d | View diff |
| o | View output |
| r | View review gates |
| c | Open chat thread |
| 1-9 | Jump to task N |

### Chat / Question Input
| Key | Action |
|-----|--------|
| Enter | Send answer (resumes agent) |
| Esc | Let agent decide (skip) |
| ←→ | Navigate suggestion chips |
| 1 2 3 | Quick-select suggestion |
| Ctrl+A | Auto-decide all remaining for this task |

### Plan Approval
| Key | Action |
|-----|--------|
| Enter | Approve plan |
| e | Edit tasks |
| Esc | Cancel pipeline |

### Final Approval
| Key | Action |
|-----|--------|
| Enter | Create pull request |
| d | View full diff |
| r | Re-run pipeline |
| Esc | Cancel |

### Settings
| Key | Action |
|-----|--------|
| ↑↓ | Navigate settings |
| ←→ | Change value |
| Enter | Toggle |
| Esc | Close (auto-saves) |

### Focus Management
1. Task needs input → auto-focus input field
2. No input needed → focus task list panel
3. Modal open → modal captures all keys
4. Viewing diff/output → focus scrollable content
5. Tab always jumps to next 🔔 task
6. Esc always goes back one level

## PR Creation Flow (TUI)

Triggered from Final Approval screen via Enter key:

1. **Push branch** — `git push -u origin {pipeline_branch}`
2. **Generate PR title** — LLM summarizes all task descriptions into short title
3. **Generate PR body** — template with: pipeline stats, per-task summaries, human decisions (Q&A log), test results, Forge attribution
4. **Create PR** — `gh pr create --title "..." --body "..."`
5. **Save URL** — `pipeline.pr_url` updated in DB

Each step shows progress with checkmarks in TUI. If any step fails, error shown with retry option.

### PR Body Template

```markdown
## Summary
Built by Forge pipeline • {n_tasks} tasks • {time} • ${cost}

## Tasks
- ✅ **{task_name}** — +{added}/-{removed}, {n_files} files
...

## Human Decisions
- Q: {question} → A: {answer}
...

## Test Results
{total_passed}/{total_tests} tests passing across {n_tasks} tasks

🤖 Built with [Forge](https://github.com/tarunms7/forge-orchestrator)
```

## Question Detection Robustness

The `FORGE_QUESTION:` marker must appear as the last content block in the agent's output. Detection strategy:

1. Check `result.text` for `FORGE_QUESTION:` marker (case-sensitive)
2. Extract JSON after the marker — handle both raw JSON and markdown-fenced JSON (` ```json ... ``` `)
3. Validate required fields: `question` (string), `suggestions` (list of strings)
4. If marker appears mid-output (not at end), ignore it — agent continued working
5. If JSON is malformed, treat as normal completion (no question asked)

Parser location: `_parse_forge_question()` in `forge/core/daemon_helpers.py` (co-located with other parsing helpers like `_extract_activity`).

## Screen Transitions

| Event | Transition |
|-------|-----------|
| `pipeline:plan_ready` | Push `PlanApprovalScreen` |
| Plan approved | Pop to `PipelineScreen`, start contracts phase |
| `pipeline:all_tasks_done` | Push `FinalApprovalScreen` |
| PR created | Show PR URL on `FinalApprovalScreen`, update to success state |
| `task:question` | If task is selected, right panel switches to chat view automatically |

Keybinding scope: Textual's `Binding(priority=True)` for global keys. Screen-specific bindings use normal priority and are only active when that screen is mounted.

## Files to Modify

### Core (daemon layer)
- `forge/core/daemon_executor.py` — question detection, pause/resume logic, slot release, timeout checker
- `forge/core/daemon.py` — pipeline paused_duration tracking, emit `pipeline:all_tasks_done`
- `forge/core/daemon_helpers.py` — `_parse_forge_question()` parser
- `forge/core/daemon_review.py` — emit `review:gate_started/passed/failed` and `review:llm_feedback` events
- `forge/core/sdk_helpers.py` — `resume` is already on `ClaudeCodeOptions`, no changes needed here

### Database (forge/storage/db.py — single file, no Alembic)
- Add `TaskQuestionRow` model + add to `_ALL_MODELS` tuple
- Add columns to `TaskRow`: `session_id`, `questions_asked`, `questions_limit`
- Add columns to `PipelineRow`: `paused_at`, `paused_duration`
- Add CRUD methods to `Database` class: `create_task_question()`, `answer_question()`, `get_pending_questions()`, `get_expired_questions()`

### Models (forge/core/models.py)
- Add `AWAITING_INPUT = "awaiting_input"` to `TaskState` enum

### TUI (Textual)
- `forge/tui/screens/pipeline.py` — execution screen with 2-panel layout, view switching (output/chat/diff/review)
- `forge/tui/screens/final_approval.py` — new screen for post-completion review + PR creation
- `forge/tui/screens/settings.py` — add autonomy section, question limits, completion toggles
- `forge/tui/widgets/chat_thread.py` — new widget: Q&A thread with work log, question card, suggestion chips, input
- `forge/tui/widgets/review_gates.py` — new widget: gate result cards with pass/fail/running states
- `forge/tui/widgets/suggestion_chips.py` — new widget: horizontal chip selector with keyboard nav
- `forge/tui/widgets/progress_bar.py` — add stages 5-7 (review, final, PR)
- `forge/tui/state.py` — add handlers to `_EVENT_MAP` for all new events, question state tracking
- `forge/tui/bus.py` — add all new event types to `TUI_EVENT_TYPES` list
- `forge/tui/app.py` — global keybindings, focus management, `FinalApprovalScreen` push, PR creation orchestration

### Config
- `forge/config/settings.py` — add `autonomy`, `question_limit`, `question_timeout`, `auto_pr` to `ForgeSettings`
- `forge/api/routes/settings.py` — API defaults for new settings fields

### Agents
- `forge/agents/adapter.py` — inject question protocol into system prompt (inline string template, matching existing `AGENT_SYSTEM_PROMPT_TEMPLATE` pattern)

### PR Creation
- `forge/tui/pr_creator.py` — new module: `push_branch()`, `generate_pr_title()`, `generate_pr_body()`, `create_pr()` — all async, using subprocess for git/gh commands
