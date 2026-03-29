# Pipeline Reliability Fixes — Design Spec

**Date:** 2026-03-29
**Goal:** Fix 5 confirmed bugs/design flaws that make large Forge pipelines unreliable.
**Principle:** Every fix must be verifiable. No assumptions, no "it should work" — each change has a concrete before/after.

---

## Fix 1: Parallelism Cap Inversion

### Problem

`daemon.py:1205-1208` uses `max(independent_count, self._settings.max_agents)` inside a `min()`, which allows the effective agent count to exceed the user's configured `max_agents` limit.

**Current code:**
```python
self._effective_max_agents = min(
    max(independent_count, self._settings.max_agents),
    len(graph.tasks),
)
```

**Example:** `max_agents=5`, `independent_count=20`, `len(tasks)=25` → `min(max(20,5), 25)` = `min(20, 25)` = **20 agents**. User configured 5.

### Fix

```python
self._effective_max_agents = min(
    independent_count,
    self._settings.max_agents,
    len(graph.tasks),
)
```

Three-way `min()`: never more agents than independent tasks (no idle agents), never more than user's budget, never more than total tasks.

**Example after fix:** `min(20, 5, 25)` = **5 agents**. Correct.

### Files Changed
- `forge/core/daemon.py` — lines 1205-1208 (one expression change)
- Update the comment on lines 1199-1203 to match the new logic

### Impact Analysis
- `_effective_max_agents` is only consumed in the agent creation loop on line 1209 (`range(self._effective_max_agents)`) and logged. No downstream code assumes the old over-provisioned behavior.
- This will reduce resource contention on large plans, fewer rate limits, fewer cascading timeouts.

### Verification
- Unit test: `max_agents=4, independent_count=10, total_tasks=15` → effective = 4
- Unit test: `max_agents=4, independent_count=2, total_tasks=15` → effective = 2
- Unit test: `max_agents=4, independent_count=10, total_tasks=3` → effective = 3

---

## Fix 2: Silent Question Dropping

### Problem

`daemon_helpers.py:71-136` (`_parse_forge_question`) silently returns `None` in 4 failure modes with zero logging. Agents that try to ask questions have them swallowed.

Key failure modes:
1. **Trailing text > 20 chars** (line 122): Agent adds a short closing sentence after the JSON → question dropped
2. **Brace matching fails** (line 119): Malformed JSON → question dropped
3. **JSON parse error** (line 128): Almost-valid JSON → question dropped
4. **Missing `question` key** (line 133): Partial question data → question dropped

None of these log anything.

### Fix

**A. Add `logger.warning()` at every silent drop point** — every `return None` after the marker is found gets a log line with the raw text that failed to parse. This makes dropped questions visible in logs.

**B. Remove the trailing text threshold entirely** — the old 20-char limit was silently killing valid questions. Instead of a character limit, use a smarter heuristic: if the text after JSON contains code-like patterns (function definitions, imports, file edits), the agent continued working. If it's just natural language, accept it. For simplicity and future MCP compatibility, **remove the threshold altogether** — if the `FORGE_QUESTION:` marker is present and the JSON is valid, accept the question regardless of trailing text. The trailing text filter was solving a hypothetical problem ("agent continued working") at the cost of dropping real questions. If an agent emits the marker and valid JSON, it intended to ask.

**C. Remove all artificial restrictions on question content** — no limits on question length, number of suggestions, context size, etc. The question JSON must have a `question` key (string). Everything else is optional and unrestricted. This keeps the system open for the future MCP layer where agents will have full freedom to communicate.

**D. Add logging at every silent drop point** — every `return None` after the marker is found gets a `logger.warning()` with the raw text. Makes dropped questions visible.

### Changes

```python
# After marker found but brace matching fails (line 119)
if json_end == -1:
    logger.warning(
        "FORGE_QUESTION marker found but JSON brace matching failed. Raw text after marker: %s",
        after_marker[:500],
    )
    return None

# REMOVE the trailing text check entirely (old line 122).
# If the marker is present and JSON is valid, accept the question.
# Old code: `if len(trailing) > 20: return None` — DELETED

# After JSON parse error (line 128)
except (_json.JSONDecodeError, ValueError) as exc:
    logger.warning(
        "FORGE_QUESTION marker found but JSON parse failed: %s. Raw JSON text: %s",
        exc,
        json_text[:500],
    )
    return None

# After missing question key (line 133) — only require 'question' as string
if "question" not in data or not isinstance(data["question"], str):
    logger.warning(
        "FORGE_QUESTION JSON parsed but missing 'question' key. Keys found: %s",
        list(data.keys()) if isinstance(data, dict) else type(data).__name__,
    )
    return None

# REMOVE any validation on suggestions count, context size, etc.
# Accept any additional keys the agent provides — future MCP compatibility.
```

