# Agent Quality & Human-in-the-Loop Completion

**Date:** 2026-03-16
**Status:** Draft
**Predecessor:** `2026-03-11-human-in-the-loop-pipeline-design.md` (original HITL spec, mostly implemented)
**Scope:** 5 fixes that complete the HITL system and unleash agent capabilities

## Context

The March 11 HITL spec was 90% implemented — DB models, question parsing, TUI widgets, event system, chat thread, session resume logic. But testing on real projects reveals the system is **architecturally complete but functionally disconnected**:

1. Human answers questions in TUI → answer stored in DB → **daemon never picks it up** → agent stays frozen in `AWAITING_INPUT` forever
2. Planning Architect has an `on_question` callback but `daemon.plan()` **never wires it** → planning runs fully autonomous with no human input
3. Agent SDK sessions get `allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"]` — **artificially lobotomized** compared to a normal Claude Code session
4. No way for humans to **send messages to running agents** — interaction only happens at question boundaries
5. Question protocol is too conservative — agents **almost never ask** in balanced mode

Each fix below reconnects a disconnected wire or removes an artificial constraint. No new architecture — just completing what exists.

---

## Fix 1: Connect the Resume Wire

### Problem

`_resume_task()` in `daemon_executor.py` (lines 332-417) is fully implemented and correct. It retrieves `session_id`, transitions back to `IN_PROGRESS`, calls `sdk_query` with `resume=session_id` and the answer as prompt, and checks for follow-up questions. **But nothing ever calls it.**

The TUI's `ForgeApp.on_chat_thread_answer_submitted()` (app.py lines 228-243) stores the answer in DB and updates TUI state, but never notifies the daemon.

### Design

**Primary path — event-driven resume:**

1. `ForgeApp.on_chat_thread_answer_submitted()` already calls `db.answer_question()` and `state.apply_event("task:answer", ...)`. Add one line after: emit `task:answer` on the event bus so the daemon hears it.

2. In `daemon.py` (or `daemon_executor.py`), register a listener for `task:answer` events. When received:
   ```
   task_id = data["task_id"]
   → look up task from DB
   → verify state == AWAITING_INPUT
   → verify question has an answer
   → acquire execution slot
   → call _resume_task(task_id, answer)
   ```

3. This reuses the existing slot acquisition mechanism (`_acquire_slot` / agent pool). The resumed task competes for slots like any other task.

**Crash recovery path — startup scan:**

On daemon startup (or pipeline resume), scan for tasks in `AWAITING_INPUT` state that have answered questions in DB:

```python
async def _recover_answered_questions(self, db, pipeline_id):
    """Resume tasks that were answered while daemon was down."""
    tasks = await db.get_tasks_by_state(pipeline_id, "awaiting_input")
    for task in tasks:
        questions = await db.get_task_questions(task.id)
        answered = [q for q in questions if q.answer and q.answered_at]
        if answered:
            latest = max(answered, key=lambda q: q.answered_at)
            await self._resume_task(task.id, latest.answer)
```

Call this at the start of the execution loop and after reconnecting to a running pipeline.

**Timeout path — already exists but disconnected:**

`_check_question_timeouts()` exists (daemon.py lines 743-767) but only auto-answers expired questions. After auto-answering, it should also call `_resume_task()`. Add the resume call after the auto-answer DB write.

### Files Changed

| File | Change |
|------|--------|
| `forge/tui/app.py` | `on_chat_thread_answer_submitted()`: emit `task:answer` on `self._bus` after DB write |
| `forge/core/daemon_executor.py` | Add `_on_task_answered()` listener method; register on event bus during init |
| `forge/core/daemon.py` | `_check_question_timeouts()`: call `_resume_task()` after auto-answering; add `_recover_answered_questions()` called at execution loop start |

### Test Plan

