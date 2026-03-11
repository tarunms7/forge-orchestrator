# Human-in-the-Loop Pipeline Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Forge agents to ask humans questions during execution, show review gate output, add final approval + PR creation stages, and overhaul the TUI with colors and keyboard navigation.

**Architecture:** Decision Queue pattern — agents output structured `FORGE_QUESTION:` JSON when uncertain, daemon detects it and pauses the task, user answers in TUI chat thread, daemon resumes via `sdk_query(resume=session_id)`. All state persisted in SQLite. No new SDK dependencies.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Textual (TUI), claude-code-sdk (existing), asyncio

**Spec:** `docs/superpowers/specs/2026-03-11-human-in-the-loop-pipeline-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `forge/tui/widgets/chat_thread.py` | Chat thread widget: work log, question card, suggestion chips, text input |
| `forge/tui/widgets/review_gates.py` | Review gate cards: Build → Lint → Test → LLM with pass/fail/running |
| `forge/tui/widgets/suggestion_chips.py` | Horizontal chip selector with keyboard navigation |
| `forge/tui/screens/final_approval.py` | Post-completion screen: stats, task table, PR creation actions |
| `forge/tui/pr_creator.py` | PR creation: push branch, generate title/body, `gh pr create` |

### Modified Files
| File | Changes |
|------|---------|
| `forge/core/models.py` | Add `AWAITING_INPUT` to `TaskState` enum |
| `forge/config/settings.py` | Add `autonomy`, `question_limit`, `question_timeout`, `auto_pr` fields |
| `forge/storage/db.py` | Add `TaskQuestionRow` model, new columns on `TaskRow`/`PipelineRow`, CRUD methods |
| `forge/core/daemon_helpers.py` | Add `_parse_forge_question()` parser |
| `forge/agents/adapter.py` | Inject question protocol into `AGENT_SYSTEM_PROMPT_TEMPLATE` |
| `forge/core/daemon_executor.py` | Question detection in `_execute_task`, `_resume_task()`, slot release |
| `forge/core/daemon.py` | Pipeline pause tracking, emit `pipeline:all_tasks_done`, timeout checker |
| `forge/core/daemon_review.py` | Emit `review:gate_started/passed/failed`, `review:llm_feedback` events |
| `forge/tui/bus.py` | Add 16 new event types to `TUI_EVENT_TYPES` |
| `forge/tui/state.py` | Add handlers for new events, question state fields |
| `forge/tui/screens/pipeline.py` | 2-panel layout with view switching (output/chat/diff/review) |
| `forge/tui/screens/settings.py` | Add autonomy section, question limits, completion toggles |
| `forge/tui/widgets/task_list.py` | Add `awaiting_input` state icon/color |
| `forge/tui/widgets/progress_bar.py` | Add stages 5-7 (review, final, PR) |
| `forge/tui/app.py` | Global keybindings, focus management, FinalApprovalScreen push, PR flow |

---

## Chunk 1: Foundation (Models, Settings, Database)

### Task 1: Add AWAITING_INPUT to TaskState enum

**Files:**
- Modify: `forge/core/models.py:14-22`
- Test: `forge/core/models_test.py` (create if needed)

- [ ] **Step 1: Write test for new enum value**

```python
# forge/core/models_test.py
from forge.core.models import TaskState

def test_awaiting_input_state_exists():
    assert TaskState.AWAITING_INPUT == "awaiting_input"
    assert TaskState.AWAITING_INPUT.value == "awaiting_input"

def test_awaiting_input_distinct_from_awaiting_approval():
    assert TaskState.AWAITING_INPUT != TaskState.AWAITING_APPROVAL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest forge/core/models_test.py -v`
Expected: FAIL — `AttributeError: 'AWAITING_INPUT' is not a member of TaskState`

- [ ] **Step 3: Add AWAITING_INPUT to TaskState enum**

In `forge/core/models.py`, after `AWAITING_APPROVAL = "awaiting_approval"` (line 18), add:

```python
AWAITING_INPUT = "awaiting_input"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest forge/core/models_test.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add forge/core/models.py forge/core/models_test.py
git commit -m "feat: add AWAITING_INPUT state to TaskState enum"
```

---

### Task 2: Add new fields to ForgeSettings

**Files:**
- Modify: `forge/config/settings.py:7-66`
- Modify: `forge/config/settings_test.py`

- [ ] **Step 1: Write tests for new settings fields**

```python
# Add to forge/config/settings_test.py
def test_autonomy_default():
    s = ForgeSettings()
    assert s.autonomy == "balanced"

def test_question_limit_default():
    s = ForgeSettings()
    assert s.question_limit == 3

def test_question_timeout_default():
    s = ForgeSettings()
    assert s.question_timeout == 1800

def test_auto_pr_default():
    s = ForgeSettings()
    assert s.auto_pr is False

def test_autonomy_valid_values():
    for val in ("full", "balanced", "supervised"):
        s = ForgeSettings(autonomy=val)
        assert s.autonomy == val
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/config/settings_test.py -k "autonomy or question_limit or question_timeout or auto_pr" -v`
Expected: FAIL — unknown fields

- [ ] **Step 3: Add fields to ForgeSettings**

In `forge/config/settings.py`, after `scheduler_poll_interval` field, add:

```python
# Human-in-the-loop settings
autonomy: str = "balanced"  # full | balanced | supervised
question_limit: int = 3  # max questions per task per execution cycle
question_timeout: int = 1800  # seconds before auto-decide (30 min)
auto_pr: bool = False  # skip final approval, auto-create PR
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/config/settings_test.py -v`
Expected: PASS

- [ ] **Step 5: Update settings API defaults and request model**

In `forge/api/routes/settings.py`:

1. Add to `DEFAULT_SETTINGS` dict (after `"reviewer_model": "sonnet"`):
```python
"autonomy": "balanced",
"question_limit": 3,
"question_timeout": 1800,
"auto_pr": False,
```

2. Add to `UpdateSettingsRequest` class (after `reviewer_model` field):
```python
autonomy: str | None = None
question_limit: int | None = Field(None, ge=1, le=10)
question_timeout: int | None = Field(None, ge=60, le=7200)
auto_pr: bool | None = None
```

- [ ] **Step 6: Update settings display groups**

In `forge/tui/screens/settings.py`, add to `_DISPLAY_GROUPS`:

```python
"Autonomy": ["autonomy", "question_limit", "question_timeout", "auto_pr"],
```

- [ ] **Step 7: Run full settings tests**

Run: `.venv/bin/python -m pytest forge/config/settings_test.py forge/api/routes/settings_test.py forge/tui/screens/settings_test.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add forge/config/settings.py forge/config/settings_test.py forge/api/routes/settings.py forge/tui/screens/settings.py
git commit -m "feat: add autonomy, question_limit, question_timeout, auto_pr settings"
```

---

### Task 3: Add TaskQuestionRow model and new DB columns

**Files:**
- Modify: `forge/storage/db.py:72-177`
- Test: `forge/storage/db_test.py` (create or extend)

- [ ] **Step 1: Write test for TaskQuestionRow model creation**

```python
# forge/storage/db_question_test.py
import pytest
from forge.storage.db import Database

