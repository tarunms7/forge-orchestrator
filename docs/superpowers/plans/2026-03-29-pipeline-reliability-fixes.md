# Pipeline Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 confirmed bugs/design flaws that make large Forge pipelines unreliable — parallelism cap, question parsing, review auto-pass, reviewer UNCERTAIN verdict, agent prompt, model escalation, plus TUI question card header.

**Architecture:** Each fix is isolated to 1-3 files. Fixes 1/2/4/5 are independent. Fix 3 introduces `needs_human` on `GateResult` and new answer routing. Fix 3b extends Fix 3 with reviewer UNCERTAIN verdict. Fix 6 (TUI) is a small formatting change.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy async, Textual TUI, pytest

**Spec:** `docs/superpowers/specs/2026-03-29-pipeline-reliability-fixes-design.md`

---

### Task 1: Fix Parallelism Cap Inversion

**Files:**
- Modify: `forge/core/daemon.py:1199-1208`

- [ ] **Step 1: Fix the parallelism calculation**

In `forge/core/daemon.py`, replace lines 1199-1208:

```python
            # Auto-scale agent pool: use the minimum of independent tasks,
            # configured max_agents, and total tasks.  This ensures we
            # never exceed the user's resource budget (max_agents) and
            # never create idle agents (more agents than tasks that can run).
            independent_count = sum(1 for t in graph.tasks if not t.depends_on)
            self._effective_max_agents = min(
                independent_count,
                self._settings.max_agents,
                len(graph.tasks),
            )
```

- [ ] **Step 2: Verify with existing tests**

Run: `python -m pytest forge/core/daemon_test.py -v -x --timeout=30 2>&1 | tail -20`
Expected: all existing tests pass (this change only tightens the cap, no tests depend on over-provisioning)

- [ ] **Step 3: Commit**

```bash
git add forge/core/daemon.py
git commit -m "fix: cap effective_max_agents with min() instead of max()

Parallelism was computed with max(independent_count, max_agents) which
allowed more agents than the user configured. Changed to three-way min()
so effective agents never exceed max_agents, independent task count, or
total task count."
```

---

### Task 2: Fix Silent Question Dropping

**Files:**
- Modify: `forge/core/daemon_helpers.py:71-136`
- Modify: `forge/core/daemon_helpers_test.py`

- [ ] **Step 1: Write failing tests for the new behavior**

Add to `forge/core/daemon_helpers_test.py` inside `class TestParseForgeQuestion`:

```python
    def test_question_with_long_trailing_text_now_accepted(self):
        """Trailing text after valid JSON should NOT cause the question to be dropped."""
        text = (
            'FORGE_QUESTION:\n{"question": "Which pattern?", "suggestions": ["A", "B"]}\n\n'
            "I'll pause here and wait for your guidance on this. "
            "Meanwhile I've set up the basic structure so we can proceed quickly once you decide."
        )
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "Which pattern?"

    def test_question_with_no_suggestions_accepted(self):
        """Questions without suggestions should be accepted (no restriction on content)."""
        text = 'FORGE_QUESTION:\n{"question": "What should the TTL be?"}'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "What should the TTL be?"

    def test_question_with_many_suggestions_accepted(self):
        """No limit on number of suggestions."""
        suggestions = [f"Option {i}" for i in range(10)]
        import json
        text = f'FORGE_QUESTION:\n{json.dumps({"question": "Pick one", "suggestions": suggestions})}'
        result = _parse_forge_question(text)
        assert result is not None
        assert len(result["suggestions"]) == 10

    def test_question_with_extra_keys_accepted(self):
        """Extra keys beyond question/suggestions should be preserved (forward-compatible)."""
        text = 'FORGE_QUESTION:\n{"question": "?", "suggestions": ["A"], "impact": "high", "custom_field": 42}'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["custom_field"] == 42

    def test_malformed_json_logs_warning(self, caplog):
        """Malformed JSON after marker should log a warning."""
        import logging
        with caplog.at_level(logging.WARNING, logger="forge"):
            result = _parse_forge_question("FORGE_QUESTION:\n{not valid json}")
        assert result is None
        assert "FORGE_QUESTION marker found but JSON parse failed" in caplog.text

    def test_missing_question_key_logs_warning(self, caplog):
        """Valid JSON without 'question' key should log a warning."""
        import logging
        with caplog.at_level(logging.WARNING, logger="forge"):
            result = _parse_forge_question('FORGE_QUESTION:\n{"suggestions": ["A"]}')
        assert result is None
        assert "missing 'question' key" in caplog.text

    def test_brace_matching_failure_logs_warning(self, caplog):
        """Unmatched braces should log a warning."""
        import logging
        with caplog.at_level(logging.WARNING, logger="forge"):
            result = _parse_forge_question("FORGE_QUESTION:\n{unclosed")
        assert result is None
        assert "brace matching failed" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/core/daemon_helpers_test.py::TestParseForgeQuestion -v -x --timeout=30 2>&1 | tail -30`