### Files Changed
- `forge/core/daemon_helpers.py` — `_parse_forge_question()` function (lines 71-136)

### Impact Analysis
- This is a pure improvement: previously silent failures now log. No behavioral change for successfully parsed questions.
- Removing the trailing text threshold means ALL questions with valid JSON will parse. The only rejection criteria are: no marker, malformed JSON, or missing `question` key.
- No restrictions on question content means agents and planners have full freedom to structure their questions however they need — any number of suggestions, any context length, any extra fields.
- Future MCP compatibility: when we add the MCP layer, the question data structure will be richer. By removing restrictions now, the DB and handlers already accept arbitrary question shapes.

### Verification
- Unit test: question with 500 chars trailing → parsed (old code dropped this, new code accepts)
- Unit test: question with 0 trailing → still works (regression check)
- Unit test: malformed JSON with marker → returns None BUT logs warning
- Unit test: valid JSON with no suggestions → parsed (suggestions are optional)
- Unit test: valid JSON with 10 suggestions → parsed (no limit)
- Unit test: valid JSON with extra keys → parsed (forward-compatible)
- Integration check: run a pipeline, grep logs for "FORGE_QUESTION marker found" — should see entries when questions are dropped

---

## Fix 3: Review Auto-Pass → Ask Human

### Problem

`llm_review.py:222-237` auto-passes review (`passed=True`) when L2 review returns empty after 2 attempts. This means unreviewed code ships silently.

### Fix

Replace auto-pass with a `needs_human=True` signal that routes the task to `awaiting_input`. The human sees the diff, gets suggestions for what to do, and tells Forge how to proceed.

### Design

**Step 1: Add `needs_human` field to `GateResult`**

```python
@dataclass
class GateResult:
    passed: bool
    gate: str
    details: str
    retriable: bool = False
    infra_error: bool = False
    needs_human: bool = False  # NEW: route to awaiting_input for human decision
```

**Step 2: Return `needs_human=True` instead of `passed=True` on empty L2**

In `llm_review.py`, replace lines 222-237:

```python
# All attempts returned empty — escalate to human instead of auto-passing.
logger.warning(
    "L2 review returned empty after %d attempts — escalating to human",
    max_review_attempts,
)
return (
    GateResult(
        passed=False,
        gate="gate2_llm_review",
        details=f"Review could not complete after {max_review_attempts} attempts (likely transient SDK issue). Human review needed.",
        needs_human=True,
    ),
    cost_info,
)
```

**Step 3: Handle `needs_human` in the executor's review result processing**

In `daemon_executor.py`, where the review outcome is processed after `run_review_pipeline()` returns, add a check:

```python
# If any gate result needs human review, transition to awaiting_input
if any(gr.needs_human for gr in outcome.gate_results):
    question_data = {
        "question": (
            "Automated review could not complete (SDK returned empty after retries). "
            "The agent's diff is preserved. What should I do?"
        ),
        "context": f"Task: {task.title}. The code changes are ready but could not be reviewed automatically.",
        "suggestions": [
            "Retry the review now",
            "I'll review the diff manually — approve",
            "I'll review the diff manually — reject and retry the task",
        ],
    }
    await self._handle_agent_question(
        db, task_id, agent_id, question_data,
        session_id=None,  # No agent session to resume — answer routes to review retry or approval
        pipeline_id=pipeline_id,
    )
    return
```

**Step 4: Handle the answer**

When the human answers a review-escalation question, the answer handler needs to route appropriately:
- "Retry the review now" → re-run `run_review_pipeline()` for this task
- "approve" → mark review as passed, proceed to merge
- "reject" → mark review as failed, trigger retry

This requires a small extension to the answer handler in `_on_task_answered()`. The question will carry metadata (e.g., `"source": "review_escalation"`) so the handler knows this is a review question, not an agent question.

Add `source` as an optional field in the question data and persist it:

```python
question_data = {
    "question": "...",
    "suggestions": [...],
    "source": "review_escalation",  # Distinguishes from agent questions
}
```

In `_on_task_answered()`, check the source:
- If `source == "review_escalation"`: route to review retry or approval logic
- Otherwise: resume agent session (existing behavior)

### Files Changed
- `forge/review/pipeline.py` — add `needs_human: bool = False` to `GateResult`
- `forge/review/llm_review.py` — replace auto-pass block (lines 222-237)
- `forge/core/daemon_executor.py` — add `needs_human` check after review, extend `_on_task_answered()` for review-source questions
- `forge/storage/db.py` — add `source` column to task question table (nullable string, no migration needed for SQLite)

### Impact Analysis
- **No more silent auto-pass.** Every piece of code is either reviewed by L2 or reviewed by a human. Zero unreviewed code ships.
- The `needs_human` field is `False` by default, so all existing gate results are unaffected.
- The answer handler extension is additive — existing agent question flow is untouched (it checks `source` and falls through to existing behavior when source is not `review_escalation`).
- The diff is preserved in the worktree throughout this process. No work is lost.

### Verification
- Unit test: empty L2 review → `GateResult(passed=False, needs_human=True)` (not `passed=True`)
- Unit test: successful L2 review → unchanged behavior
- Unit test: `needs_human` gate result → task transitions to `awaiting_input`
- Unit test: answer "approve" → task proceeds to merge
- Unit test: answer "retry" → re-runs review pipeline
- Integration: force an empty SDK response, verify TUI shows question with suggestions

---

## Fix 3b: Reviewer Can Ask Questions (UNCERTAIN Verdict)

### Problem

The reviewer is strictly binary: PASS or FAIL. If the reviewer is genuinely uncertain — e.g., "this looks correct but I can't tell if this edge case is handled without knowing the caller's intent" — it's forced to FAIL, which triggers a pointless retry. The reviewer has no way to say "I need a human to look at this specific thing."

### Fix

Add `UNCERTAIN` as a third verdict option. When the reviewer says UNCERTAIN, the task transitions to `awaiting_input` with the reviewer's specific concerns, and the human decides.

### Design

**Step 1: Update reviewer system prompt**

Add to `REVIEW_SYSTEM_PROMPT` in `llm_review.py`:

```
PASS: <explanation covering what you verified>
FAIL: <specific issues with file paths and line references>
UNCERTAIN: <specific concerns you cannot resolve from the diff alone>
```

Add a new section to the review checklist:

```
## When to use UNCERTAIN
- You see code that MIGHT be correct but depends on context you don't have
- The task spec is ambiguous and the code matches ONE valid interpretation
- You found something suspicious but can't confirm it's a bug without seeing the caller/consumer
- The diff is too large to review thoroughly and you need human guidance on what to focus on

Do NOT use UNCERTAIN for:
- Code that is clearly wrong → use FAIL
- Code that is clearly correct → use PASS
- Style preferences → use PASS (not your job)
```

**Step 2: Extend `_parse_review_result()` to recognize UNCERTAIN**

```python
# After existing PASS/FAIL checks, add:
# Check for UNCERTAIN verdict
if upper.startswith("UNCERTAIN"):
    return GateResult(
        passed=False, gate="gate2_llm_review",
        details=text, needs_human=True,
    )

# Also in the line-by-line and fallback sections:
for line in text.splitlines():
    line_upper = line.strip().upper()
    if line_upper.startswith("UNCERTAIN"):
        return GateResult(
            passed=False, gate="gate2_llm_review",
            details=text, needs_human=True,
        )
```

This reuses the `needs_human` field from Fix 3, so the executor routes it to `awaiting_input` via the same pipeline. The human sees the reviewer's specific concerns and decides: approve, reject, or give guidance.

**Step 3: Update the question suggestions for reviewer uncertainty**

When the executor detects `needs_human=True` from a review (not from empty response), the suggestions should be different:

```python
if any(gr.needs_human for gr in outcome.gate_results):
    # Determine if this is empty-response or reviewer-uncertainty
    uncertain_gate = next(
        (gr for gr in outcome.gate_results if gr.needs_human and gr.details),
        None,
    )
    if uncertain_gate and not uncertain_gate.details.startswith("Review could not complete"):
        # Reviewer expressed specific uncertainty
        question_data = {
            "question": "The reviewer is uncertain about this code and needs your input.",
            "context": uncertain_gate.details,  # Reviewer's full analysis
            "suggestions": [
                "Approve — the code is correct, reviewer's concern is not applicable",
                "Reject — the reviewer's concern is valid, retry the task",
                "Provide guidance for the reviewer to re-review with more context",
            ],
            "source": "review_uncertain",
        }
    else:
        # Empty response fallback (Fix 3 behavior)
        question_data = { ... }  # existing Fix 3 suggestions
```

### Files Changed
- `forge/review/llm_review.py` — update `REVIEW_SYSTEM_PROMPT`, update `_parse_review_result()`
- `forge/core/daemon_executor.py` — differentiate review-uncertain from review-empty in the `needs_human` handler

### Impact Analysis
- Builds directly on Fix 3's `needs_human` infrastructure — no new data flow, just a new way to reach it.
- The reviewer now has three choices instead of two. Models handle three-option prompts well.
- Risk: reviewer might over-use UNCERTAIN to avoid making decisions. Mitigated by the prompt being explicit about when to use it (only when context is genuinely missing).
- The `_parse_review_result` fallback for unclear responses still defaults to FAIL (not UNCERTAIN), so ambiguous reviewer output doesn't accidentally route to human.

### Verification
- Unit test: review response starting with "UNCERTAIN:" → `GateResult(passed=False, needs_human=True)`
- Unit test: review response starting with "PASS" → unchanged
- Unit test: review response starting with "FAIL" → unchanged
- Unit test: unclear response (no verdict) → still FAIL (not UNCERTAIN)
- Unit test: `needs_human=True` from reviewer → task goes to `awaiting_input` with reviewer's concerns as context
- Unit test: human answers "approve" → proceeds to merge
- Unit test: human answers "reject" → triggers task retry

---

## Fix 4: Agent Prompt — Dead-Ends Become Questions

### Problem

`adapter.py:196` tells agents: "If you cannot make something work after 3 attempts, document what you tried and move on." This directly contradicts the question protocol (line 100) which says "pause and ask when uncertain."

Agents resolve this contradiction by picking "move on" because it finishes the session. Result: silent failures instead of human-recoverable questions.

Similarly, line 200 (turn budget) says "write a status summary" instead of asking for guidance.

### Fix

**Change 1: Retry discipline (line 196)**

Current:
```
If you cannot make something work after 3 attempts, document what you tried and move on.
An honest "this didn't work because X" is infinitely better than burning 20 retries on the same dead end.
```

New:
```
If you cannot make something work after 3 attempts, STOP and ask for help using FORGE_QUESTION.
Explain what you tried, why each attempt failed, and suggest 2-3 options (different approach,
skip this part, get specific guidance). An honest question prevents wasted retries and wasted work.
DO NOT silently "move on" — if you're stuck, the human can unblock you in seconds.
```

**Change 2: Turn budget (line 200)**

Current:
```
If you're past turn {wrap_up_turn} and not done, STOP coding and write a status summary
of what's done, what's remaining, and what the next agent should do.
```

New:
```
If you're past turn {wrap_up_turn} and not done, STOP coding and emit a FORGE_QUESTION with:
what's done, what's remaining, and options (extend with more turns, hand off to next agent,
or get specific guidance on the remaining work). Let the human decide.
```

**Change 3: "Before You Finish" section (line 211)**

Current:
```
If nothing meaningful to do (files don't exist, task already done), make no changes.
```

New:
```
If nothing meaningful to do (files don't exist, task already done), make no changes and commit
with message "chore: no changes needed — <reason>". The reviewer will see the empty diff and
your reasoning. This is a valid outcome.
```

### Files Changed
- `forge/agents/adapter.py` — three prompt text changes in `AGENT_SYSTEM_PROMPT_TEMPLATE`

### Impact Analysis
- **Prompt-only change.** No code logic changes. No new code paths.
- This is the highest-leverage fix for the "agents never ask me anything" problem. The prompt was explicitly telling them not to ask.
- Risk: agents may now ask more questions, increasing human interaction load. This is the intended behavior — questions are recoverable, silent failures are not.
- The question limit (`questions_remaining`) still applies, so agents can't ask unlimited questions. Default is 3 per task.