- Unit: mock DB with AWAITING_INPUT task + answered question → verify `_resume_task` called
- Unit: daemon startup with orphaned answered question → verify recovery
- Unit: question timeout → verify auto-answer + resume
- Integration: TUI answer submission → verify agent resumes and completes task
- Edge case: answer submitted while daemon is restarting → crash recovery picks it up

---

## Fix 2: Unleash Agent Sessions

### Problem

Agent SDK sessions are artificially constrained:

1. `allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"]` — agents can't use WebSearch, WebFetch, Agent sub-dispatch, or any MCP tools available in the environment
2. `CLAUDE.md` is never loaded — agents don't get project-specific instructions that make normal Claude Code sessions context-aware
3. `AGENT_SYSTEM_PROMPT_TEMPLATE` is thin — lacks guidance on adaptive tool use, self-correction, and exploration patterns

### Design

**2A: Remove allowed_tools for task agents**

In `ClaudeAdapter._build_options()` (adapter.py line 265), remove the `allowed_tools` parameter entirely. When omitted, Claude Code gives the agent access to all available tools — the same set a normal interactive session would have.

Planning and review agents KEEP their restrictions:
- Scout: `["Read", "Glob", "Grep", "Bash"]` — explores, doesn't edit
- Detailer: `["Read", "Glob", "Grep"]` — reads for context, doesn't edit
- Architect: `["Read", "Glob", "Grep", "Bash"]` — explores, doesn't edit
- Reviewer: `["Read", "Glob", "Grep"]` — reads for review, doesn't edit

Only task execution agents (the ones in `ClaudeAdapter`) get full access.

**2B: Load and inject CLAUDE.md**

Add a `_load_claude_md(project_dir: str) -> str | None` function that reads CLAUDE.md from standard locations:
1. `{project_dir}/CLAUDE.md`
2. `{project_dir}/.claude/CLAUDE.md`

Returns the content as a string, or None if not found. The content is injected into the agent's system prompt as a dedicated section:

```
## Project Instructions (from CLAUDE.md)

{claude_md_content}
```

This goes in `AGENT_SYSTEM_PROMPT_TEMPLATE` after the conventions block. If CLAUDE.md and conventions.md both exist, both are included (they serve different purposes — CLAUDE.md is project instructions, conventions.md is Forge-specific patterns).

**2C: Enrich agent system prompt**

Add to `AGENT_SYSTEM_PROMPT_TEMPLATE`:

```
## Working Effectively

- Use all available tools. If you need to look up API docs, use WebSearch.
  If you need to understand a library, read its source. Be resourceful.
- If tests fail, read the full error output. Diagnose the root cause.
  Fix it. Re-run. Don't guess — verify.
- Before editing a file, read it first. Understand the existing patterns.
  Follow them. Don't introduce new conventions.
- If you're unsure about something, explore first. Grep the codebase.
  Read related files. Build understanding before making changes.
- Commit your work when you reach a stable point. Small, focused commits
  are better than one giant commit at the end.
```

This gives agents the behavioral guidance that makes normal Claude Code sessions feel competent and adaptive.

### Files Changed

| File | Change |
|------|--------|
| `forge/agents/adapter.py` | Remove `allowed_tools` from `_build_options()`; add `_load_claude_md()`; enrich `AGENT_SYSTEM_PROMPT_TEMPLATE` with CLAUDE.md section and working-effectively guidance |

### Test Plan

- Unit: `_load_claude_md()` finds CLAUDE.md in project root
- Unit: `_load_claude_md()` finds .claude/CLAUDE.md as fallback
- Unit: `_load_claude_md()` returns None when neither exists
- Unit: `_build_options()` returns options WITHOUT `allowed_tools` key
- Unit: system prompt includes CLAUDE.md content when present
- Unit: system prompt includes working-effectively section

---

## Fix 3: Wire Planning Questions

### Problem