Expected: `test_question_with_long_trailing_text_now_accepted` FAILS (old code drops it), logging tests FAIL (old code has no logging)

- [ ] **Step 3: Rewrite `_parse_forge_question` with logging and no trailing text restriction**

Replace the entire function in `forge/core/daemon_helpers.py` (lines 71-136):

```python
def _parse_forge_question(text: str | None) -> dict | None:
    """Parse a FORGE_QUESTION block from agent output.

    Returns dict with at least a 'question' key (string), or None.
    No restrictions on additional keys — accepts any valid JSON with a 'question' field.
    """
    if not text:
        return None

    marker_idx = text.rfind(_FORGE_QUESTION_MARKER)
    if marker_idx == -1:
        return None

    after_marker = text[marker_idx + len(_FORGE_QUESTION_MARKER) :].strip()

    # Strip markdown fences if present
    json_text = after_marker
    fence_match = re.match(r"```(?:json)?\s*\n?(.*?)\n?\s*```", json_text, re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1).strip()
    else:
        # Find the closing brace using string-aware matching
        brace_depth = 0
        json_end = -1
        in_string = False
        escape_next = False
        for i, ch in enumerate(json_text):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
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
        if json_end == -1:
            logger.warning(
                "FORGE_QUESTION marker found but JSON brace matching failed. "
                "Raw text after marker: %s",
                after_marker[:500],
            )
            return None
        # Accept the question regardless of trailing text.
        # If the marker is present and JSON is valid, the agent intended to ask.
        json_text = json_text[:json_end]

    try:
        data = _json.loads(json_text)
    except (_json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "FORGE_QUESTION marker found but JSON parse failed: %s. Raw JSON text: %s",
            exc,
            json_text[:500],
        )
        return None

    if not isinstance(data, dict):
        logger.warning(
            "FORGE_QUESTION JSON parsed but is not a dict (got %s)",
            type(data).__name__,
        )
        return None
    if "question" not in data or not isinstance(data["question"], str):
        logger.warning(
            "FORGE_QUESTION JSON parsed but missing 'question' key. Keys found: %s",
            list(data.keys()),
        )
        return None

    return data
```

- [ ] **Step 4: Update the existing test that expects trailing text to be dropped**

The test `test_question_mid_output_ignored` expects trailing text to cause a drop. With our change, valid JSON + marker = accepted. Update the test:

```python
    def test_question_mid_output_with_trailing_text_accepted(self):
        """Trailing text after valid question JSON is now accepted (marker + valid JSON = question)."""
        text = 'FORGE_QUESTION:\n{"question": "?", "suggestions": ["A"]}\n\nThen I continued working and wrote code.'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "?"
```

- [ ] **Step 5: Run all question parsing tests**

Run: `python -m pytest forge/core/daemon_helpers_test.py::TestParseForgeQuestion forge/core/daemon_executor_question_test.py -v --timeout=30 2>&1 | tail -30`
Expected: ALL pass

- [ ] **Step 6: Commit**

```bash
git add forge/core/daemon_helpers.py forge/core/daemon_helpers_test.py
git commit -m "fix: stop silently dropping FORGE_QUESTION from agents

Remove the 20-char trailing text threshold that was silently killing
valid questions. Add logger.warning at every drop point so failed parses
are visible. Remove all restrictions on question content — accept any
valid JSON with a 'question' key."
```

---

### Task 3: Model Escalation on Retry

**Files:**
- Modify: `forge/core/model_router.py`
- Modify: `forge/core/model_router_test.py`
- Modify: `forge/core/daemon_executor.py` (4 call sites)

- [ ] **Step 1: Write failing tests for escalation**

Add to `forge/core/model_router_test.py`:

```python
class TestModelEscalation:
    """Model escalation on retry 2+ for agent stage."""

    def test_no_escalation_retry_0(self):
        assert select_model("auto", "agent", "low", retry_count=0) == "sonnet"

    def test_no_escalation_retry_1(self):
        assert select_model("auto", "agent", "low", retry_count=1) == "sonnet"

    def test_escalation_retry_2_sonnet_to_opus(self):
        assert select_model("auto", "agent", "low", retry_count=2) == "opus"

    def test_escalation_retry_2_haiku_to_sonnet(self):
        assert select_model("fast", "agent", "high", retry_count=2) == "sonnet"

    def test_no_escalation_already_opus(self):
        assert select_model("quality", "agent", "low", retry_count=2) == "opus"

    def test_no_escalation_for_reviewer(self):
        assert select_model("auto", "reviewer", "low", retry_count=5) == "sonnet"

    def test_no_escalation_for_planner(self):
        assert select_model("auto", "planner", "low", retry_count=5) == "opus"

    def test_escalation_applies_to_overrides(self):
        """Override selects haiku, but retry 2+ should escalate it to sonnet."""
        result = select_model("auto", "agent", "low", overrides={"agent_model_low": "haiku"}, retry_count=2)
        assert result == "sonnet"

    def test_escalation_retry_3_same_as_2(self):
        """Escalation is capped at one tier — retry 3 doesn't escalate further."""
        assert select_model("auto", "agent", "low", retry_count=3) == "opus"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/core/model_router_test.py::TestModelEscalation -v -x --timeout=30 2>&1 | tail -20`
Expected: FAIL — `select_model` doesn't accept `retry_count` parameter yet

- [ ] **Step 3: Implement escalation in `select_model()`**

Replace the function in `forge/core/model_router.py`:

```python
def select_model(
    strategy: str,
    stage: str,
    complexity: str,
    overrides: dict | None = None,
    retry_count: int = 0,
) -> str:
    """Select the Claude model for a given strategy, pipeline stage, and task complexity.

    Args:
        strategy: "auto", "fast", or "quality"
        stage: "planner", "contract_builder", "agent", or "reviewer"
        complexity: "low", "medium", or "high"
        overrides: Optional dict of model overrides from user settings.
            Keys like ``planner_model``, ``reviewer_model``,
            ``agent_model_low``, ``agent_model_medium``, ``agent_model_high``.
        retry_count: Current retry number. On retry 2+, agent models
            escalate one tier (haiku→sonnet, sonnet→opus).

    Returns:
        Model name string: "opus", "sonnet", or "haiku"
    """
    model: str | None = None

    if overrides:
        # Check for direct override — planner/reviewer/contract_builder use {stage}_model,
        # agent uses agent_model_{complexity}
        if stage in ("planner", "reviewer", "contract_builder"):
            key = f"{stage}_model"
        else:
            key = f"agent_model_{complexity}"
        model = overrides.get(key)

    if not model:
        table = _ROUTING_TABLE.get(strategy)
        if table is None:
            logger.warning("Unknown model_strategy '%s', falling back to 'auto'", strategy)
            table = _ROUTING_TABLE["auto"]

        stage_map = table.get(stage)
        if stage_map is None:
            logger.warning(
                "Unknown stage '%s' for strategy '%s', falling back to 'agent'", stage, strategy
            )
            stage_map = table["agent"]

        model = stage_map.get(complexity)
        if model is None:
            logger.warning(
                "Unknown complexity '%s' for stage '%s', falling back to 'sonnet'",
                complexity,
                stage,
            )
            model = "sonnet"

    # Escalate on retry 2+ for agent stage only.
    # First retry uses same model with review feedback — feedback alone may suffice.
    # Second retry suggests the task needs more capability.
    if retry_count >= 2 and stage == "agent":
        _ESCALATION = {"haiku": "sonnet", "sonnet": "opus"}
        original = model
        model = _ESCALATION.get(model, model)  # opus stays opus
        if model != original:
            logger.info(
                "Escalating model %s -> %s for retry %d (stage=%s, complexity=%s)",
                original,
                model,
                retry_count,
                stage,
                complexity,
            )

    return model
```

- [ ] **Step 4: Run all model_router tests**

Run: `python -m pytest forge/core/model_router_test.py -v --timeout=30 2>&1 | tail -30`
Expected: ALL pass (including existing tests — `retry_count=0` default preserves behavior)

- [ ] **Step 5: Pass `retry_count` at all 4 executor call sites**

In `forge/core/daemon_executor.py`, find all 4 places where `select_model` is called for agents and add `retry_count=task.retry_count`:

Line ~238:
```python
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium", retry_count=task.retry_count)
```

Line ~281:
```python
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium", retry_count=task.retry_count)
```

Line ~458:
```python
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium", retry_count=task.retry_count)
```