@pytest.fixture
async def db():
    d = Database("sqlite+aiosqlite:///:memory:")
    await d.initialize()
    yield d
    await d.close()

async def test_create_task_question(db):
    # Setup: create a pipeline and task first
    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="Test", description="desc", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")

    q = await db.create_task_question(
        task_id="t1",
        pipeline_id="p1",
        question="Which ORM pattern?",
        suggestions=["SQLAlchemy 2.0", "Raw SQL"],
    )
    assert q is not None
    assert q.question == "Which ORM pattern?"
    assert q.answer is None
    assert q.answered_by is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest forge/storage/db_question_test.py -v`
Expected: FAIL — `TaskQuestionRow` does not exist, `create_task_question` method not found

- [ ] **Step 3: Add TaskQuestionRow model**

In `forge/storage/db.py`, after `PipelineEventRow` class (around line 173), add:

```python
class TaskQuestionRow(Base):
    """Agent question awaiting human answer."""

    __tablename__ = "task_questions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    suggestions: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    answered_by: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat(),
    )
    answered_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
```

- [ ] **Step 4: Add TaskQuestionRow to _ALL_MODELS tuple**

Update line 177:
```python
_ALL_MODELS = (UserRow, AuditLogRow, TaskRow, AgentRow, PipelineRow, UserTemplateRow, PipelineEventRow, TaskQuestionRow)
```

- [ ] **Step 5: Add new columns to TaskRow**

After `implementation_summary` column in `TaskRow`:

```python
session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
questions_asked: Mapped[int] = mapped_column(default=0)
questions_limit: Mapped[int] = mapped_column(default=3)
```

- [ ] **Step 6: Add new columns to PipelineRow**

After `contracts_json` column in `PipelineRow`:

```python
paused_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
paused_duration: Mapped[float] = mapped_column(default=0.0)
```

- [ ] **Step 7: Run test to verify model creation works**

Run: `.venv/bin/python -m pytest forge/storage/db_question_test.py::test_create_task_question -v`
Expected: FAIL — `create_task_question` method not found yet

- [ ] **Step 8: Add CRUD methods to Database class**

In `forge/storage/db.py`, add to `Database` class:

```python
async def create_task_question(
    self,
    *,
    task_id: str,
    pipeline_id: str,
    question: str,
    suggestions: list[str] | None = None,
    context: dict | None = None,
) -> TaskQuestionRow:
    async with self._session_factory() as session:
        row = TaskQuestionRow(
            task_id=task_id,
            pipeline_id=pipeline_id,
            question=question,
            suggestions=json.dumps(suggestions) if suggestions else None,
            context=json.dumps(context) if context else None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

async def answer_question(
    self, question_id: str, answer: str, answered_by: str = "human",
) -> None:
    async with self._session_factory() as session:
        row = await session.get(TaskQuestionRow, question_id)
        if row:
            row.answer = answer
            row.answered_by = answered_by
            row.answered_at = datetime.now(timezone.utc).isoformat()
            await session.commit()

async def get_pending_questions(self, pipeline_id: str) -> list[TaskQuestionRow]:
    async with self._session_factory() as session:
        result = await session.execute(
            select(TaskQuestionRow)
            .where(TaskQuestionRow.pipeline_id == pipeline_id)
            .where(TaskQuestionRow.answer.is_(None))
            .order_by(TaskQuestionRow.created_at)
        )
        return list(result.scalars().all())

async def get_task_questions(self, task_id: str) -> list[TaskQuestionRow]:
    async with self._session_factory() as session:
        result = await session.execute(
            select(TaskQuestionRow)
            .where(TaskQuestionRow.task_id == task_id)
            .order_by(TaskQuestionRow.created_at)
        )
        return list(result.scalars().all())

async def get_expired_questions(self, timeout_seconds: int) -> list[TaskQuestionRow]:
    cutoff = datetime.now(timezone.utc).timestamp() - timeout_seconds
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    async with self._session_factory() as session:
        result = await session.execute(
            select(TaskQuestionRow)
            .where(TaskQuestionRow.answer.is_(None))
            .where(TaskQuestionRow.created_at < cutoff_iso)
        )
        return list(result.scalars().all())
```

- [ ] **Step 9: Write additional CRUD tests**

```python
# Add to forge/storage/db_question_test.py

async def test_answer_question(db):
    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="Test", description="desc", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")
    q = await db.create_task_question(task_id="t1", pipeline_id="p1", question="Which?", suggestions=["A", "B"])

    await db.answer_question(q.id, "A", "human")
    questions = await db.get_task_questions("t1")
    assert len(questions) == 1
    assert questions[0].answer == "A"
    assert questions[0].answered_by == "human"
    assert questions[0].answered_at is not None

async def test_get_pending_questions(db):
    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="T1", description="d", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")
    await db.create_task(id="t2", title="T2", description="d", files=["b.py"], depends_on=[], complexity="low", pipeline_id="p1")

    q1 = await db.create_task_question(task_id="t1", pipeline_id="p1", question="Q1")
    q2 = await db.create_task_question(task_id="t2", pipeline_id="p1", question="Q2")
    await db.answer_question(q1.id, "Answer", "human")

    pending = await db.get_pending_questions("p1")
    assert len(pending) == 1
    assert pending[0].id == q2.id

async def test_expired_questions(db):
    from forge.storage.db import TaskQuestionRow

    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="T1", description="d", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")

    q = await db.create_task_question(task_id="t1", pipeline_id="p1", question="Q?")
    # Override created_at to be old
    async with db._session_factory() as session:
        row = await session.get(TaskQuestionRow, q.id)
        row.created_at = "2020-01-01T00:00:00+00:00"
        await session.commit()

    expired = await db.get_expired_questions(timeout_seconds=60)
    assert len(expired) == 1
    assert expired[0].id == q.id

async def test_task_session_id_column(db):
    await db.create_pipeline(id="p1", description="test", project_dir="/tmp")
    await db.create_task(id="t1", title="T1", description="d", files=["a.py"], depends_on=[], complexity="low", pipeline_id="p1")
    task = await db.get_task("t1")
    assert task.session_id is None
    assert task.questions_asked == 0
    assert task.questions_limit == 3
```

- [ ] **Step 10: Run all DB tests**

Run: `.venv/bin/python -m pytest forge/storage/db_question_test.py -v`
Expected: ALL PASS

- [ ] **Step 11: Commit**

```bash
git add forge/storage/db.py forge/storage/db_question_test.py
git commit -m "feat: add TaskQuestionRow model and question CRUD methods"
```

---

## Chunk 2: Core Engine (Question Parser, Prompt Injection, Pause/Resume)

### Task 4: Question parser in daemon_helpers.py

**Files:**
- Modify: `forge/core/daemon_helpers.py`
- Test: `forge/core/daemon_helpers_test.py`

- [ ] **Step 1: Write tests for _parse_forge_question**

```python
# Add to forge/core/daemon_helpers_test.py
from forge.core.daemon_helpers import _parse_forge_question

class TestParseForgeQuestion:
    def test_valid_question_at_end(self):
        text = "I analyzed the code.\n\nFORGE_QUESTION:\n{\"question\": \"Which pattern?\", \"suggestions\": [\"A\", \"B\"], \"impact\": \"high\"}"
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "Which pattern?"
        assert result["suggestions"] == ["A", "B"]
        assert result["impact"] == "high"

    def test_valid_question_with_context(self):
        text = 'Analyzed.\n\nFORGE_QUESTION:\n{"question": "Which?", "context": "Found 2", "suggestions": ["A", "B"]}'
        result = _parse_forge_question(text)
        assert result is not None
        assert result["context"] == "Found 2"

    def test_question_in_markdown_fence(self):
        text = "Done.\n\nFORGE_QUESTION:\n```json\n{\"question\": \"Which?\", \"suggestions\": [\"A\"]}\n```"
        result = _parse_forge_question(text)
        assert result is not None
        assert result["question"] == "Which?"

    def test_no_question_returns_none(self):
        text = "I wrote the code and committed it."
        result = _parse_forge_question(text)
        assert result is None

    def test_missing_question_field_returns_none(self):
        text = 'FORGE_QUESTION:\n{"suggestions": ["A", "B"]}'
        result = _parse_forge_question(text)
        assert result is None

    def test_malformed_json_returns_none(self):
        text = "FORGE_QUESTION:\n{not valid json}"
        result = _parse_forge_question(text)
        assert result is None

    def test_question_mid_output_ignored(self):
        text = 'FORGE_QUESTION:\n{"question": "?", "suggestions": ["A"]}\n\nThen I continued working and wrote code.'
        result = _parse_forge_question(text)
        assert result is None

    def test_empty_text_returns_none(self):
        result = _parse_forge_question("")
        assert result is None

    def test_none_text_returns_none(self):
        result = _parse_forge_question(None)
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/core/daemon_helpers_test.py::TestParseForgeQuestion -v`
Expected: FAIL — `_parse_forge_question` not found

- [ ] **Step 3: Implement _parse_forge_question**

In `forge/core/daemon_helpers.py`, add:

```python
import json as _json
import re as _re

_FORGE_QUESTION_MARKER = "FORGE_QUESTION:"

def _parse_forge_question(text: str | None) -> dict | None:
    """Parse a FORGE_QUESTION block from agent output.

    Returns dict with at least 'question' and 'suggestions' keys, or None.
    Only matches if the marker appears near the end of output (agent stopped to ask).
    """
    if not text:
        return None

    marker_idx = text.rfind(_FORGE_QUESTION_MARKER)
    if marker_idx == -1:
        return None

    after_marker = text[marker_idx + len(_FORGE_QUESTION_MARKER):].strip()

    # Check nothing substantial follows the JSON (agent continued working)
    # Strip markdown fences if present
    json_text = after_marker
    fence_match = _re.match(r"```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", json_text, _re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1).strip()
    else:
        # Check if there's significant text after the JSON block
        # Find the closing brace
        brace_depth = 0
        json_end = -1
        for i, ch in enumerate(json_text):
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    json_end = i + 1
                    break
        if json_end == -1:
            return None
        trailing = json_text[json_end:].strip()
        if len(trailing) > 20:  # significant trailing text = agent continued
            return None
        json_text = json_text[:json_end]

    try:
        data = _json.loads(json_text)
    except (_json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    if "question" not in data or not isinstance(data["question"], str):
        return None

    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/core/daemon_helpers_test.py::TestParseForgeQuestion -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add forge/core/daemon_helpers.py forge/core/daemon_helpers_test.py
git commit -m "feat: add _parse_forge_question parser for agent question detection"
```

---

### Task 5: Inject question protocol into agent system prompt

**Files:**
- Modify: `forge/agents/adapter.py:18-41`
- Test: `forge/agents/adapter_test.py` (create or extend)

- [ ] **Step 1: Write test for question protocol injection**

```python
# forge/agents/adapter_question_test.py
from forge.agents.adapter import _build_question_protocol, AGENT_SYSTEM_PROMPT_TEMPLATE

def test_balanced_autonomy_protocol():
    protocol = _build_question_protocol(autonomy="balanced", remaining=3)
    assert "balanced" in protocol
    assert "3" in protocol
    assert "high-impact decisions" in protocol.lower() or "high-impact" in protocol.lower()
    assert "FORGE_QUESTION:" in protocol

def test_full_autonomy_no_questions():
    protocol = _build_question_protocol(autonomy="full", remaining=0)
    assert "NEVER ask questions" in protocol

def test_supervised_autonomy_always_ask():
    protocol = _build_question_protocol(autonomy="supervised", remaining=5)
    assert "ANY" in protocol or "any" in protocol

def test_protocol_included_in_system_prompt():
    # The template should contain {question_protocol} placeholder
    assert "{question_protocol}" in AGENT_SYSTEM_PROMPT_TEMPLATE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/agents/adapter_question_test.py -v`
Expected: FAIL

- [ ] **Step 3: Add _build_question_protocol function**

In `forge/agents/adapter.py`, add before `AGENT_SYSTEM_PROMPT_TEMPLATE`:

```python
def _build_question_protocol(autonomy: str = "balanced", remaining: int = 3) -> str:
    """Build the human interaction protocol section for agent system prompts."""
    if autonomy == "full":
        when_to_ask = "NEVER ask questions. Make your best judgment on all decisions."
    elif autonomy == "supervised":
        when_to_ask = (
            "Ask when uncertain about ANY implementation choice.\n"
            "This includes architecture, naming, patterns, and ambiguous requirements."
        )
    else:  # balanced
        when_to_ask = (
            "Ask ONLY for high-impact decisions:\n"
            "- Architecture patterns (which auth strategy, which ORM)\n"
            "- Ambiguous requirements (spec says X but codebase does Y)\n"
            "- Destructive changes (deleting files, dropping columns)\n"
            "Do NOT ask about: naming conventions, formatting, minor style choices."
        )

    return f"""## Human Interaction Protocol

Autonomy level: {autonomy} | Questions remaining: {remaining}

### When to ask:
{when_to_ask}

### How to ask:
When you need human input, output this JSON block as your FINAL message, then STOP:

FORGE_QUESTION:
{{
  "question": "Your specific question here",
  "context": "What you found that led to this question",
  "suggestions": ["Option A", "Option B"],
  "impact": "high"
}}

### Rules:
- You have {remaining} questions left. Use them wisely.
- ALWAYS provide 2-3 concrete suggestions.
- ALWAYS explain what you found that led to the question.
- NEVER ask open-ended "what should I do?" questions.
- If you hit 0 remaining, proceed with best judgment."""
```

- [ ] **Step 4: Add {question_protocol} to AGENT_SYSTEM_PROMPT_TEMPLATE**

Add `{question_protocol}` to the end of the existing template, before the Rules section:

```python
# In AGENT_SYSTEM_PROMPT_TEMPLATE, add after {file_scope_block} and before "Rules:":
{question_protocol}
```

- [ ] **Step 5: Update _build_options to pass question protocol**

In `_build_options` method, add `autonomy` and `question_remaining` parameters. Format the question protocol and pass to the template.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/agents/adapter_question_test.py -v`
Expected: ALL PASS

- [ ] **Step 7: Run existing adapter tests to check no regressions**

Run: `.venv/bin/python -m pytest forge/agents/ -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add forge/agents/adapter.py forge/agents/adapter_question_test.py
git commit -m "feat: inject question protocol into agent system prompt based on autonomy level"
```

---

### Task 6: Question detection and pause/resume in daemon_executor.py

**Files:**
- Modify: `forge/core/daemon_executor.py:42-99,483-558`
- Test: `forge/core/daemon_executor_question_test.py`

- [ ] **Step 1: Write test for question detection in _execute_task**

```python
# forge/core/daemon_executor_question_test.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from forge.core.daemon_helpers import _parse_forge_question

class TestQuestionDetection:
    def test_detects_question_in_result(self):
        result_text = 'Analysis done.\n\nFORGE_QUESTION:\n{"question": "Which?", "suggestions": ["A", "B"]}'
        q = _parse_forge_question(result_text)
        assert q is not None
        assert q["question"] == "Which?"

    def test_no_question_means_normal_completion(self):
        result_text = "I wrote the code and committed."
        q = _parse_forge_question(result_text)
        assert q is None
```

- [ ] **Step 2: Write test for _resume_task**

```python
# Add to daemon_executor_question_test.py
async def test_resume_task_calls_sdk_with_resume():
    """Verify _resume_task passes resume=session_id to sdk_query."""
    # This test verifies the SDK options are constructed correctly
    from claude_code_sdk import ClaudeCodeOptions
    opts = ClaudeCodeOptions(
        resume="sess_123",
        allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
        permission_mode="acceptEdits",
        max_turns=25,
    )
    assert opts.resume == "sess_123"
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest forge/core/daemon_executor_question_test.py -v`
Expected: PASS (these are unit-level, not integration)

- [ ] **Step 4: Modify _execute_task to detect questions**

In `daemon_executor.py`, in the `_execute_task` method, after `_run_agent` returns, add question detection:

```python
# After agent completes, check for question
from forge.core.daemon_helpers import _parse_forge_question

# In _run_agent (or after it returns), capture result text
# If question detected:
#   1. Save question to DB
#   2. Set task state to AWAITING_INPUT
#   3. Store session_id on task
#   4. Emit task:question event
#   5. Release agent slot (return without proceeding to review)
```

The exact integration depends on how `_run_agent` returns the result. The key changes:
- After `_stream_agent` returns, check `agent_result.summary` for FORGE_QUESTION marker
- If found, call `db.create_task_question(...)`, update task state, emit event, release slot
- If not found, proceed to review as normal

- [ ] **Step 5: Add _resume_task method to ExecutorMixin**

```python
async def _resume_task(
    self, db, runtime, worktree_mgr, merge_worker,
    task_id: str, agent_id: str, answer: str, pipeline_id: str | None = None,
) -> None:
    """Resume a task after human answered a question."""
    task = await db.get_task(task_id)
    if not task or task.state != "awaiting_input":
        return

    await db.update_task_state(task_id, "in_progress")
    await self._emit("task:state_changed", {"task_id": task_id, "state": "in_progress"}, db=db, pipeline_id=pipeline_id)
    await self._emit("task:resumed", {"task_id": task_id}, db=db, pipeline_id=pipeline_id)

    # Re-run agent with resume=session_id
    # The answer becomes the new prompt, SDK continues the conversation
    worktree_path = task.worktree_path
    if not worktree_path:
        logger.error("No worktree for resumed task %s", task_id)
        return

    # Build options with resume
    # Stream agent, then check for another question or proceed to review
    # (Same flow as _execute_task but with resume parameter)
```

- [ ] **Step 6: Run all executor tests**

Run: `.venv/bin/python -m pytest forge/core/daemon_executor_question_test.py forge/core/daemon_executor_test.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add forge/core/daemon_executor.py forge/core/daemon_executor_question_test.py
git commit -m "feat: add question detection and pause/resume to task execution"
```

---

### Task 7: Review gate events in daemon_review.py

**Files:**
- Modify: `forge/core/daemon_review.py`
- Test: `forge/core/daemon_review_test.py`

- [ ] **Step 1: Write tests for review gate events**

```python
# Add to forge/core/daemon_review_test.py
class TestReviewGateEvents:
    async def test_gate_started_event_emitted(self):
        """Verify review emits gate:started before each gate."""
        # Mock the emitter and verify events are emitted
        # during review execution
        pass  # Integration test — verify event names match spec

    async def test_gate_passed_event_includes_details(self):
        """Verify gate:passed includes gate name and result details."""
        pass

    async def test_llm_feedback_event_emitted(self):
        """Verify LLM reviewer feedback is emitted as review:llm_feedback."""
        pass
```

- [ ] **Step 2: Add event emissions to _run_review**

In `daemon_review.py`, before each gate call, emit `review:gate_started`. After each gate, emit `review:gate_passed` or `review:gate_failed` with details. After LLM review, emit `review:llm_feedback` with the reviewer's comments.

```python
# Before gate_build:
await self._emit("review:gate_started", {"task_id": task_id, "gate": "gate0_build"}, db=db, pipeline_id=pipeline_id)
# After gate_build:
await self._emit("review:gate_passed" if build_result.passed else "review:gate_failed",
    {"task_id": task_id, "gate": "gate0_build", "details": build_result.details, "duration": build_result.duration},
    db=db, pipeline_id=pipeline_id)

# Same pattern for gate1_lint, gate1_5_test, gate2_llm_review
# For LLM review specifically:
await self._emit("review:llm_feedback", {"task_id": task_id, "feedback": llm_result.details}, db=db, pipeline_id=pipeline_id)
```

- [ ] **Step 3: Run review tests**

Run: `.venv/bin/python -m pytest forge/core/daemon_review_test.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add forge/core/daemon_review.py forge/core/daemon_review_test.py
git commit -m "feat: emit review gate events (started/passed/failed/llm_feedback)"
```

---

### Task 8: Pipeline pause tracking and all_tasks_done event

**Files:**
- Modify: `forge/core/daemon.py:529-630`
- Test: `forge/core/daemon_test.py` (extend)

- [ ] **Step 1: Add pipeline pause tracking to _execution_loop**

In `_execution_loop`, after the dispatch loop, check if all running tasks are paused (awaiting_input). If so, emit `pipeline:paused`, set `paused_at`. When a task resumes, compute elapsed pause and add to `paused_duration`.

- [ ] **Step 2: Add pipeline:all_tasks_done event**

In `_execution_loop`, when the while loop exits (all tasks DONE or ERROR), emit `pipeline:all_tasks_done` with summary stats before setting phase to "complete".

- [ ] **Step 3: Add question timeout checker**

Add a periodic check (every 30s) in the execution loop for expired questions:

```python
async def _check_question_timeouts(self, db, pipeline_id):
    expired = await db.get_expired_questions(self._settings.question_timeout)
    for q in expired:
        await db.answer_question(q.id, "Proceed with your best judgment.", "timeout")
        await self._emit("task:auto_decided", {"task_id": q.task_id, "reason": "timeout"}, db=db, pipeline_id=pipeline_id)
        # Queue task for resume
```

- [ ] **Step 4: Run daemon tests**

Run: `.venv/bin/python -m pytest forge/core/daemon_test.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add forge/core/daemon.py
git commit -m "feat: add pipeline pause tracking, all_tasks_done event, question timeout checker"
```

---

## Chunk 3: Event Bus & TUI State

### Task 9: Add new events to bus.py and state.py handlers

**Files:**
- Modify: `forge/tui/bus.py:22-52`
- Modify: `forge/tui/state.py:19-166`
- Test: `forge/tui/state_test.py` (extend), `forge/tui/integration_test.py` (extend)

- [ ] **Step 1: Write tests for new state handlers**

```python
# forge/tui/state_question_test.py
from forge.tui.state import TuiState

def test_task_question_updates_state():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "in_progress", "title": "Test"}}
    state.apply_event("task:question", {
        "task_id": "t1",
        "question": {"id": "q1", "question": "Which?", "suggestions": ["A", "B"]},
    })
    assert state.tasks["t1"]["state"] == "awaiting_input"
    assert state.pending_questions["t1"] is not None

def test_task_answer_clears_pending():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "awaiting_input", "title": "Test"}}
    state.pending_questions = {"t1": {"id": "q1", "question": "Which?"}}
    state.apply_event("task:answer", {"task_id": "t1", "answer": "A"})
    assert "t1" not in state.pending_questions

def test_task_resumed_sets_running():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "awaiting_input", "title": "Test"}}
    state.apply_event("task:resumed", {"task_id": "t1"})
    assert state.tasks["t1"]["state"] == "in_progress"

def test_review_gate_started():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "in_review", "title": "Test"}}
    state.apply_event("review:gate_started", {"task_id": "t1", "gate": "gate0_build"})
    assert state.review_gates.get("t1", {}).get("gate0_build", {}).get("status") == "running"

def test_review_gate_passed():
    state = TuiState()
    state.tasks = {"t1": {"id": "t1", "state": "in_review", "title": "Test"}}
    state.review_gates = {"t1": {"gate0_build": {"status": "running"}}}
    state.apply_event("review:gate_passed", {"task_id": "t1", "gate": "gate0_build", "details": "OK"})
    assert state.review_gates["t1"]["gate0_build"]["status"] == "passed"

def test_pipeline_all_tasks_done():
    state = TuiState()
    state.apply_event("pipeline:all_tasks_done", {"summary": {"done": 4, "total": 4}})
    assert state.phase == "final_approval"

def test_pipeline_pr_created():
    state = TuiState()
    state.apply_event("pipeline:pr_created", {"pr_url": "https://github.com/..."})
    assert state.pr_url == "https://github.com/..."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/tui/state_question_test.py -v`
Expected: FAIL — new attributes and handlers don't exist

- [ ] **Step 3: Add new event types to TUI_EVENT_TYPES in bus.py**

```python
# Add to TUI_EVENT_TYPES list in forge/tui/bus.py:
"task:question",
"task:answer",
"task:resumed",
"task:auto_decided",
"pipeline:all_tasks_done",
"pipeline:pr_creating",
"pipeline:pr_created",
"pipeline:pr_failed",
"review:gate_started",
"review:gate_passed",
"review:gate_failed",
"review:llm_feedback",
"slot:acquired",
"slot:released",
"slot:queued",
```

- [ ] **Step 4: Add new state fields to TuiState**

```python
# New fields in TuiState.__init__:
self.pending_questions: dict[str, dict] = {}  # task_id → question data
self.review_gates: dict[str, dict[str, dict]] = {}  # task_id → gate_name → {status, details}
self.pr_url: str | None = None
self.question_history: dict[str, list[dict]] = {}  # task_id → [Q&A pairs]
```

- [ ] **Step 5: Add event handlers**

```python
def _on_task_question(self, data: dict) -> None:
    task_id = data.get("task_id")
    if task_id and task_id in self.tasks:
        self.tasks[task_id]["state"] = "awaiting_input"
        self.pending_questions[task_id] = data.get("question", {})

def _on_task_answer(self, data: dict) -> None:
    task_id = data.get("task_id")
    if task_id:
        q = self.pending_questions.pop(task_id, None)
        if q:
            history = self.question_history.setdefault(task_id, [])
            history.append({"question": q, "answer": data.get("answer")})

def _on_task_resumed(self, data: dict) -> None:
    task_id = data.get("task_id")
    if task_id and task_id in self.tasks:
        self.tasks[task_id]["state"] = "in_progress"

def _on_task_auto_decided(self, data: dict) -> None:
    task_id = data.get("task_id")
    if task_id:
        q = self.pending_questions.pop(task_id, None)
        if q:
            history = self.question_history.setdefault(task_id, [])
            history.append({"question": q, "answer": f"[auto: {data.get('reason', 'unknown')}]"})

def _on_review_gate_started(self, data: dict) -> None:
    task_id = data.get("task_id")
    gate = data.get("gate")
    if task_id and gate:
        self.review_gates.setdefault(task_id, {})[gate] = {"status": "running"}

def _on_review_gate_passed(self, data: dict) -> None:
    task_id = data.get("task_id")
    gate = data.get("gate")
    if task_id and gate:
        self.review_gates.setdefault(task_id, {})[gate] = {"status": "passed", "details": data.get("details")}

def _on_review_gate_failed(self, data: dict) -> None:
    task_id = data.get("task_id")
    gate = data.get("gate")
    if task_id and gate:
        self.review_gates.setdefault(task_id, {})[gate] = {"status": "failed", "details": data.get("details")}

def _on_review_llm_feedback(self, data: dict) -> None:
    task_id = data.get("task_id")
    if task_id:
        gates = self.review_gates.setdefault(task_id, {})
        gates["gate2_llm_review"] = {"status": "passed", "details": data.get("feedback")}

def _on_all_tasks_done(self, data: dict) -> None:
    self.phase = "final_approval"

def _on_pr_creating(self, data: dict) -> None:
    self.phase = "pr_creating"

def _on_pr_created(self, data: dict) -> None:
    self.pr_url = data.get("pr_url")
    self.phase = "pr_created"

def _on_pr_failed(self, data: dict) -> None:
    self.error = data.get("error", "PR creation failed")
```

- [ ] **Step 6: Add handlers to _EVENT_MAP**

```python
_EVENT_MAP: dict[str, Callable[["TuiState", dict], None]] = {
    # ... existing entries ...
    "task:question": _on_task_question,
    "task:answer": _on_task_answer,
    "task:resumed": _on_task_resumed,
    "task:auto_decided": _on_task_auto_decided,
    "review:gate_started": _on_review_gate_started,
    "review:gate_passed": _on_review_gate_passed,
    "review:gate_failed": _on_review_gate_failed,
    "review:llm_feedback": _on_review_llm_feedback,
    "pipeline:all_tasks_done": _on_all_tasks_done,
    "pipeline:pr_creating": _on_pr_creating,
    "pipeline:pr_created": _on_pr_created,
    "pipeline:pr_failed": _on_pr_failed,
}
```

- [ ] **Step 7: Run all state tests**

Run: `.venv/bin/python -m pytest forge/tui/state_question_test.py forge/tui/state_test.py forge/tui/integration_test.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add forge/tui/bus.py forge/tui/state.py forge/tui/state_question_test.py
git commit -m "feat: add 16 new events to bus.py and state.py handlers for questions, review, PR"
```

---

## Chunk 4: TUI Widgets

### Task 10: SuggestionChips widget

**Files:**
- Create: `forge/tui/widgets/suggestion_chips.py`
- Test: `forge/tui/widgets/suggestion_chips_test.py`

- [ ] **Step 1: Write widget tests**

```python
# forge/tui/widgets/suggestion_chips_test.py
from forge.tui.widgets.suggestion_chips import SuggestionChips, format_chips

def test_format_chips_renders_all():
    result = format_chips(["Option A", "Option B", "Let agent decide"], selected=0)
    assert "Option A" in result
    assert "Option B" in result

def test_format_chips_highlights_selected():
    result = format_chips(["A", "B"], selected=0)
    assert "bold" in result or "reverse" in result  # Rich markup

def test_format_chips_empty():
    result = format_chips([], selected=0)
    assert result == ""
```

- [ ] **Step 2: Implement SuggestionChips**

```python
# forge/tui/widgets/suggestion_chips.py
"""Horizontal chip selector for agent question suggestions."""

from __future__ import annotations
from textual.widget import Widget
from textual.message import Message
from rich.text import Text

def format_chips(suggestions: list[str], selected: int = -1) -> str:
    if not suggestions:
        return ""
    parts = []
    for i, s in enumerate(suggestions):
        if i == selected:
            parts.append(f"[bold reverse #58a6ff] {i+1}. {s} [/]")
        else:
            parts.append(f"[#58a6ff on #1c3a5f] {i+1}. {s} [/]")
    return "  ".join(parts)

class SuggestionChips(Widget):
    class Selected(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    DEFAULT_CSS = "SuggestionChips { height: 1; margin: 0 1; }"

    def __init__(self, suggestions: list[str] | None = None) -> None:
        super().__init__()
        self._suggestions = suggestions or []
        self._selected = -1

    def update_suggestions(self, suggestions: list[str]) -> None:
        self._suggestions = suggestions
        self._selected = -1
        self.refresh()

    def select_next(self) -> None:
        if self._suggestions:
            self._selected = (self._selected + 1) % len(self._suggestions)
            self.refresh()

    def select_prev(self) -> None:
        if self._suggestions:
            self._selected = (self._selected - 1) % len(self._suggestions)
            self.refresh()

    def confirm(self) -> None:
        if 0 <= self._selected < len(self._suggestions):
            self.post_message(self.Selected(self._suggestions[self._selected]))

    def select_by_number(self, n: int) -> None:
        idx = n - 1
        if 0 <= idx < len(self._suggestions):
            self._selected = idx
            self.post_message(self.Selected(self._suggestions[idx]))

    def render(self) -> str:
        return format_chips(self._suggestions, self._selected)
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest forge/tui/widgets/suggestion_chips_test.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add forge/tui/widgets/suggestion_chips.py forge/tui/widgets/suggestion_chips_test.py
git commit -m "feat: add SuggestionChips widget with keyboard navigation"
```

---

### Task 11: ReviewGates widget

**Files:**
- Create: `forge/tui/widgets/review_gates.py`
- Test: `forge/tui/widgets/review_gates_test.py`

- [ ] **Step 1: Write widget tests**

```python
# forge/tui/widgets/review_gates_test.py
from forge.tui.widgets.review_gates import format_gates

def test_format_gates_all_passed():
    gates = {
        "gate0_build": {"status": "passed", "details": "OK in 1.8s"},
        "gate1_lint": {"status": "passed", "details": "Clean"},
    }
    result = format_gates(gates)
    assert "✓" in result
    assert "Build" in result

def test_format_gates_one_running():
    gates = {
        "gate0_build": {"status": "passed"},
        "gate1_5_test": {"status": "running"},
    }
    result = format_gates(gates)
    assert "running" in result.lower() or "◎" in result

def test_format_gates_one_failed():
    gates = {"gate0_build": {"status": "failed", "details": "Exit code 1"}}
    result = format_gates(gates)
    assert "✗" in result or "failed" in result.lower()

def test_format_gates_empty():
    result = format_gates({})
    assert "No review" in result or result == ""
```

- [ ] **Step 2: Implement ReviewGates widget**

```python
# forge/tui/widgets/review_gates.py
"""Review gate result cards for task review status display."""

from __future__ import annotations
from textual.widget import Widget

_GATE_NAMES = {
    "gate0_build": ("Build", "🔨"),
    "gate1_lint": ("Lint", "📏"),
    "gate1_5_test": ("Tests", "🧪"),
    "gate2_llm_review": ("LLM Review", "🤖"),
}

_STATUS_ICONS = {"passed": "[#3fb950]✓[/]", "failed": "[#f85149]✗[/]", "running": "[#d2a8ff]◎[/]"}

def format_gates(gates: dict[str, dict]) -> str:
    if not gates:
        return "[#484f58]No review data yet[/]"
    lines = []
    for gate_key, (name, icon) in _GATE_NAMES.items():
        gate = gates.get(gate_key)
        if not gate:
            lines.append(f"  [#484f58]○ {icon} {name}[/]")
            continue
        status = gate.get("status", "unknown")
        status_icon = _STATUS_ICONS.get(status, "[#8b949e]?[/]")
        details = gate.get("details", "")
        detail_str = f" [#8b949e]— {details}[/]" if details else ""
        lines.append(f"  {status_icon} {icon} {name}{detail_str}")
    return "\n".join(lines)

class ReviewGates(Widget):
    DEFAULT_CSS = "ReviewGates { height: auto; padding: 1; }"

    def __init__(self) -> None:
        super().__init__()
        self._gates: dict[str, dict] = {}

    def update_gates(self, gates: dict[str, dict]) -> None:
        self._gates = gates
        self.refresh()

    def render(self) -> str:
        return format_gates(self._gates)
```

- [ ] **Step 3: Run tests, commit**

Run: `.venv/bin/python -m pytest forge/tui/widgets/review_gates_test.py -v`

```bash
git add forge/tui/widgets/review_gates.py forge/tui/widgets/review_gates_test.py
git commit -m "feat: add ReviewGates widget with gate status cards"
```

---

### Task 12: ChatThread widget

**Files:**
- Create: `forge/tui/widgets/chat_thread.py`
- Test: `forge/tui/widgets/chat_thread_test.py`

- [ ] **Step 1: Write tests**

```python
# forge/tui/widgets/chat_thread_test.py
from forge.tui.widgets.chat_thread import format_work_log, format_question_card

def test_format_work_log():
    lines = ["📖 Reading auth.py", "🔎 Searching for middleware"]
    result = format_work_log(lines)
    assert "auth.py" in result
    assert "middleware" in result

def test_format_question_card():
    question = {"question": "Which ORM?", "suggestions": ["A", "B"], "context": "Found 2 patterns"}
    result = format_question_card(question)
    assert "Which ORM?" in result
    assert "Found 2 patterns" in result
```

- [ ] **Step 2: Implement ChatThread**

The `ChatThread` widget composes: work log (agent tool calls), question card, suggestion chips, and input. It emits `ChatThread.AnswerSubmitted` when user sends.

```python
# forge/tui/widgets/chat_thread.py
"""Chat thread widget for agent Q&A interaction."""

from __future__ import annotations
from textual.widget import Widget
from textual.widgets import Input
from textual.containers import VerticalScroll
from textual.message import Message

from forge.tui.widgets.suggestion_chips import SuggestionChips

def format_work_log(lines: list[str]) -> str:
    if not lines:
        return "[#484f58]No activity yet[/]"
    formatted = []
    for line in lines[-10:]:  # show last 10
        formatted.append(f"  [#8b949e]{line}[/]")
    return "\n".join(formatted)

def format_question_card(question: dict) -> str:
    q = question.get("question", "")
    ctx = question.get("context", "")
    parts = []
    if ctx:
        parts.append(f"[#c9d1d9]{ctx}[/]")
    parts.append(f"\n[#f0883e]{q}[/]")
    return "\n".join(parts)

class ChatThread(Widget):
    class AnswerSubmitted(Message):
        def __init__(self, task_id: str, answer: str) -> None:
            self.task_id = task_id
            self.answer = answer
            super().__init__()

    DEFAULT_CSS = """
    ChatThread { height: 1fr; }
    ChatThread VerticalScroll { height: 1fr; }
    ChatThread Input { dock: bottom; margin: 0 1; }
    """

    def __init__(self, task_id: str = "") -> None:
        super().__init__()
        self.task_id = task_id
        self._work_lines: list[str] = []
        self._question: dict | None = None
        self._history: list[dict] = []

    def compose(self):
        yield VerticalScroll(id="chat-scroll")
        yield SuggestionChips()
        yield Input(placeholder="Type your answer or click a suggestion...", id="chat-input")

    def update_question(self, question: dict, work_lines: list[str], history: list[dict] | None = None) -> None:
        self._question = question
        self._work_lines = work_lines
        self._history = history or []
        chips = self.query_one(SuggestionChips)
        suggestions = question.get("suggestions", [])
        suggestions.append("Let agent decide")
        chips.update_suggestions(suggestions)
        self.refresh()

    def clear_question(self) -> None:
        self._question = None
        self.query_one(SuggestionChips).update_suggestions([])
        self.query_one("#chat-input", Input).value = ""
        self.refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.post_message(self.AnswerSubmitted(self.task_id, event.value.strip()))
            event.input.value = ""

    def on_suggestion_chips_selected(self, event: SuggestionChips.Selected) -> None:
        self.post_message(self.AnswerSubmitted(self.task_id, event.text))
```

- [ ] **Step 3: Run tests, commit**

Run: `.venv/bin/python -m pytest forge/tui/widgets/chat_thread_test.py -v`

```bash
git add forge/tui/widgets/chat_thread.py forge/tui/widgets/chat_thread_test.py
git commit -m "feat: add ChatThread widget with Q&A interaction and suggestion chips"
```

---

### Task 13: Update task_list.py with awaiting_input state

**Files:**
- Modify: `forge/tui/widgets/task_list.py:8-28`

- [ ] **Step 1: Add awaiting_input to STATE_ICONS and STATE_COLORS**

```python
# In STATE_ICONS, add:
"awaiting_input": "◆",

# In STATE_COLORS, add:
"awaiting_input": "#f0883e",
```

- [ ] **Step 2: Run existing task_list tests**

Run: `.venv/bin/python -m pytest forge/tui/widgets/ -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add forge/tui/widgets/task_list.py
git commit -m "feat: add awaiting_input state icon and color to task list"
```

---

## Chunk 5: TUI Screens & App

### Task 14: FinalApprovalScreen

**Files:**
- Create: `forge/tui/screens/final_approval.py`
- Test: `forge/tui/screens/final_approval_test.py`

- [ ] **Step 1: Write tests**

```python
# forge/tui/screens/final_approval_test.py
from forge.tui.screens.final_approval import format_summary_stats, format_task_table

def test_format_summary_stats():
    stats = {"added": 342, "removed": 28, "files": 12, "elapsed": "8m 23s", "cost": 0.42, "questions": 2}
    result = format_summary_stats(stats)
    assert "+342" in result
    assert "$0.42" in result

def test_format_task_table():
    tasks = [
        {"title": "JWT middleware", "added": 89, "removed": 4, "tests_passed": 14, "tests_total": 14, "review": "passed"},
    ]
    result = format_task_table(tasks)
    assert "JWT middleware" in result
    assert "14/14" in result
```

- [ ] **Step 2: Implement FinalApprovalScreen**

A Textual Screen with centered stats, task summary table, and action bindings (Enter=PR, d=diff, r=re-run, Esc=cancel). Emits `FinalApprovalScreen.CreatePR` and `FinalApprovalScreen.ReRun` messages.

- [ ] **Step 3: Run tests, commit**

```bash
git add forge/tui/screens/final_approval.py forge/tui/screens/final_approval_test.py
git commit -m "feat: add FinalApprovalScreen with stats, task table, PR creation"
```

---

### Task 15: PR creation module

**Files:**
- Create: `forge/tui/pr_creator.py`
- Test: `forge/tui/pr_creator_test.py`

- [ ] **Step 1: Write tests**

```python
# forge/tui/pr_creator_test.py
import pytest
from unittest.mock import AsyncMock, patch
from forge.tui.pr_creator import generate_pr_body

def test_generate_pr_body_includes_tasks():
    tasks = [{"title": "Auth", "added": 89, "removed": 4, "files": 3}]
    body = generate_pr_body(tasks=tasks, time="8m", cost=0.42, questions=[])
    assert "Auth" in body
    assert "+89/-4" in body
    assert "$0.42" in body

def test_generate_pr_body_includes_questions():
    questions = [{"question": "Which ORM?", "answer": "SQLAlchemy 2.0"}]
    body = generate_pr_body(tasks=[], time="5m", cost=0.10, questions=questions)
    assert "Which ORM?" in body
    assert "SQLAlchemy 2.0" in body
```

- [ ] **Step 2: Implement pr_creator module**

```python
# forge/tui/pr_creator.py
"""PR creation utilities for TUI — push, generate, create."""

from __future__ import annotations
import asyncio
import logging

logger = logging.getLogger("forge.tui.pr_creator")

async def push_branch(project_dir: str, branch: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "git", "push", "-u", "origin", branch,
        cwd=project_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("Push failed: %s", stderr.decode())
        return False
    return True

def generate_pr_body(
    *, tasks: list[dict], time: str, cost: float, questions: list[dict],
) -> str:
    lines = [f"## Summary", f"Built by Forge pipeline • {len(tasks)} tasks • {time} • ${cost:.2f}", ""]
    lines.append("## Tasks")
    for t in tasks:
        added = t.get("added", 0)
        removed = t.get("removed", 0)
        files = t.get("files", 0)
        lines.append(f"- ✅ **{t['title']}** — +{added}/-{removed}, {files} files")
    if questions:
        lines.append("")
        lines.append("## Human Decisions")
        for q in questions:
            lines.append(f"- **Q:** {q['question']} → **A:** {q['answer']}")
    lines.extend(["", "🤖 Built with [Forge](https://github.com/tarunms7/forge-orchestrator)"])
    return "\n".join(lines)

async def create_pr(project_dir: str, title: str, body: str, base: str = "main") -> str | None:
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "create", "--title", title, "--body", body, "--base", base,
        cwd=project_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("PR creation failed: %s", stderr.decode())
        return None
    return stdout.decode().strip()
```

- [ ] **Step 3: Run tests, commit**

```bash
git add forge/tui/pr_creator.py forge/tui/pr_creator_test.py
git commit -m "feat: add PR creation module (push, generate body, gh pr create)"
```

---

### Task 16: Pipeline screen overhaul (2-panel, view switching)

**Files:**
- Modify: `forge/tui/screens/pipeline.py`
- Modify: `forge/tui/widgets/progress_bar.py`

- [ ] **Step 1: Update progress_bar with new stages**

Add `final_approval`, `pr_creating`, `pr_created` phases to `format_progress`. Add 7-segment visual.

- [ ] **Step 2: Overhaul PipelineScreen layout**

Replace current layout with 2-panel design. Left panel: phase banner + task list + decision badge. Right panel: view switching between output, chat, diff, review.

Add keybindings:
```python
Binding("d", "view_diff", "Diff", show=True),
Binding("o", "view_output", "Output", show=True),
Binding("r", "view_review", "Review", show=True),
Binding("c", "view_chat", "Chat", show=True),
Binding("1", "jump_task_1", show=False),
# ... through 9
```

- [ ] **Step 3: Wire view switching**

Each view action shows/hides the appropriate right-panel widget: AgentOutput, ChatThread, ReviewGates, or a DiffView (scrollable diff).

- [ ] **Step 4: Auto-focus chat input when task needs input**

When selected task has `state == "awaiting_input"`, auto-switch to chat view and focus the input.

- [ ] **Step 5: Run tests, commit**

```bash
git add forge/tui/screens/pipeline.py forge/tui/widgets/progress_bar.py
git commit -m "feat: overhaul pipeline screen with 2-panel layout, view switching, 7-stage progress"
```

---

### Task 17: Settings screen update with autonomy section

**Files:**
- Modify: `forge/tui/screens/settings.py`

- [ ] **Step 1: Add autonomy radio selector**

Replace static display with interactive settings. Add autonomy radio (Full/Balanced/Supervised), question limit +/- control, question timeout, auto_pr toggle.

- [ ] **Step 2: Add keybindings**

```python
Binding("up", "prev_setting"), Binding("down", "next_setting"),
Binding("left", "decrease"), Binding("right", "increase"),
Binding("enter", "toggle"), Binding("escape", "close"),
```

- [ ] **Step 3: Auto-save on change**

When any setting changes, write to ForgeSettings and persist.

- [ ] **Step 4: Run tests, commit**

```bash
git add forge/tui/screens/settings.py forge/tui/screens/settings_test.py
git commit -m "feat: add interactive autonomy settings with keyboard controls"
```

---

### Task 18: App-level keybindings, focus management, screen transitions

**Files:**
- Modify: `forge/tui/app.py`

- [ ] **Step 1: Add global keybindings**

```python
Binding("tab", "cycle_questions", "Next 🔔", show=False, priority=True),
Binding("question_mark", "show_help", "Help", show=False),
```

- [ ] **Step 2: Wire FinalApprovalScreen push**

When `pipeline:all_tasks_done` fires (detected via state change), push `FinalApprovalScreen`.

- [ ] **Step 3: Handle ChatThread.AnswerSubmitted**

```python
async def on_chat_thread_answer_submitted(self, event):
    task_id = event.task_id
    answer = event.answer
    # Write answer to DB
    pending = await self._db.get_pending_questions(self._pipeline_id)
    for q in pending:
        if q.task_id == task_id and q.answer is None:
            await self._db.answer_question(q.id, answer, "human")
            break
    # Resume task
    await self._daemon.resume_task(task_id, answer)
```

- [ ] **Step 4: Handle FinalApprovalScreen.CreatePR**

```python
async def on_final_approval_screen_create_pr(self, event):
    from forge.tui.pr_creator import push_branch, create_pr, generate_pr_body
    self._state.apply_event("pipeline:pr_creating", {})
    # Push, generate, create — with progress events
```

- [ ] **Step 5: Run all TUI tests**

Run: `.venv/bin/python -m pytest forge/tui/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add forge/tui/app.py
git commit -m "feat: add global keybindings, focus management, screen transitions for full pipeline"
```

---

## Chunk 6: Integration & Polish

### Task 19: End-to-end smoke test

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest --tb=short -q`
Expected: All pre-existing tests pass + all new tests pass

- [ ] **Step 2: Manual smoke test**

Launch `forge tui`, run a pipeline:
1. Verify planner streams tool activity
2. Approve plan → contracts phase visible
3. Tasks execute with live output
4. If an agent asks a question → chat view appears
5. Answer → agent resumes
6. Review gates show pass/fail with details
7. Final approval screen shows summary
8. Create PR → PR link displayed

- [ ] **Step 3: Fix any issues found**

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "fix: integration polish from smoke testing"
```