`Architect.__init__()` accepts `on_question` callback (architect.py line 40). `Architect.run()` detects FORGE_QUESTION in output, calls `on_question` if provided, resumes session with the answer (lines 87-99). But `daemon.plan()` (daemon.py lines 344-348) constructs the Architect without passing `on_question`. Planning questions have nowhere to go.

### Design

**Synchronization mechanism:**

The Architect runs inside `daemon.plan()` which is an async method. The `on_question` callback needs to:
1. Store the question in DB
2. Emit a `planning:question` event to the TUI
3. Wait for the human to answer
4. Return the answer string

Use an `asyncio.Event` for the wait:

```python
# In daemon.plan():
pending_planning_answer: dict[str, asyncio.Event] = {}
planning_answers: dict[str, str] = {}

async def _on_architect_question(question_data: dict) -> str:
    """Called by Architect when it has a question."""
    q = await db.create_task_question(
        task_id="__planning__",  # sentinel for planning-phase questions
        pipeline_id=pipeline_id,
        question=question_data["question"],
        suggestions=question_data.get("suggestions"),
        context=question_data.get("context"),
        stage="planning",
    )
    await self._emit("planning:question", {
        "question_id": q.id,
        "question": question_data,
    }, db=db, pipeline_id=pipeline_id)

    event = asyncio.Event()
    pending_planning_answer[q.id] = event
    await event.wait()
    return planning_answers.pop(q.id, "Proceed with your best judgment.")
```

**Answer delivery:**

When the TUI submits an answer for a planning question, it emits `planning:answer` on the event bus. The daemon listener resolves the asyncio.Event:

```python
async def _on_planning_answer(data: dict):
    q_id = data["question_id"]
    answer = data["answer"]
    planning_answers[q_id] = answer
    event = pending_planning_answer.pop(q_id, None)
    if event:
        event.set()
```

**DB schema change:**

Add `stage` column to `TaskQuestionRow`:

```python
stage: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
# Values: "planning", "execution", None (backward compat)
```

Planning questions use `task_id="__planning__"` as a sentinel since there's no task yet during planning. The `stage` column distinguishes them from execution questions.

**TUI display:**

The planning screen (`pipeline.py`) already shows a `PlannerCard` during planning. When `planning:question` arrives:

1. TUI state stores the question in `pending_questions["__planning__"]`
2. PipelineScreen detects planning question and shows a question overlay — same `ChatThread` widget, rendered inline below the planning output
3. Human types answer, submits
4. `ForgeApp` handler writes answer to DB, emits `planning:answer` on bus
5. Planning output continues streaming after the Architect resumes

**Timeout:** Planning questions get the same timeout as execution questions (`settings.question_timeout`). If human doesn't answer, auto-proceed.

### Files Changed

| File | Change |
|------|--------|
| `forge/core/daemon.py` | `plan()`: construct `_on_architect_question` callback, pass to Architect; register `_on_planning_answer` listener; add `_recover_planning_questions()` |
| `forge/storage/db.py` | Add `stage` column to `TaskQuestionRow`; add to `_add_missing_columns()` auto-migration |
| `forge/tui/state.py` | Add `_on_planning_question` and `_on_planning_answer` handlers to `_EVENT_MAP` |
| `forge/tui/screens/pipeline.py` | Show `ChatThread` inline when planning question arrives; handle answer submission routing for planning vs execution questions |
| `forge/tui/app.py` | Route planning answers through `planning:answer` event instead of `task:answer` |

### Test Plan

- Unit: `_on_architect_question` stores question in DB with `stage="planning"` and `task_id="__planning__"`
- Unit: `_on_planning_answer` resolves the asyncio.Event and returns answer
- Unit: planning question timeout auto-proceeds
- Integration: Architect asks question → TUI displays → human answers → Architect resumes and produces plan
- Edge case: multiple planning questions in sequence (up to question_limit)

---

## Fix 4: Human Interrupt (Interjection)

### Problem

Humans can only interact with agents at question boundaries. No way to send a message to a running agent — no course correction, no "hey, use this approach instead", no "stop, you're going the wrong way."