Line ~1023:
```python
        agent_model = select_model(self._strategy, "agent", task.complexity or "medium", retry_count=task.retry_count)
```

- [ ] **Step 6: Run executor tests**

Run: `python -m pytest forge/core/daemon_executor_test.py -v -x --timeout=60 2>&1 | tail -20`
Expected: ALL pass

- [ ] **Step 7: Commit**

```bash
git add forge/core/model_router.py forge/core/model_router_test.py forge/core/daemon_executor.py
git commit -m "feat: escalate agent model on retry 2+ (haiku->sonnet->opus)

Tasks that fail twice now get a more capable model on the next attempt.
Escalation only applies to agent stage, and only on retry_count >= 2.
Applies regardless of whether model came from overrides or routing table."
```

---

### Task 4: Agent Prompt — Dead-Ends Become Questions

**Files:**
- Modify: `forge/agents/adapter.py`

- [ ] **Step 1: Update retry discipline prompt**

In `forge/agents/adapter.py`, replace the retry discipline section (around line 196):

Find:
```
- If you cannot make something work after 3 attempts, document what you tried and move on. An honest "this didn't work because X" is infinitely better than burning 20 retries on the same dead end.
```

Replace with:
```
- If you cannot make something work after 3 attempts, STOP and ask for help using FORGE_QUESTION. Explain what you tried, why each attempt failed, and suggest 2-3 options (different approach, skip this part, get specific guidance). An honest question prevents wasted retries and wasted work. DO NOT silently "move on" — if you're stuck, the human can unblock you in seconds.
```

- [ ] **Step 2: Update turn budget prompt**

Find (around line 200):
```
- If you're past turn {wrap_up_turn} and not done, STOP coding and write a status summary of what's done, what's remaining, and what the next agent should do.
```

Replace with:
```
- If you're past turn {wrap_up_turn} and not done, STOP coding and emit a FORGE_QUESTION with: what's done, what's remaining, and options (extend with more turns, hand off to next agent, or get specific guidance on the remaining work). Let the human decide.
```

- [ ] **Step 3: Update "Before You Finish" no-op guidance**

Find (around line 211):
```
4. If nothing meaningful to do (files don't exist, task already done), make no changes.
```

Replace with:
```
4. If nothing meaningful to do (files don't exist, task already done), make no changes and commit with message "chore: no changes needed — <reason>". The reviewer will see the empty diff and your reasoning. This is a valid outcome.
```

- [ ] **Step 4: Verify prompt renders correctly**

Run: `python -c "from forge.agents.adapter import _build_question_protocol, AGENT_SYSTEM_PROMPT_TEMPLATE; print('OK')" 2>&1`
Expected: `OK` (no import errors)

- [ ] **Step 5: Commit**

```bash
git add forge/agents/adapter.py
git commit -m "fix: agent prompt now says to ask FORGE_QUESTION on dead-ends

Previously the prompt told agents to 'document and move on' after 3
failures, which contradicted the question protocol. Now it tells them
to emit FORGE_QUESTION with options. Turn budget exhaustion also routes
to FORGE_QUESTION instead of a silent status summary."
```

---

### Task 5: Add `needs_human` to GateResult + Review Auto-Pass → Ask Human

**Files:**
- Modify: `forge/review/pipeline.py`
- Modify: `forge/review/llm_review.py`
- Modify: `forge/review/llm_review_test.py`
- Modify: `forge/storage/db.py` (add `source` column)
- Modify: `forge/core/daemon_review.py`
- Modify: `forge/core/daemon_executor.py`

- [ ] **Step 1: Add `needs_human` field to `GateResult`**

In `forge/review/pipeline.py`, add the field:

```python
@dataclass
class GateResult:
    """Outcome of a single review gate."""

    passed: bool
    gate: str
    details: str
    retriable: bool = (
        False  # True = transient failure (empty response, SDK error) — re-review, don't re-agent
    )
    infra_error: bool = (
        False  # True = environment/infra failure (missing module, wrong Python, cmd not found)
    )
    # — skip this gate instead of consuming a retry
    needs_human: bool = (
        False  # True = escalate to awaiting_input for human decision
    )
```

- [ ] **Step 2: Write test for empty L2 returning `needs_human`**

Add to `forge/review/llm_review_test.py`:

```python
class TestEmptyReviewEscalation:
    """Empty L2 review should escalate to human, not auto-pass."""

    @pytest.mark.asyncio
    async def test_empty_review_returns_needs_human(self):
        """Empty SDK response should return needs_human=True, not passed=True."""
        mock_result = MagicMock()
        mock_result.result = ""
        mock_result.cost_usd = 0
        mock_result.input_tokens = 0
        mock_result.output_tokens = 0
        mock_result.num_turns = 0
        mock_result.duration_ms = 100
        mock_result.duration_api_ms = 50

        with patch("forge.review.llm_review.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.return_value = mock_result
            gate_result, cost_info = await gate2_llm_review(
                "Test task", "Test desc", "diff --git a/test.py", model="sonnet"
            )

        assert gate_result.passed is False
        assert gate_result.needs_human is True
        assert "Human review needed" in gate_result.details

    @pytest.mark.asyncio
    async def test_successful_review_no_needs_human(self):
        """Successful review should not set needs_human."""
        mock_result = MagicMock()
        mock_result.result = "PASS: Looks good"
        mock_result.cost_usd = 0.01
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50

        with patch("forge.review.llm_review.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.return_value = mock_result
            gate_result, cost_info = await gate2_llm_review(
                "Test task", "Test desc", "diff --git a/test.py", model="sonnet"
            )

        assert gate_result.passed is True
        assert gate_result.needs_human is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest forge/review/llm_review_test.py::TestEmptyReviewEscalation -v -x --timeout=30 2>&1 | tail -20`
Expected: FAIL — `needs_human` attribute doesn't exist yet on old auto-pass result

- [ ] **Step 4: Replace auto-pass with `needs_human` in `llm_review.py`**

In `forge/review/llm_review.py`, replace lines 222-237 (the auto-pass block):

Find:
```python
    # All attempts returned empty — auto-pass with warning rather than
    # retrying the entire task.  Empty results are transient SDK issues
    # (rate limits, overload), not code quality signals.  Retrying the
    # whole task would just regenerate the same diff and hit the same issue.
    logger.warning(
        "L2 review returned empty after %d attempts — auto-passing to avoid infinite retry loop",
        max_review_attempts,
    )
    return (
        GateResult(
            passed=True,
            gate="gate2_llm_review",
            details=f"Review auto-passed: empty response after {max_review_attempts} attempts (likely transient SDK issue)",
        ),
        cost_info,
    )
```

Replace with:
```python
    # All attempts returned empty — escalate to human instead of auto-passing.
    # Empty results are transient SDK issues, not code quality signals.
    # Instead of shipping unreviewed code, ask the human what to do.
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

- [ ] **Step 5: Run review tests**

Run: `python -m pytest forge/review/llm_review_test.py -v --timeout=30 2>&1 | tail -20`
Expected: ALL pass

- [ ] **Step 6: Add `source` column to TaskQuestionRow**

In `forge/storage/db.py`, find `class TaskQuestionRow` and add after the `context` field:

```python
    source: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
```

- [ ] **Step 7: Propagate `needs_human` through `_run_review` return**

In `forge/core/daemon_review.py`, the `_run_review` method returns `tuple[bool, str | None]`. We need to add a third element for `needs_human`. Change the return type and add the check after L2 review.

Find (around line 1166):
```python
            if not gate2_result.passed:
                console.print(f"[red]  L2 failed: {gate2_result.details}[/red]")
                prefix = "[RETRIABLE] " if gate2_result.retriable else ""
                feedback_parts.append(
                    f"{prefix}L2 (LLM code review) FAILED:\n{gate2_result.details}"
                )
```

Add a `needs_human` check right before the existing `if not gate2_result.passed:`:

```python
            if gate2_result.needs_human:
                console.print(f"[yellow]  L2: escalating to human — {gate2_result.details[:100]}[/yellow]")
                try:
                    await db.set_task_timing(
                        task.id, review_duration_s=time.monotonic() - review_t0
                    )
                except Exception:
                    pass
                return False, gate2_result.details, True  # (passed, feedback, needs_human)
```

Update the method signature and docstring to return `tuple[bool, str | None, bool]` (third element = `needs_human`, default `False`).

Update ALL other `return` statements in `_run_review` to include the third element:
- `return False, "\n\n".join(feedback_parts)` → `return False, "\n\n".join(feedback_parts), False`
- `return True, None` → `return True, None, False`

- [ ] **Step 8: Handle `needs_human` in the executor's review processing**

In `forge/core/daemon_executor.py`, find where `_run_review` is called (around line 1350):

```python
                passed, feedback = await self._run_review(...)
```

Change to unpack the third element:

```python
                passed, feedback, needs_human = await self._run_review(...)