### Verification
- Manual test: give an agent an ambiguous task → it should emit FORGE_QUESTION instead of guessing
- Manual test: give an agent an impossible task (missing dependency) → it should ask instead of silently failing
- The prompt changes don't have unit tests (they're text), but the downstream behavior is verified by checking that more questions flow through the FORGE_QUESTION parser (Fix 2 makes those visible in logs)

---

## Fix 5: Model Escalation on Retry

### Problem

`model_router.py` selects models based only on task complexity and strategy. Retry count is ignored. A "low" complexity task that fails 3 times with sonnet will keep retrying with sonnet, even though the failures suggest it needs a more capable model.

`daemon_executor.py` calls `select_model(strategy, "agent", complexity)` identically on every retry (lines 238, 281, 458, 1023).

### Fix

**Step 1: Add `retry_count` parameter to `select_model()`**

```python
def select_model(
    strategy: str,
    stage: str,
    complexity: str,
    overrides: dict | None = None,
    retry_count: int = 0,
) -> str:
```

**Step 2: Escalation logic**

After the normal routing table lookup (and after checking overrides), apply escalation:

```python
# Escalate model on retry 2+ (first retry uses same model with review feedback;
# second retry suggests the task needs more capability)
if retry_count >= 2 and stage == "agent":
    escalation = {"haiku": "sonnet", "sonnet": "opus"}
    original = model
    model = escalation.get(model, model)  # opus stays opus
    if model != original:
        logger.info(
            "Escalating model %s → %s for retry %d (stage=%s, complexity=%s)",
            original, model, retry_count, stage, complexity,
        )
```

Key decisions:
- **Only escalates agent stage.** Reviewer and planner don't retry in the same way.
- **Only on retry 2+.** First retry (retry_count=1) uses same model with review feedback — the feedback alone may be enough. Second retry escalates.
- **User overrides take precedence.** If the user explicitly set `agent_model_low=haiku`, the override check happens before escalation, so the override wins. This is correct — if a user explicitly chose a model, we respect that.
  - **WAIT — this is wrong.** If overrides take precedence and return early (line 55: `return override_val`), the escalation code never runs. We need escalation to also apply to overrides. Fix: move escalation AFTER the override check but apply it to whatever model was selected.

Revised logic:

```python
def select_model(
    strategy: str,
    stage: str,
    complexity: str,
    overrides: dict | None = None,
    retry_count: int = 0,
) -> str:
    # ... existing override check and routing table lookup ...
    # (produces `model` variable)

    # Escalate on retry 2+ for agent stage
    if retry_count >= 2 and stage == "agent":
        escalation = {"haiku": "sonnet", "sonnet": "opus"}
        original = model
        model = escalation.get(model, model)
        if model != original:
            logger.info(
                "Escalating model %s → %s for retry %d (stage=%s, complexity=%s)",
                original, model, retry_count, stage, complexity,
            )

    return model
```

This applies escalation regardless of whether the model came from overrides or the routing table.

**Step 3: Pass `retry_count` from executor**

In `daemon_executor.py`, every call to `select_model()` for agents passes `retry_count`:

```python
agent_model = select_model(
    self._strategy, "agent", task.complexity or "medium",
    retry_count=task.retry_count,
)
```

There are 4 call sites (lines 238, 281, 458, 1023). All get the same change.

### Files Changed
- `forge/core/model_router.py` — add `retry_count` param, escalation logic after model selection
- `forge/core/daemon_executor.py` — pass `retry_count` at 4 call sites
- `forge/core/model_router_test.py` — new tests for escalation behavior

### Impact Analysis
- **Default `retry_count=0` means all existing callers are unaffected** unless they pass the param.
- The `cost_estimator.py` caller (line 63) doesn't pass `retry_count`, so cost estimates remain based on base models (correct — estimates shouldn't assume retries).
- Escalation is bounded: haiku→sonnet→opus. Opus can't escalate further. So max cost increase is one tier up, and only on retry 2+.