### Design

**New concept: Interjection**

An interjection is a human message sent to an agent that is currently `IN_PROGRESS`. Unlike a question (agent asks, human answers), an interjection is human-initiated (human speaks, agent listens).

**TUI trigger:**

Add keybinding `i` ("interject") on the pipeline screen. When pressed:
1. Opens the ChatThread for the currently selected task
2. Input placeholder changes to "Type a message to the agent..."
3. Human types message and presses Enter
4. Message stored in DB and queued for the agent

**DB model — InterjectionRow:**

```python
class InterjectionRow(Base):
    __tablename__ = "task_interjections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=...)
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(String, default=...)
    delivered_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
```

Add to `_ALL_MODELS` for auto-migration.

**Daemon-side delivery:**

The executor's `_execute_task` flow has a natural injection point. After `sdk_query()` returns (agent completed one full turn), before proceeding to review:

```python
# After agent turn completes, before review:
interjections = await db.get_pending_interjections(task_id)
if interjections:
    # Combine all pending messages
    combined = "\n\n".join(
        f"Human message: {ij.message}" for ij in interjections
    )
    prompt = (
        f"The human has sent you a message while you were working:\n\n"
        f"{combined}\n\n"
        f"Read their input carefully. Adjust your approach if needed, "
        f"then continue working on the task."
    )
    # Mark as delivered
    for ij in interjections:
        await db.mark_interjection_delivered(ij.id)

    # Resume session with human's message
    agent_result = await self._run_agent(
        ..., resume=session_id, prompt_override=prompt,
    )
    # Check result for questions or completion as normal
    ...
```

**Multi-turn interjection:** After the agent processes an interjection and continues, check for new interjections again. This allows ongoing dialogue — human sends "use approach X", agent adjusts, human sends "looks good, but also handle edge case Y", agent adjusts again.

**Edge cases:**

1. **Agent completes before interjection is processed** — Interjection stays in DB as undelivered. Show in TUI as "message not delivered — task already completed." Human can decide to re-run with the feedback.

2. **Multiple interjections queued** — Combine into one message block delivered together. Don't interrupt the agent once per message.

3. **Task in AWAITING_INPUT** — If human uses interject on a task that's waiting for a question answer, treat the interjection as the answer. Route through existing `_resume_task` flow.

4. **Agent asks question after receiving interjection** — Normal question flow. The interjection is already part of the conversation context via resume.

**Event flow:**

```
Human presses 'i', types message, submits
  → TUI emits "task:interjection" with task_id + message
  → ForgeApp handler stores in DB (InterjectionRow)
  → daemon_executor picks up after current agent turn completes
  → agent sees message via resume, adjusts approach
  → continues working
```

### Files Changed

| File | Change |
|------|--------|
| `forge/storage/db.py` | Add `InterjectionRow` model; add `create_interjection()`, `get_pending_interjections()`, `mark_interjection_delivered()` methods |
| `forge/core/daemon_executor.py` | After `sdk_query()` returns and before review, check for pending interjections; deliver via `resume=session_id` |
| `forge/tui/screens/pipeline.py` | Add `i` keybinding; open ChatThread in "interjection mode" (different placeholder, no suggestion chips) |
| `forge/tui/widgets/chat_thread.py` | Add interjection mode — different placeholder text, message styled differently from question answers |
| `forge/tui/app.py` | Handle `ChatThread.InterjectionSubmitted` message; store in DB via `create_interjection()` |
| `forge/tui/state.py` | Add `_on_task_interjection` handler; track interjection state per task |

### Test Plan

- Unit: interjection stored in DB with `delivered=False`
- Unit: executor detects pending interjection after agent turn, delivers via resume
- Unit: multiple interjections combined into single prompt
- Unit: interjection on AWAITING_INPUT task routes to answer flow
- Unit: interjection on completed task marked as undelivered
- Integration: human sends interjection → agent acknowledges and adjusts approach
- Edge case: interjection arrives between agent turn and review gate start