```

Then after the re-review loop (around line 1367, after `break`), add the `needs_human` check:

```python
            if needs_human:
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
                    "source": "review_escalation",
                }
                await self._handle_agent_question(
                    db, task_id, agent_id, question_data,
                    session_id=None,
                    pipeline_id=pid,
                )
                return
```

- [ ] **Step 9: Extend `_on_task_answered` for review-source questions**

In `forge/core/daemon_executor.py`, in `_on_task_answered()` (around line 1040), after acquiring the agent slot and before creating the resume task, add source routing:

After `await db.assign_task(task_id, agent_id)` (line 1095), get the question source:

```python
        # Get the source of the question to determine routing
        source = None
        async with db._session_factory() as session:
            from forge.storage.db import TaskQuestionRow
            from sqlalchemy import select as sa_select
            stmt = sa_select(TaskQuestionRow).where(
                TaskQuestionRow.task_id == task_id,
                TaskQuestionRow.answer.isnot(None),
            ).order_by(TaskQuestionRow.created_at.desc()).limit(1)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row:
                source = row.source

        if source in ("review_escalation", "review_uncertain"):
            await self._handle_review_answer(db, task_id, agent_id, answer, pipeline_id)
            return
```

Then add the `_handle_review_answer` method:

```python
    async def _handle_review_answer(
        self, db, task_id: str, agent_id: str, answer: str, pipeline_id: str
    ) -> None:
        """Route human's answer to a review-escalation question."""
        answer_lower = answer.lower()
        pid = pipeline_id

        if "retry" in answer_lower:
            # Re-run the review pipeline
            logger.info("Review answer for %s: retry review", task_id)
            await db.update_task_state(task_id, TaskState.IN_REVIEW.value)
            await db.release_agent(agent_id)
            # The main loop will pick up the task in IN_REVIEW state
            # and re-run review on next cycle. For now, trigger retry.
            await self._handle_retry(db, task_id, self._worktree_mgr, pipeline_id=pid)
        elif "approve" in answer_lower:
            # Human approved — proceed to merge
            logger.info("Review answer for %s: human approved", task_id)
            task = await db.get_task(task_id)
            agent_model = select_model(self._strategy, "agent", task.complexity or "medium", retry_count=task.retry_count)
            await db.update_task_state(task_id, TaskState.MERGING.value)
            await self._emit(
                "task:state_changed", {"task_id": task_id, "state": "merging"}, db=db, pipeline_id=pid
            )
            await self._attempt_merge(
                db, self._merge_worker, task, task_id, agent_id,
                self._worktree_mgr, agent_model, pid,
            )
        elif "reject" in answer_lower:
            # Human rejected — trigger task retry
            logger.info("Review answer for %s: human rejected, retrying task", task_id)
            await db.release_agent(agent_id)
            await self._handle_retry(
                db, task_id, self._worktree_mgr,
                review_feedback="Human reviewer rejected the code. Please revise.",
                pipeline_id=pid,
            )
        else:
            # Treat as guidance — store and retry review
            logger.info("Review answer for %s: human provided guidance", task_id)
            await db.release_agent(agent_id)
            await self._handle_retry(
                db, task_id, self._worktree_mgr,
                review_feedback=f"Human reviewer guidance: {answer}",
                pipeline_id=pid,
            )
```

- [ ] **Step 10: Update `_handle_agent_question` to persist `source`**

In `forge/core/daemon_executor.py`, in `_handle_agent_question()` (around line 738), pass `source` to `create_task_question`:

Find:
```python
        q = await db.create_task_question(
            task_id=task_id,
            pipeline_id=pid,
            question=question_data["question"],
            suggestions=question_data.get("suggestions"),
            context=question_data.get("context"),
        )
```

Replace with:
```python
        q = await db.create_task_question(
            task_id=task_id,
            pipeline_id=pid,
            question=question_data["question"],
            suggestions=question_data.get("suggestions"),
            context=question_data.get("context"),
            source=question_data.get("source"),
        )
```

And update the `create_task_question` method in `forge/storage/db.py` to accept and store `source`:

Find the `create_task_question` method and add `source: str | None = None` parameter, then set `source=source` on the row.

- [ ] **Step 11: Run tests**

Run: `python -m pytest forge/review/ forge/core/daemon_review_test.py forge/core/daemon_executor_test.py forge/core/daemon_executor_question_test.py -v --timeout=60 2>&1 | tail -30`
Expected: ALL pass

- [ ] **Step 12: Commit**

```bash
git add forge/review/pipeline.py forge/review/llm_review.py forge/review/llm_review_test.py forge/storage/db.py forge/core/daemon_review.py forge/core/daemon_executor.py
git commit -m "feat: review auto-pass replaced with human escalation