### Verification
- Unit test: `select_model("auto", "agent", "low", retry_count=0)` → sonnet (unchanged)
- Unit test: `select_model("auto", "agent", "low", retry_count=1)` → sonnet (unchanged, only escalates at 2+)
- Unit test: `select_model("auto", "agent", "low", retry_count=2)` → opus (escalated from sonnet)
- Unit test: `select_model("fast", "agent", "high", retry_count=2)` → sonnet (escalated from haiku)
- Unit test: `select_model("quality", "agent", "low", retry_count=2)` → opus (already opus, no change)
- Unit test: `select_model("auto", "reviewer", "low", retry_count=5)` → sonnet (reviewer not escalated)
- Unit test with override: `select_model("auto", "agent", "low", overrides={"agent_model_low": "haiku"}, retry_count=2)` → sonnet (override selected haiku, then escalated)

---

## Concurrent Questions — How Multiple Agents Asking at the Same Time Works

### Scenarios

**Scenario 1: Agent A asks, Agent B keeps working.**
- A's task → `awaiting_input`, A's agent slot released. B is unaffected. The pipeline continues. Other pending tasks can claim A's freed slot.

**Scenario 2: Agents A and C both ask around the same time.**
- Both tasks independently transition to `awaiting_input`. Both agent slots released. The human sees both questions (via TUI `DecisionBadge` count and `ChatThread`). The human answers in any order. Each answer resumes only its own task via `_on_task_answered(task_id=...)`.

**Scenario 3: Agent A asks, human is slow, 3 more agents ask while waiting.**
- 4 tasks now in `awaiting_input`. Pipeline keeps running any remaining tasks that don't need input. Human works through the queue at their own pace. Each answered task acquires an agent slot via the scheduler and resumes independently.

**Scenario 4: ALL non-terminal tasks are in `awaiting_input`.**
- Pipeline transitions to `paused` state (`daemon.py:2169-2191`). The `pipeline:paused` event fires. Pipeline resumes automatically when any task gets answered and transitions out of `awaiting_input`.

### Why This Already Works (No New Code Needed for Concurrency)

The existing infrastructure is already concurrent-safe:

1. **`_handle_agent_question()`** operates on a single `task_id` — creates a DB question row, transitions that task's state, releases that task's agent slot. No shared state between questions.

2. **`_on_task_answered()`** acquires its own agent slot via the scheduler's `dispatch_plan()`, and creates its own `asyncio.Task` for resumption. The `_active_tasks_lock` prevents double-resumption of the same task.

3. **DB question table** stores questions per `(task_id, pipeline_id)`. Multiple questions from different tasks coexist without conflict.

4. **Pipeline pause/resume** uses a simple "are ALL non-terminal tasks awaiting?" check — naturally handles partial vs full pause.

### What Fix 3/3b Adds to This Picture

Fix 3 (review escalation) and Fix 3b (reviewer UNCERTAIN) introduce a new question source: the review pipeline rather than the agent. These questions go through the same `_handle_agent_question()` → `awaiting_input` → `_on_task_answered()` flow, but the answer routing differs based on the `source` field:

| Source | Answer routes to |
|--------|-----------------|
| `None` (default) | Resume agent session via `session_id` |
| `"review_escalation"` | Re-run review, approve, or reject based on human's choice |
| `"review_uncertain"` | Same as review_escalation — approve, reject, or provide guidance |

The `source` field is stored in the DB question row and read back in `_on_task_answered()`. This is the only new routing logic — everything else (concurrency, slot management, pipeline pause) is inherited from the existing infrastructure.

### Answer Routing in `_on_task_answered()` — Extended Design

```python
async def _on_task_answered(self, data: dict, db) -> None:
    task_id = data.get("task_id")
    answer = data.get("answer")
    source = data.get("source")  # NEW: read from question metadata

    # ... existing validation and slot acquisition ...

    if source in ("review_escalation", "review_uncertain"):
        # Route to review handler
        await self._handle_review_answer(db, task_id, agent_id, answer, pipeline_id)
    else:
        # Default: resume agent session (existing behavior)
        atask = asyncio.create_task(
            self._safe_execute_resume(db, runtime, worktree_mgr, merge_worker,
                                      task_id, agent_id, answer, pipeline_id)
        )
```

The `_handle_review_answer` method parses the human's choice:
- If answer contains "retry" → re-run `run_review_pipeline()` for this task
- If answer contains "approve" → create a synthetic `ReviewOutcome(approved=True)` and proceed to merge
- If answer contains "reject" → trigger task retry via `_handle_retry()`
- If answer contains "guidance" → store guidance as context, re-run review with the human's notes appended to the review prompt

---

## TUI Question Display Design

### Existing Infrastructure (No New Widgets Needed)