---

## Fix 5: Tune the Question Protocol

### Problem

The "balanced" autonomy mode says "Ask ONLY for high-impact decisions" — too vague, agents interpret this as "never ask." The protocol lacks examples and doesn't encourage agents to explain their uncertainty.

### Design

**Replace the balanced mode text:**

```
IF autonomy == "balanced":
    Ask when you are less than 80% confident about a decision that
    affects correctness. It is always better to pause for 30 seconds
    than to build the wrong thing for 10 minutes.

    ASK when:
    - The spec is ambiguous and you see multiple valid interpretations
    - You're about to make an architectural choice the spec doesn't specify
    - You found conflicting patterns in the codebase and aren't sure which to follow
    - You're about to delete, rename, or restructure something that other code depends on

    DON'T ASK when:
    - The spec is clear and you know exactly what to do
    - It's a naming, formatting, or minor style choice
    - You can verify your assumption by reading existing code

    EXAMPLES:
    - Spec says "add caching" but doesn't mention TTL or eviction strategy → ASK
    - Spec says "add a login button to the nav bar" and you can see the nav component → DON'T ASK
    - You're about to change a function signature that 12 other files import → ASK
    - You need to pick between two equivalent testing patterns → DON'T ASK
```

**Add "thinking out loud" requirement:**

Before emitting FORGE_QUESTION, agents must explain:

```
Before asking a question, briefly explain:
1. What you're working on
2. What you found that created the uncertainty
3. What options you see

Then ask your specific question with concrete suggestions.
This context helps the human give you a useful answer.
```

**Expose autonomy as a setting:**

Add `autonomy` field to `ForgeSettings` (may already exist from March 11 spec — verify and ensure it's wired through to `_build_question_protocol`). Values: `full`, `balanced`, `supervised`. Default: `balanced`.

Wire it: `daemon_executor.py` reads `settings.autonomy` and passes to `ClaudeAdapter._build_options()` → `_build_question_protocol(autonomy=settings.autonomy, ...)`.

### Files Changed

| File | Change |
|------|--------|
| `forge/agents/adapter.py` | Rewrite `_build_question_protocol()` balanced mode text; add thinking-out-loud section; add examples |
| `forge/config/settings.py` | Verify `autonomy` field exists; add if not |
| `forge/core/daemon_executor.py` | Pass `settings.autonomy` through to adapter |

### Test Plan

- Unit: `_build_question_protocol("balanced", 3)` contains "80% confident" and examples
- Unit: `_build_question_protocol("full", 3)` says "NEVER ask"
- Unit: `_build_question_protocol("supervised", 3)` says "when uncertain about ANY"
- Unit: settings.autonomy is respected in agent system prompt
- Manual: run balanced agent on ambiguous task → verify it asks about the ambiguity

---

## Summary

| # | Fix | Severity | Core Change |
|---|-----|----------|-------------|
| 1 | Resume wire | Critical | Connect TUI answer → daemon `_resume_task()` call |
| 2 | Unleash agents | Critical | Remove `allowed_tools`, load CLAUDE.md, enrich prompt |
| 3 | Planning questions | High | Wire Architect `on_question` through DB → TUI → asyncio.Event |
| 4 | Human interrupt | High | New interjection flow — human sends messages to running agents |
| 5 | Question protocol | Medium | Tune balanced mode, add examples, thinking-out-loud |

## Ordering

Fixes 1 and 2 are independent and highest priority — they make the existing system actually work.
Fix 5 is independent and can be done anytime.
Fix 3 depends on Fix 1's event pattern (same resume mechanism, different context).
Fix 4 depends on Fix 1 (uses the same session resume pattern) and needs Fix 2's unrestricted agents to be most useful.

Recommended execution order: 1 → 2 → 5 → 3 → 4