Empty L2 review now returns needs_human=True instead of passed=True.
The executor routes needs_human to awaiting_input with suggestions
(retry, approve, reject). Added source field to task questions for
routing answers to review-specific handlers vs agent resume."
```

---

### Task 6: Reviewer UNCERTAIN Verdict

**Files:**
- Modify: `forge/review/llm_review.py` (system prompt + parser)
- Modify: `forge/review/llm_review_test.py`
- Modify: `forge/core/daemon_review.py` (differentiate uncertain from empty)

- [ ] **Step 1: Write failing tests for UNCERTAIN verdict**

Add to `forge/review/llm_review_test.py`:

```python
class TestUncertainVerdict:
    """UNCERTAIN verdict should return needs_human=True."""

    def test_uncertain_at_start(self):
        result = _parse_review_result("UNCERTAIN: Can't tell if edge case is handled without seeing caller")
        assert result.passed is False
        assert result.needs_human is True
        assert "edge case" in result.details

    def test_uncertain_on_line(self):
        text = "Analysis:\nThe code looks reasonable but...\nUNCERTAIN: Missing context about the caller's intent"
        result = _parse_review_result(text)
        assert result.passed is False
        assert result.needs_human is True

    def test_pass_still_works(self):
        result = _parse_review_result("PASS: All checks verified")
        assert result.passed is True
        assert result.needs_human is False

    def test_fail_still_works(self):
        result = _parse_review_result("FAIL: Bug on line 42")
        assert result.passed is False
        assert result.needs_human is False

    def test_unclear_response_still_fails_not_uncertain(self):
        result = _parse_review_result("I'm not sure what to think about this code")
        assert result.passed is False
        assert result.needs_human is False  # Unclear != UNCERTAIN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/review/llm_review_test.py::TestUncertainVerdict -v -x --timeout=30 2>&1 | tail -20`
Expected: FAIL — `_parse_review_result` doesn't recognize UNCERTAIN yet

- [ ] **Step 3: Update `REVIEW_SYSTEM_PROMPT` to include UNCERTAIN**

In `forge/review/llm_review.py`, replace lines 34-38:

Find:
```
You will receive a task specification and a git diff. Review the code
thoroughly and respond with EXACTLY one of:

PASS: <explanation covering what you verified>
FAIL: <specific issues with file paths and line references>
```

Replace with:
```
You will receive a task specification and a git diff. Review the code
thoroughly and respond with EXACTLY one of:

PASS: <explanation covering what you verified>
FAIL: <specific issues with file paths and line references>
UNCERTAIN: <specific concerns you cannot resolve from the diff alone>

## When to use UNCERTAIN
- You see code that MIGHT be correct but depends on context you don't have
- The task spec is ambiguous and the code matches ONE valid interpretation
- You found something suspicious but can't confirm it's a bug without seeing the caller
- The diff is too large to review thoroughly and you need human guidance

Do NOT use UNCERTAIN for:
- Code that is clearly wrong — use FAIL
- Code that is clearly correct — use PASS
- Style preferences — use PASS (not your job)
```

Also update the closing instruction at line 298:

Find: `parts.append("Review this code. Respond with PASS or FAIL.")`
Replace: `parts.append("Review this code. Respond with PASS, FAIL, or UNCERTAIN.")`

- [ ] **Step 4: Extend `_parse_review_result` to recognize UNCERTAIN**

In `forge/review/llm_review.py`, update `_parse_review_result`:

After the PASS/FAIL startswith checks (line 323), add UNCERTAIN:

```python
    if upper.startswith("UNCERTAIN"):
        return GateResult(passed=False, gate="gate2_llm_review", details=text, needs_human=True)
```

In the line-by-line loop (after line 331), add:

```python
        if line_upper.startswith("UNCERTAIN"):
            return GateResult(passed=False, gate="gate2_llm_review", details=text, needs_human=True)
```

In the fallback regex section (after line 339), add:

```python
    uncertain_match = re.search(r"^UNCERTAIN\b", upper, re.MULTILINE)
    if uncertain_match and not pass_match and not fail_match:
        return GateResult(passed=False, gate="gate2_llm_review", details=text, needs_human=True)
```

Update the docstring to mention UNCERTAIN.

- [ ] **Step 5: Differentiate uncertain from empty in `_run_review`'s `needs_human` handling**