The TUI already has all the widgets required:
- `TaskList` — shows `◆` icon in orange for `awaiting_input` state
- `DecisionBadge` — shows "N decision(s) pending" count in orange/red at bottom of left panel
- `ChatThread` — full Q&A widget with question card, suggestion chips, input field, Q&A history
- `SuggestionChips` — clickable suggestion buttons, also selectable via number keys 1-9
- `_auto_switch_chat()` — auto-switches right panel to ChatThread when selected task is `awaiting_input`

### What Changes

**1. `format_question_card()` in `chat_thread.py` — differentiate question sources**

Current: Always shows "Question from Planner". Needs to show different headers based on source:

```python
def format_question_card(question: dict) -> str:
    source = question.get("source")
    if source == "review_escalation":
        header = "Review could not complete"
    elif source == "review_uncertain":
        header = f"Reviewer is uncertain about this code"
    else:
        # Agent or planner question (existing behavior)
        header = "Question from Agent"
    # ... rest of formatting
```

**2. No other TUI changes needed.** The existing event flow handles everything:
- `task:question` event → `PipelineScreen` stores in `pending_questions` → `DecisionBadge` count updates
- User selects task with j/k → `_auto_switch_chat()` fires → ChatThread populates
- User answers → `AnswerSubmitted` message bubbles to App → dispatched to daemon

### Scenario Summary

| Scenario | Left Panel | Right Panel | User Action |
|----------|-----------|-------------|-------------|
| **Agent asks, task selected** | ◆ icon, badge count++ | Auto-switch to ChatThread | Type answer or press 1-N |
| **Agent asks, task NOT selected** | ◆ icon, badge count++ | Stays on current view | Navigate with j/k when ready |
| **Multiple tasks asking** | Multiple ◆ icons, badge "N pending" | Shows question for selected task | Answer one at a time, any order |
| **Review empty (Fix 3)** | ◆ icon, badge count++ | "Review could not complete" header | Retry / Approve / Reject |
| **Reviewer UNCERTAIN (Fix 3b)** | ◆ icon, badge count++ | "Reviewer is uncertain" header + concerns | Approve / Reject / Guide |
| **ALL tasks awaiting** | All ◆ icons | PhaseBanner shows "Paused" | Answer any to resume pipeline |

### Design Principles
- **No popups. No interruptions.** User navigates to questions when ready.
- **DecisionBadge + task icons are passive notifications.** Everything else is pull-based.
- **Press `d` to view diff before answering reviewer questions.** Press `t` to return to chat.
- **Each answer is independent.** Answering T1 doesn't affect T3.

---

## What's NOT in This Spec

| Item | Why not |
|------|---------|
| **MCP agent-to-Forge layer** | Right architecture, separate conversation/scope. All question handling designed to be transport-agnostic — swapping text parsing for MCP tool calls later only changes how questions arrive, not how they're processed. |
| **Cascade blocking recovery** | Requires dependency graph rescheduling — separate effort |
| **TUI notification bar for questions** | Existing `DecisionBadge` + `ChatThread` + `awaiting_input` state already render in TUI. The problem was questions never reaching the TUI, which fixes 2/3/3b/4 address. If UX is still lacking after these fixes, that's a follow-up. |
| **Planner question support** | Already implemented in `unified_planner.py:172-184`. No changes needed. |
| **Reviewer context for large diffs** | Passing a 47-file diff into a single reviewer context causes saturation, shallow review, and empty responses. Needs its own design (chunked review, delta-focused review, two-pass approach). Separate spec. |

---

## Implementation Order

1. **Fix 1** (parallelism cap) — zero dependencies, one-line change
2. **Fix 5** (model escalation) — zero dependencies on other fixes, isolated to model_router
3. **Fix 2** (question parsing) — zero dependencies, isolated to daemon_helpers
4. **Fix 4** (agent prompt) — zero dependencies, prompt-only change
5. **Fix 3** (review auto-pass → ask human) — adds `needs_human` to GateResult, touches review + executor
6. **Fix 3b** (reviewer UNCERTAIN verdict) — builds on Fix 3's `needs_human` infrastructure, extends review prompt and parser

Fixes 1, 2, 4, 5 can be implemented in parallel. Fix 3 should come before 3b since 3b depends on the `needs_human` field and executor routing that Fix 3 introduces.