In `forge/core/daemon_review.py`, the `needs_human` check added in Task 5 Step 7 handles both empty review and UNCERTAIN. Update the `needs_human` block to pass different context:

The code from Task 5 already returns `(False, gate2_result.details, True)` for `needs_human`. The executor in Task 5 Step 8 checks `needs_human` and creates a question. We need to differentiate the question text:

In `forge/core/daemon_executor.py`, in the `needs_human` check added in Task 5, update to differentiate:

```python
            if needs_human:
                # Determine if this is empty-response or reviewer-uncertainty
                is_uncertain = feedback and not feedback.startswith("Review could not complete")
                if is_uncertain:
                    question_data = {
                        "question": "The reviewer is uncertain about this code and needs your input.",
                        "context": feedback,
                        "suggestions": [
                            "Approve — the code is correct, reviewer's concern is not applicable",
                            "Reject — the reviewer's concern is valid, retry the task",
                            "Provide guidance for the reviewer to re-review with more context",
                        ],
                        "source": "review_uncertain",
                    }
                else:
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
                        "source": "review_escalation",
                    }
                await self._handle_agent_question(
                    db, task_id, agent_id, question_data,
                    session_id=None,
                    pipeline_id=pid,
                )
                return
```

- [ ] **Step 6: Run all review tests**

Run: `python -m pytest forge/review/llm_review_test.py -v --timeout=30 2>&1 | tail -30`
Expected: ALL pass

- [ ] **Step 7: Commit**

```bash
git add forge/review/llm_review.py forge/review/llm_review_test.py forge/core/daemon_review.py forge/core/daemon_executor.py
git commit -m "feat: reviewer can now say UNCERTAIN to escalate to human

Added UNCERTAIN as a third verdict option in the review prompt.
UNCERTAIN routes to awaiting_input via needs_human, with reviewer's
specific concerns shown to the human. Differentiated from empty-review
escalation with separate source field and suggestions."
```

---

### Task 7: TUI Question Card Header

**Files:**
- Modify: `forge/tui/widgets/chat_thread.py`

- [ ] **Step 1: Update `format_question_card` to differentiate sources**

In `forge/tui/widgets/chat_thread.py`, replace the `format_question_card` function:

```python
def format_question_card(question: dict) -> str:
    """Format a question card with clear visual structure.

    Header changes based on question source:
    - review_escalation: "Review could not complete"
    - review_uncertain: "Reviewer is uncertain about this code"
    - default: "Question from Agent" or "Question from Planner"
    """
    q = question.get("question", "")
    ctx = question.get("context", "")
    source = question.get("source")
    task_id = question.get("task_id", "")

    if source == "review_escalation":
        header = "Review Could Not Complete"
    elif source == "review_uncertain":
        header = "Reviewer Is Uncertain"
    else:
        header = "Question from Agent"

    parts = []
    parts.append(f"[bold {ACCENT_ORANGE}]━━━ {_escape(header)} ━━━[/]")
    parts.append("")
    if ctx:
        parts.append(f"[{TEXT_SECONDARY}]{_escape(ctx)}[/]")
        parts.append("")
    parts.append(f"[bold {ACCENT_ORANGE}]{_escape(q)}[/]")
    parts.append("")
    parts.append(
        f"[{TEXT_SECONDARY}]Type your answer below, or press a number key (1-9) to select a suggestion:[/]"
    )
    return "\n".join(parts)
```

- [ ] **Step 2: Verify TUI imports still work**

Run: `python -c "from forge.tui.widgets.chat_thread import format_question_card; print(format_question_card({'question': 'test', 'source': 'review_uncertain'}))" 2>&1`
Expected: Output contains "Reviewer Is Uncertain"

- [ ] **Step 3: Commit**

```bash
git add forge/tui/widgets/chat_thread.py
git commit -m "feat: TUI question card shows different headers per source

Review-escalation shows 'Review Could Not Complete', reviewer-uncertain
shows 'Reviewer Is Uncertain', and agent/planner questions show
'Question from Agent'. Enables user to immediately see what kind of
decision they're being asked to make."
```

---

### Task 8: Full Test Suite Verification

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest forge/ -v --timeout=120 -x 2>&1 | tail -40`
Expected: ALL tests pass, zero failures

- [ ] **Step 2: If any failures, fix them before committing**

Any test that fails at this stage indicates a regression. Read the error, trace it to the change that caused it, fix it.

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -A -- ':!.venv' ':!node_modules'
git commit -m "fix: address test regressions from reliability fixes"
```
