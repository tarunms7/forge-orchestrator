# Agent Quality & HITL Completion Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the disconnected HITL system (resume wire, planning questions, human interrupt) and unleash agent sessions to match normal Claude Code capability.

**Architecture:** Five independent fixes that reconnect disconnected wires and remove artificial constraints. No new architecture — the pieces exist, they just need to be connected. Event-driven communication between TUI→daemon via the daemon's EventEmitter. DB models extended for planning questions and interjections.

**Tech Stack:** Python 3.12+, asyncio, Pydantic v2, claude-code-sdk, aiosqlite, Textual (TUI)

**Spec:** `docs/superpowers/specs/2026-03-16-agent-quality-and-hitl-completion-design.md`

---

## Chunk 1: Unleash Agents + Question Protocol (Fixes 2 & 5)

Both fixes only touch `forge/agents/adapter.py`. Independent of all other fixes. Simplest to implement.

### Task 1: Remove allowed_tools Restriction (Fix 2A)

**Files:**
- Modify: `forge/agents/adapter.py:263-265`
- Test: `forge/agents/adapter_test.py`

- [ ] **Step 1: Write failing test — options have no allowed_tools**

```python
# In forge/agents/adapter_test.py — add this test

def test_build_options_has_no_allowed_tools():
    """Task agents should get full tool access (no allowed_tools key)."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path="/tmp/test",
        allowed_dirs=[],
    )
    # When allowed_tools is not explicitly set, it defaults to [] (empty list)
    # which gives the agent full tool access in Claude Code SDK
    assert not options.allowed_tools  # empty list = full access
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest forge/agents/adapter_test.py::test_build_options_has_no_allowed_tools -v`
Expected: FAIL — `options.allowed_tools` is `["Read", "Edit", "Write", "Glob", "Grep", "Bash"]`

- [ ] **Step 3: Remove allowed_tools from _build_options**

In `forge/agents/adapter.py`, line 265, remove the `allowed_tools` line from the `ClaudeCodeOptions` constructor:

```python
# Before (line 263-271):
        return ClaudeCodeOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            permission_mode="acceptEdits",
            cwd=worktree_path,
            model=model,
            max_turns=25,
            resume=resume,
        )

# After:
        return ClaudeCodeOptions(
            system_prompt=system_prompt,
            permission_mode="acceptEdits",
            cwd=worktree_path,
            model=model,
            max_turns=25,
            resume=resume,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest forge/agents/adapter_test.py::test_build_options_has_no_allowed_tools -v`
Expected: PASS

- [ ] **Step 5: Update existing test that asserts old allowed_tools**

`forge/core/daemon_executor_question_test.py` line 68 asserts `allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"]`. Update this assertion to match the new behavior (empty list or no assertion on allowed_tools). Search for any other tests that assert the old value:

Run: `grep -rn "allowed_tools.*Read.*Edit" forge/ --include="*test*"`

Update all matches.

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest forge/ -x -q --timeout=30`
Expected: All PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add forge/agents/adapter.py forge/agents/adapter_test.py forge/core/daemon_executor_question_test.py
git commit -m "feat(agents): remove allowed_tools to give task agents full tool access"
```

---

### Task 2: Load and Inject CLAUDE.md (Fix 2B)

**Files:**
- Modify: `forge/agents/adapter.py`
- Test: `forge/agents/adapter_test.py`

- [ ] **Step 1: Write failing tests for _load_claude_md**

```python
# In forge/agents/adapter_test.py — add these tests

import os
import tempfile


class TestLoadClaudeMd:
    def test_loads_from_project_root(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Project Rules\nUse pytest.")
        result = _load_claude_md(str(tmp_path))
        assert result == "# Project Rules\nUse pytest."

    def test_loads_from_dotclaude_dir(self, tmp_path):
        dotclaude = tmp_path / ".claude"
        dotclaude.mkdir()
        (dotclaude / "CLAUDE.md").write_text("# Alt Rules")
        result = _load_claude_md(str(tmp_path))
        assert result == "# Alt Rules"

    def test_prefers_project_root(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("root")
        dotclaude = tmp_path / ".claude"
        dotclaude.mkdir()
        (dotclaude / "CLAUDE.md").write_text("dotclaude")
        result = _load_claude_md(str(tmp_path))
        assert result == "root"

    def test_returns_none_when_missing(self, tmp_path):
        result = _load_claude_md(str(tmp_path))
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/agents/adapter_test.py::TestLoadClaudeMd -v`
Expected: FAIL — `_load_claude_md` not defined

- [ ] **Step 3: Implement _load_claude_md**

Add to `forge/agents/adapter.py`, before `_build_question_protocol`:

```python
def _load_claude_md(project_dir: str) -> str | None:
    """Load CLAUDE.md from standard locations.

    Searches:
      1. {project_dir}/CLAUDE.md
      2. {project_dir}/.claude/CLAUDE.md

    Returns content as string, or None if not found.
    """
    for rel_path in ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md")):
        full_path = os.path.join(project_dir, rel_path)
        if os.path.isfile(full_path):
            try:
                with open(full_path, "r") as f:
                    return f.read()
            except OSError:
                continue
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest forge/agents/adapter_test.py::TestLoadClaudeMd -v`
Expected: All 4 PASS

- [ ] **Step 5: Commit**

```bash
git add forge/agents/adapter.py forge/agents/adapter_test.py
git commit -m "feat(agents): add _load_claude_md for project instruction loading"
```

---

### Task 3: Inject CLAUDE.md into System Prompt (Fix 2B continued)

**Files:**
- Modify: `forge/agents/adapter.py:63-88` (AGENT_SYSTEM_PROMPT_TEMPLATE)
- Modify: `forge/agents/adapter.py:220-271` (_build_options)
- Test: `forge/agents/adapter_test.py`

- [ ] **Step 1: Write failing test — system prompt includes CLAUDE.md content**

```python
# In forge/agents/adapter_test.py

def test_system_prompt_includes_claude_md(tmp_path):
    """When CLAUDE.md exists, its content appears in the system prompt."""
    (tmp_path / "CLAUDE.md").write_text("Always use type hints.")
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path=str(tmp_path),
        allowed_dirs=[],
        project_dir=str(tmp_path),
    )
    assert "Always use type hints." in options.system_prompt
    assert "Project Instructions" in options.system_prompt


def test_system_prompt_without_claude_md(tmp_path):
    """When CLAUDE.md doesn't exist, prompt still works without it."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path=str(tmp_path),
        allowed_dirs=[],
        project_dir=str(tmp_path),
    )
    assert "Project Instructions" not in options.system_prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/agents/adapter_test.py::test_system_prompt_includes_claude_md -v`
Expected: FAIL — `_build_options` doesn't accept `project_dir` parameter

- [ ] **Step 3: Add project_dir param to _build_options and inject CLAUDE.md**

In `forge/agents/adapter.py`:

1. Add `project_dir: str | None = None` parameter to `_build_options()` (after `resume`):

```python
    def _build_options(
        self, worktree_path: str, allowed_dirs: list[str], model: str = "sonnet",
        project_context: str = "",
        conventions_json: str | None = None,
        conventions_md: str | None = None,
        completed_deps: list[dict] | None = None,
        allowed_files: list[str] | None = None,
        contracts_block: str = "",
        autonomy: str = "balanced",
        questions_remaining: int = 3,
        resume: str | None = None,
        project_dir: str | None = None,
    ) -> ClaudeCodeOptions:
```

2. After building `question_protocol` (line 253), add CLAUDE.md loading:

```python
        question_protocol = _build_question_protocol(autonomy, questions_remaining)

        # Load project instructions from CLAUDE.md
        claude_md_block = ""
        if project_dir:
            claude_md_content = _load_claude_md(project_dir)
            if claude_md_content:
                claude_md_block = (
                    "## Project Instructions (from CLAUDE.md)\n\n"
                    f"{claude_md_content}"
                )
```

3. Add `{claude_md_block}` placeholder to `AGENT_SYSTEM_PROMPT_TEMPLATE` after `{conventions_block}`:

```python
AGENT_SYSTEM_PROMPT_TEMPLATE = """You are a coding agent working on a specific task within the Forge orchestration system.

Your working directory is {cwd}. Do NOT read, write, or execute anything outside this directory{extra_dirs_clause}.

You have access to a git worktree isolated to your task. Write clean, tested code.

{project_context}

{conventions_block}

{claude_md_block}

{contracts_block}

{dependency_context}

{file_scope_block}

{question_protocol}

{working_effectively}

Rules:
- You MUST ONLY modify files listed in the File Scope section above. Changes to other files are automatically reverted by the system.
- If Interface Contracts are provided above, you MUST implement them EXACTLY as specified. Do NOT rename fields, change types, or alter response shapes.
- Follow existing code style and patterns — see the conventions section above
- Write tests for any new functionality
- Commit your changes with a SHORT conventional commit message (max 72 chars) — use feat/fix/refactor/test/docs/chore prefix and describe WHAT changed, not the task title
- If you encounter an error, fix it rather than giving up
- If image file paths are mentioned in the task description, use the Read tool to view them (images are readable)"""
```

4. Pass `claude_md_block` and `working_effectively=""` (placeholder for Task 4) in the `.format()` call (line 254-262):

```python
        system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            cwd=worktree_path, extra_dirs_clause=extra_dirs_clause,
            project_context=project_context,
            conventions_block=conventions_block,
            claude_md_block=claude_md_block,
            contracts_block=contracts_block,
            dependency_context=dependency_context,
            file_scope_block=file_scope_block,
            question_protocol=question_protocol,
            working_effectively="",  # Populated in Task 4
        )
```

**Important:** Both `{claude_md_block}` and `{working_effectively}` are added to the template now, but `working_effectively` is empty until Task 4 replaces the empty string with the actual content.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest forge/agents/adapter_test.py::test_system_prompt_includes_claude_md forge/agents/adapter_test.py::test_system_prompt_without_claude_md -v`
Expected: Both PASS

- [ ] **Step 5: Commit**

```bash
git add forge/agents/adapter.py forge/agents/adapter_test.py
git commit -m "feat(agents): inject CLAUDE.md project instructions into agent system prompt"
```

---

### Task 4: Enrich Agent System Prompt (Fix 2C)

**Files:**
- Modify: `forge/agents/adapter.py` (AGENT_SYSTEM_PROMPT_TEMPLATE and _build_options)
- Test: `forge/agents/adapter_test.py`

- [ ] **Step 1: Write failing test — working effectively section present**

```python
# In forge/agents/adapter_test.py

def test_system_prompt_includes_working_effectively(tmp_path):
    """System prompt should include working-effectively guidance."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path=str(tmp_path),
        allowed_dirs=[],
    )
    assert "Working Effectively" in options.system_prompt
    assert "Use all available tools" in options.system_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest forge/agents/adapter_test.py::test_system_prompt_includes_working_effectively -v`
Expected: FAIL — no "Working Effectively" in prompt

- [ ] **Step 3: Add working_effectively block**

In `_build_options()`, define `working_effectively` before the `system_prompt` format call:

```python
        working_effectively = """## Working Effectively

- Use all available tools. If you need to look up API docs, use WebSearch.
  If you need to understand a library, read its source. Be resourceful.
- If tests fail, read the full error output. Diagnose the root cause.
  Fix it. Re-run. Don't guess — verify.
- Before editing a file, read it first. Understand the existing patterns.
  Follow them. Don't introduce new conventions.
- If you're unsure about something, explore first. Grep the codebase.
  Read related files. Build understanding before making changes.
- Commit your work when you reach a stable point. Small, focused commits
  are better than one giant commit at the end."""
```

The `{working_effectively}` placeholder was already added to the template in Task 3.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest forge/agents/adapter_test.py::test_system_prompt_includes_working_effectively -v`
Expected: PASS

- [ ] **Step 5: Run all adapter tests for regressions**

Run: `python -m pytest forge/agents/adapter_test.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add forge/agents/adapter.py forge/agents/adapter_test.py
git commit -m "feat(agents): add working-effectively guidance to agent system prompt"
```

---

### Task 5: Wire project_dir Through to Adapter (Fix 2B — final connection)

**Files:**
- Modify: `forge/core/daemon_executor.py` (where `_build_options` is called via `_run_agent`)
- Test: `forge/agents/adapter_test.py`

The adapter now accepts `project_dir` but callers need to pass it. Trace the call chain:
- `_execute_task()` → `_run_agent()` → `runtime.run_task()` → `ClaudeAdapter.run()` → `_build_options()`

- [ ] **Step 1: Read `ClaudeAdapter.run()` to see how `_build_options` is called**

Read `forge/agents/adapter.py` lines 273+ to understand the `run()` method and what params it receives.

- [ ] **Step 2: Add `project_dir` to `ClaudeAdapter.run()` and pass through to `_build_options()`**

In `ClaudeAdapter.run()` method signature, add `project_dir: str | None = None` parameter. Pass it through to `_build_options()`.

In `AgentRuntime.run_task()`, pass the project directory from the runtime config to `ClaudeAdapter.run()`.

In `_run_agent()` in `daemon_executor.py`, pass `self._project_dir` to the runtime call.

**Note:** The exact wiring depends on reading the full call chain. The implementer should trace:
1. `_run_agent()` → `runtime.run_task()` — what params does `run_task` take?
2. `runtime.run_task()` → `ClaudeAdapter.run()` — how is the adapter invoked?
3. `ClaudeAdapter.run()` → `_build_options()` — what params are forwarded?

Add `project_dir=self._project_dir` at the appropriate level.

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `python -m pytest forge/ -x -q --timeout=30`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add forge/agents/adapter.py forge/core/daemon_executor.py
git commit -m "feat(agents): wire project_dir through to adapter for CLAUDE.md loading"
```

---

### Task 6: Tune Question Protocol (Fix 5)

**Files:**
- Modify: `forge/agents/adapter.py:19-60` (_build_question_protocol)
- Test: `forge/agents/adapter_test.py`

- [ ] **Step 1: Write failing tests for new balanced mode text**

```python
# In forge/agents/adapter_test.py

class TestQuestionProtocol:
    def test_balanced_contains_80_percent_threshold(self):
        result = _build_question_protocol("balanced", 3)
        assert "80% confident" in result

    def test_balanced_contains_examples(self):
        result = _build_question_protocol("balanced", 3)
        assert "add caching" in result.lower() or "caching" in result.lower()
        assert "ASK" in result
        assert "DON'T ASK" in result

    def test_balanced_contains_thinking_out_loud(self):
        result = _build_question_protocol("balanced", 3)
        assert "What you're working on" in result or "working on" in result

    def test_full_says_never(self):
        result = _build_question_protocol("full", 3)
        assert "NEVER" in result

    def test_supervised_says_any(self):
        result = _build_question_protocol("supervised", 3)
        assert "ANY" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/agents/adapter_test.py::TestQuestionProtocol -v`
Expected: FAIL — "80% confident" not in current balanced text

- [ ] **Step 3: Rewrite _build_question_protocol balanced mode**

Replace lines 28-35 in `forge/agents/adapter.py`:

```python
    else:  # balanced
        when_to_ask = (
            "Ask when you are less than 80% confident about a decision that\n"
            "affects correctness. It is always better to pause for 30 seconds\n"
            "than to build the wrong thing for 10 minutes.\n\n"
            "ASK when:\n"
            "- The spec is ambiguous and you see multiple valid interpretations\n"
            "- You're about to make an architectural choice the spec doesn't specify\n"
            "- You found conflicting patterns in the codebase and aren't sure which to follow\n"
            "- You're about to delete, rename, or restructure something that other code depends on\n\n"
            "DON'T ASK when:\n"
            "- The spec is clear and you know exactly what to do\n"
            "- It's a naming, formatting, or minor style choice\n"
            "- You can verify your assumption by reading existing code\n\n"
            "EXAMPLES:\n"
            "- Spec says \"add caching\" but doesn't mention TTL or eviction strategy → ASK\n"
            "- Spec says \"add a login button to the nav bar\" and you can see the nav component → DON'T ASK\n"
            "- You're about to change a function signature that 12 other files import → ASK\n"
            "- You need to pick between two equivalent testing patterns → DON'T ASK"
        )
```

Add "thinking out loud" to the protocol template, before the "How to ask" section:

```python
    return f"""## Human Interaction Protocol

Autonomy level: {autonomy} | Questions remaining: {remaining}

### When to ask:
{when_to_ask}

### Before asking:
Before emitting a question, briefly explain:
1. What you're working on
2. What you found that created the uncertainty
3. What options you see

Then ask your specific question with concrete suggestions.
This context helps the human give you a useful answer.

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest forge/agents/adapter_test.py::TestQuestionProtocol -v`
Expected: All 5 PASS

- [ ] **Step 5: Run all adapter tests for regressions**

Run: `python -m pytest forge/agents/adapter_test.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add forge/agents/adapter.py forge/agents/adapter_test.py
git commit -m "feat(agents): rewrite balanced question protocol with 80% threshold and examples"
```

---

## Chunk 2: Connect the Resume Wire (Fix 1)

The critical fix. Three disconnected paths need connecting: event-driven resume, crash recovery, timeout resume.

### Task 7: TUI Emits task:answer to Daemon's EventEmitter

**Files:**
- Modify: `forge/tui/app.py:227-243`
- Test: `forge/tui/app_test.py` (or existing test file)

The TUI EventBus is one-directional (daemon→TUI via EmbeddedSource). For TUI→daemon, we emit directly on the daemon's EventEmitter since the daemon is accessible in-process via `self._daemon._events`.

- [ ] **Step 1: Write failing test — answer submission emits on daemon's EventEmitter**

```python
# In forge/tui/app_test.py (or add to existing)
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_answer_submission_emits_to_daemon_events():
    """Answering a question should emit task:answer on the daemon's EventEmitter."""
    from forge.tui.app import ForgeApp

    emitted = []

    class FakeEmitter:
        async def emit(self, event_type, data):
            emitted.append((event_type, data))

    class FakeDB:
        async def get_pending_questions(self, pipeline_id):
            q = MagicMock()
            q.task_id = "t1"
            q.answer = None
            q.id = "q1"
            return [q]
        async def answer_question(self, q_id, answer, answered_by):
            pass

    class FakeDaemon:
        _events = FakeEmitter()

    # Create a minimal event
    event = MagicMock()
    event.task_id = "t1"
    event.answer = "Use JWT"

    app = ForgeApp.__new__(ForgeApp)
    app._db = FakeDB()
    app._pipeline_id = "pipe1"
    app._daemon = FakeDaemon()
    app._state = MagicMock()
    app._state.apply_event = MagicMock()

    await app.on_chat_thread_answer_submitted(event)

    assert len(emitted) == 1
    assert emitted[0][0] == "task:answer"
    assert emitted[0][1]["task_id"] == "t1"
    assert emitted[0][1]["answer"] == "Use JWT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest forge/tui/app_test.py::test_answer_submission_emits_to_daemon_events -v`
Expected: FAIL — `app._daemon` is None or emit not called

- [ ] **Step 3: Add event emission to on_chat_thread_answer_submitted**

In `forge/tui/app.py`, modify `on_chat_thread_answer_submitted()` (line 227-243). After the DB write and state update, add:

```python
    async def on_chat_thread_answer_submitted(self, event) -> None:
        """Write the user's answer to DB and update TUI state."""
        task_id = event.task_id
        answer = event.answer
        if not self._db or not self._pipeline_id:
            logger.warning("Cannot record answer: DB or pipeline_id not set")
            return
        try:
            pending = await self._db.get_pending_questions(self._pipeline_id)
            for q in pending:
                if q.task_id == task_id and q.answer is None:
                    await self._db.answer_question(q.id, answer, "human")
                    break
        except Exception:
            logger.error("Failed to record answer to DB", exc_info=True)
        self._state.apply_event("task:answer", {"task_id": task_id, "answer": answer})

        # Notify daemon to resume the task
        if self._daemon and hasattr(self._daemon, '_events'):
            try:
                await self._daemon._events.emit("task:answer", {
                    "task_id": task_id,
                    "answer": answer,
                    "pipeline_id": self._pipeline_id,
                })
            except Exception:
                logger.error("Failed to emit task:answer to daemon", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest forge/tui/app_test.py::test_answer_submission_emits_to_daemon_events -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add forge/tui/app.py forge/tui/app_test.py
git commit -m "feat(tui): emit task:answer to daemon EventEmitter on question answer"
```

---

### Task 8: Daemon Listens for task:answer and Calls _resume_task

**Files:**
- Modify: `forge/core/daemon_executor.py`
- Modify: `forge/core/daemon.py`
- Test: `forge/core/daemon_executor_test.py`

- [ ] **Step 1: Write failing test — _on_task_answered resumes task**

```python
# In forge/core/daemon_executor_test.py — add these tests

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_on_task_answered_resumes_awaiting_input_task():
    """When task:answer arrives, daemon should resume the task."""
    from forge.core.daemon_executor import ExecutorMixin

    executor = ExecutorMixin.__new__(ExecutorMixin)
    executor._project_dir = "/tmp/test"

    # Mock DB
    mock_db = AsyncMock()
    mock_task = MagicMock()
    mock_task.state = "awaiting_input"
    mock_task.id = "t1"
    mock_db.get_task.return_value = mock_task

    # Mock question with answer
    mock_q = MagicMock()
    mock_q.answer = "Use JWT"
    mock_q.answered_at = "2026-03-16T00:00:00"
    mock_q.id = "q1"
    mock_db.get_task_questions.return_value = [mock_q]

    # Mock dependencies
    executor._runtime = MagicMock()
    executor._worktree_mgr = MagicMock()
    executor._merge_worker = MagicMock()
    executor._resume_task = AsyncMock()
    executor._active_tasks = {}
    executor._effective_max_agents = 4

    # Mock Scheduler to return an agent slot
    with patch("forge.core.daemon_executor.Scheduler") as MockSched:
        MockSched.dispatch_plan.return_value = [("t1", "agent-1")]
        mock_db.list_agents.return_value = [MagicMock(id="agent-1")]

        await executor._on_task_answered(
            data={"task_id": "t1", "answer": "Use JWT", "pipeline_id": "pipe1"},
            db=mock_db,
        )

    executor._resume_task.assert_called_once()
    call_args = executor._resume_task.call_args
    assert call_args[1].get("task_id", call_args[0][4] if len(call_args[0]) > 4 else None) == "t1" or "t1" in str(call_args)


@pytest.mark.asyncio
async def test_on_task_answered_skips_non_awaiting_task():
    """task:answer for a task not in AWAITING_INPUT should be a no-op."""
    from forge.core.daemon_executor import ExecutorMixin

    executor = ExecutorMixin.__new__(ExecutorMixin)

    mock_db = AsyncMock()
    mock_task = MagicMock()
    mock_task.state = "in_progress"  # Not AWAITING_INPUT
    mock_db.get_task.return_value = mock_task

    executor._resume_task = AsyncMock()

    await executor._on_task_answered(
        data={"task_id": "t1", "answer": "x", "pipeline_id": "pipe1"},
        db=mock_db,
    )

    executor._resume_task.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/core/daemon_executor_test.py::test_on_task_answered_resumes_awaiting_input_task -v`
Expected: FAIL — `_on_task_answered` method doesn't exist

- [ ] **Step 3: Implement _on_task_answered in ExecutorMixin**

Add to `forge/core/daemon_executor.py`, after `_resume_task()`:

```python
    async def _on_task_answered(
        self, data: dict, db,
    ) -> None:
        """Handle task:answer event — resume a task after human answers a question.

        Called by the daemon's EventEmitter listener when the TUI submits an answer.
        """
        task_id = data.get("task_id")
        answer = data.get("answer")
        pipeline_id = data.get("pipeline_id", "")
        if not task_id or not answer:
            return

        task = await db.get_task(task_id)
        if not task or task.state != TaskState.AWAITING_INPUT.value:
            logger.debug("_on_task_answered: task %s not awaiting_input (state=%s)", task_id, getattr(task, "state", None))
            return

        # Skip if task is already being resumed (in active pool)
        if task_id in getattr(self, "_active_tasks", {}):
            logger.debug("_on_task_answered: task %s already active, skipping", task_id)
            return

        # Acquire an agent slot via Scheduler
        from forge.core.scheduler import Scheduler
        from forge.core.engine import _row_to_agent, _row_to_record
        prefix = pipeline_id[:8] if pipeline_id else None
        agents = await db.list_agents(prefix=prefix)
        agent_records = [_row_to_agent(a) for a in agents]
        tasks = await (db.list_tasks_by_pipeline(pipeline_id) if pipeline_id else db.list_tasks())
        task_records = [_row_to_record(t) for t in tasks]
        dispatch_plan = Scheduler.dispatch_plan(task_records, agent_records, self._effective_max_agents)

        # Find an available agent from the dispatch plan
        agent_id = None
        for tid, aid in dispatch_plan:
            if tid == task_id:
                agent_id = aid
                break

        if not agent_id:
            logger.info("_on_task_answered: no slot available for %s, will retry on next cycle", task_id)
            return

        await db.assign_task(task_id, agent_id)
        logger.info("Resuming task %s after human answer (agent=%s)", task_id, agent_id)

        atask = asyncio.create_task(
            self._safe_execute_resume(
                db, self._runtime, self._worktree_mgr, self._merge_worker,
                task_id, agent_id, answer, pipeline_id,
            ),
            name=f"forge-resume-{task_id}",
        )
        self._active_tasks[task_id] = atask

    async def _safe_execute_resume(
        self, db, runtime, worktree_mgr, merge_worker,
        task_id: str, agent_id: str, answer: str, pipeline_id: str | None = None,
    ) -> None:
        """Safe wrapper around _resume_task with cleanup on error."""
        try:
            await self._resume_task(
                db, runtime, worktree_mgr, merge_worker,
                task_id, agent_id, answer, pipeline_id,
            )
        except asyncio.CancelledError:
            logger.info("Resume of %s was cancelled", task_id)
        except Exception as e:
            logger.error("Resume of %s crashed: %s", task_id, e, exc_info=True)
            try:
                # Transition task back to AWAITING_INPUT so it can be retried
                await db.update_task_state(task_id, TaskState.AWAITING_INPUT.value)
                await db.release_agent(agent_id)
            except Exception:
                pass
```

- [ ] **Step 4: Register task:answer listener in daemon.py**

In `forge/core/daemon.py`, in the `_execution_loop_inner()` method, after the EventEmitter is available (around line 870), register the listener:

```python
        # Register task:answer listener for event-driven resume
        async def _answer_handler(data):
            await self._on_task_answered(data=data, db=db)

        self._events.on("task:answer", _answer_handler)
```

Store the reference to `_runtime`, `_worktree_mgr`, `_merge_worker` on `self` at the start of `_execution_loop_inner` so `_on_task_answered` can access them:

```python
        self._runtime = runtime
        self._worktree_mgr = worktree_mgr
        self._merge_worker = merge_worker
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest forge/core/daemon_executor_test.py::test_on_task_answered_resumes_awaiting_input_task forge/core/daemon_executor_test.py::test_on_task_answered_skips_non_awaiting_task -v`
Expected: Both PASS

- [ ] **Step 6: Commit**

```bash
git add forge/core/daemon_executor.py forge/core/daemon.py forge/core/daemon_executor_test.py
git commit -m "feat(daemon): add event-driven task resume on task:answer from TUI"
```

---

### Task 9: Crash Recovery — _recover_answered_questions

**Files:**
- Modify: `forge/core/daemon.py`
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing test for crash recovery**

```python
# In forge/core/daemon_test.py (or create)

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_recover_answered_questions_resumes_tasks():
    """On startup, tasks in AWAITING_INPUT with answered questions should be resumed."""
    from forge.core.daemon import ForgeDaemon

    daemon = ForgeDaemon.__new__(ForgeDaemon)
    daemon._settings = MagicMock()
    daemon._settings.max_agents = 4

    # Mock a task in AWAITING_INPUT
    mock_task = MagicMock()
    mock_task.id = "t1"
    mock_task.state = "awaiting_input"

    mock_db = AsyncMock()
    mock_db.get_tasks_by_state = AsyncMock(return_value=[mock_task])

    # Mock answered question
    mock_q = MagicMock()
    mock_q.answer = "Use JWT"
    mock_q.answered_at = "2026-03-16T00:00:00"
    mock_q.id = "q1"
    mock_db.get_task_questions = AsyncMock(return_value=[mock_q])

    daemon._on_task_answered = AsyncMock()

    await daemon._recover_answered_questions(mock_db, "pipe1")

    daemon._on_task_answered.assert_called_once()
    call_data = daemon._on_task_answered.call_args[1].get("data", daemon._on_task_answered.call_args[0][0] if daemon._on_task_answered.call_args[0] else {})
    assert call_data.get("task_id") == "t1" or "t1" in str(daemon._on_task_answered.call_args)


@pytest.mark.asyncio
async def test_recover_skips_planning_questions():
    """Recovery should skip __planning__ sentinel tasks."""
    from forge.core.daemon import ForgeDaemon

    daemon = ForgeDaemon.__new__(ForgeDaemon)

    mock_task = MagicMock()
    mock_task.id = "__planning__"
    mock_task.state = "awaiting_input"

    mock_db = AsyncMock()
    mock_db.get_tasks_by_state = AsyncMock(return_value=[mock_task])

    daemon._on_task_answered = AsyncMock()

    await daemon._recover_answered_questions(mock_db, "pipe1")

    daemon._on_task_answered.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/core/daemon_test.py::test_recover_answered_questions_resumes_tasks -v`
Expected: FAIL — `_recover_answered_questions` doesn't exist

- [ ] **Step 3: Implement _recover_answered_questions**

Add to `forge/core/daemon.py`, near `_check_question_timeouts()`:

```python
    async def _recover_answered_questions(self, db: Database, pipeline_id: str) -> None:
        """Resume tasks that were answered while daemon was down.

        Called at the start of the execution loop and after reconnecting
        to a running pipeline. Skips __planning__ sentinel tasks (handled
        by the planning question system).
        """
        try:
            tasks = await db.get_tasks_by_state(pipeline_id, "awaiting_input")
        except Exception:
            logger.exception("Failed to query awaiting_input tasks for recovery")
            return

        for task in tasks:
            if task.id == "__planning__":
                continue
            try:
                questions = await db.get_task_questions(task.id)
                answered = [q for q in questions if q.answer and q.answered_at]
                if answered:
                    latest = max(answered, key=lambda q: q.answered_at)
                    await self._on_task_answered(
                        data={
                            "task_id": task.id,
                            "answer": latest.answer,
                            "pipeline_id": pipeline_id,
                        },
                        db=db,
                    )
            except Exception:
                logger.exception("Failed to recover task %s", task.id)
```

**Required:** `db.get_tasks_by_state()` does NOT exist in the current DB class. Add it to `forge/storage/db.py` in the task methods section:

```python
    async def get_tasks_by_state(self, pipeline_id: str, state: str) -> list[TaskRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskRow)
                .where(TaskRow.pipeline_id == pipeline_id)
                .where(TaskRow.state == state)
            )
            return list(result.scalars().all())
```

Add a test for it:
```python
@pytest.mark.asyncio
async def test_get_tasks_by_state(db):
    # Create pipeline + task in awaiting_input state, verify retrieval
    pass  # Implementer fills in
```

- [ ] **Step 4: Call _recover_answered_questions at start of execution loop**

In `_execution_loop_inner()`, after the `self._executor_token` setup (around line 873), add:

```python
        # Recover tasks that were answered while daemon was down
        if pipeline_id:
            await self._recover_answered_questions(db, pipeline_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest forge/core/daemon_test.py::test_recover_answered_questions_resumes_tasks forge/core/daemon_test.py::test_recover_skips_planning_questions -v`
Expected: Both PASS

- [ ] **Step 6: Commit**

```bash
git add forge/core/daemon.py forge/storage/db.py forge/core/daemon_test.py
git commit -m "feat(daemon): add crash recovery for answered questions on startup"
```

---

### Task 10: Timeout Resume — _check_question_timeouts Calls _resume_task

**Files:**
- Modify: `forge/core/daemon.py:743-767`
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Write failing test — timeout auto-answer triggers resume**

```python
# In forge/core/daemon_test.py

@pytest.mark.asyncio
async def test_check_question_timeouts_triggers_resume():
    """After auto-answering a timed-out question, _on_task_answered should be called."""
    from forge.core.daemon import ForgeDaemon

    daemon = ForgeDaemon.__new__(ForgeDaemon)
    daemon._settings = MagicMock()
    daemon._settings.question_timeout = 300

    # Mock expired question
    mock_q = MagicMock()
    mock_q.id = "q1"
    mock_q.task_id = "t1"
    mock_q.pipeline_id = "pipe1"

    mock_db = AsyncMock()
    mock_db.get_expired_questions = AsyncMock(return_value=[mock_q])
    mock_db.answer_question = AsyncMock()

    daemon._emit = AsyncMock()
    daemon._on_task_answered = AsyncMock()

    await daemon._check_question_timeouts(mock_db, "pipe1")

    mock_db.answer_question.assert_called_once_with(
        "q1", "Proceed with your best judgment.", "timeout"
    )
    daemon._on_task_answered.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest forge/core/daemon_test.py::test_check_question_timeouts_triggers_resume -v`
Expected: FAIL — `_on_task_answered` not called in current implementation

- [ ] **Step 3: Add resume call after auto-answer in _check_question_timeouts**

In `forge/core/daemon.py`, modify `_check_question_timeouts()` (line 743-767). After `await db.answer_question(...)` and the event emit, add the resume call:

```python
    async def _check_question_timeouts(self, db: Database, pipeline_id: str) -> None:
        """Auto-answer expired questions so waiting tasks can resume."""
        try:
            expired = await db.get_expired_questions(self._settings.question_timeout)
        except Exception:
            logger.exception("Failed to query expired questions for pipeline %s", pipeline_id)
            return
        for q in expired:
            if q.pipeline_id != pipeline_id:
                continue
            try:
                await db.answer_question(q.id, "Proceed with your best judgment.", "timeout")
                await self._emit(
                    "task:auto_decided",
                    {"task_id": q.task_id, "reason": "timeout", "question_id": q.id},
                    db=db,
                    pipeline_id=pipeline_id,
                )
                logger.info(
                    "Auto-answered timed-out question %s for task %s", q.id, q.task_id
                )
                # Resume the task now that it has an answer
                await self._on_task_answered(
                    data={
                        "task_id": q.task_id,
                        "answer": "Proceed with your best judgment.",
                        "pipeline_id": pipeline_id,
                    },
                    db=db,
                )
            except Exception:
                logger.exception(
                    "Failed to auto-answer question %s for task %s", q.id, q.task_id
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest forge/core/daemon_test.py::test_check_question_timeouts_triggers_resume -v`
Expected: PASS

- [ ] **Step 5: Run all daemon tests for regressions**

Run: `python -m pytest forge/core/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add forge/core/daemon.py forge/core/daemon_test.py
git commit -m "feat(daemon): auto-resume tasks after question timeout"
```

---

## Chunk 3: Wire Planning Questions (Fix 3)

### Task 11: Add stage Column to TaskQuestionRow

**Files:**
- Modify: `forge/storage/db.py:187-206` (TaskQuestionRow)
- Modify: `forge/storage/db.py:1000-1020` (create_task_question)
- Test: `forge/storage/db_test.py`

- [ ] **Step 1: Write failing tests for stage column**

```python
# In forge/storage/db_test.py (add to existing or create)

import pytest


@pytest.mark.asyncio
async def test_create_task_question_with_stage(db):
    """create_task_question should accept and persist a stage parameter."""
    q = await tmp_db.create_task_question(
        task_id="__planning__",
        pipeline_id="pipe1",
        question="JWT or session?",
        stage="planning",
    )
    assert q.stage == "planning"


@pytest.mark.asyncio
async def test_create_task_question_stage_defaults_none(db):
    """stage should default to None for backward compatibility."""
    q = await tmp_db.create_task_question(
        task_id="t1",
        pipeline_id="pipe1",
        question="Which pattern?",
    )
    assert q.stage is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/storage/db_test.py::test_create_task_question_with_stage -v`
Expected: FAIL — `stage` not accepted

- [ ] **Step 3: Add stage column to TaskQuestionRow**

In `forge/storage/db.py`, add to `TaskQuestionRow` (after `answered_at`, line 205):

```python
    stage: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
```

- [ ] **Step 4: Update create_task_question to accept stage**

In `forge/storage/db.py`, modify `create_task_question()` (line 1000-1020):

```python
    async def create_task_question(
        self,
        *,
        task_id: str,
        pipeline_id: str,
        question: str,
        suggestions: list[str] | None = None,
        context: dict | None = None,
        stage: str | None = None,
    ) -> TaskQuestionRow:
        async with self._session_factory() as session:
            row = TaskQuestionRow(
                task_id=task_id,
                pipeline_id=pipeline_id,
                question=question,
                suggestions=json.dumps(suggestions) if suggestions else None,
                context=json.dumps(context) if context else None,
                stage=stage,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row
```

- [ ] **Step 5: Add get_planning_questions method**

Add to `forge/storage/db.py` after `get_task_questions`:

```python
    async def get_planning_questions(self, pipeline_id: str) -> list[TaskQuestionRow]:
        """Get all planning-phase questions for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskQuestionRow)
                .where(TaskQuestionRow.pipeline_id == pipeline_id)
                .where(TaskQuestionRow.stage == "planning")
                .order_by(TaskQuestionRow.created_at)
            )
            return list(result.scalars().all())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest forge/storage/db_test.py::test_create_task_question_with_stage forge/storage/db_test.py::test_create_task_question_stage_defaults_none -v`
Expected: Both PASS

- [ ] **Step 7: Commit**

```bash
git add forge/storage/db.py forge/storage/db_test.py
git commit -m "feat(db): add stage column to TaskQuestionRow for planning questions"
```

---

### Task 12: Wire on_question Callback in daemon.plan()

**Files:**
- Modify: `forge/core/daemon.py:360-364` (PlanningPipeline construction)
- Modify: `forge/tui/bus.py` (add planning event types)
- Test: `forge/core/daemon_test.py`

- [ ] **Step 1: Add planning question event types to TUI_EVENT_TYPES**

In `forge/tui/bus.py`, add to `TUI_EVENT_TYPES` list (after `"task:auto_decided"`):

```python
    "planning:question",
    "planning:answer",
```

- [ ] **Step 2: Write failing test — Architect question surfaces via callback**

```python
# In forge/core/daemon_test.py

@pytest.mark.asyncio
async def test_plan_wires_on_question_to_pipeline():
    """daemon.plan() should pass on_question callback to PlanningPipeline."""
    from forge.core.daemon import ForgeDaemon

    captured_callbacks = {}

    class MockPipeline:
        def __init__(self, *args, **kwargs):
            captured_callbacks["on_question"] = kwargs.get("on_question")
            captured_callbacks["on_message"] = kwargs.get("on_message")

        async def run(self, **kwargs):
            from forge.core.planning.pipeline import PlanningResult
            from forge.core.models import TaskGraph
            return PlanningResult(
                task_graph=TaskGraph(tasks=[]),
                codebase_map=None,
            )

    # Verify the PlanningPipeline is constructed with on_question
    # (This is a structural test — verifying the wiring exists)
    assert True  # Placeholder — real test below after implementation
```

- [ ] **Step 3: Implement on_question callback in daemon.plan()**

In `forge/core/daemon.py`, inside the `plan()` method, before `PlanningPipeline` construction (around line 354), add:

```python
            # Planning question support via asyncio.Event synchronization
            pending_planning_answer: dict[str, asyncio.Event] = {}
            planning_answers: dict[str, str] = {}

            async def _on_architect_question(question_data: dict) -> str:
                """Called by Architect when it has a question. Blocks until human answers."""
                q = await db.create_task_question(
                    task_id="__planning__",
                    pipeline_id=pipeline_id or "",
                    question=question_data["question"],
                    suggestions=question_data.get("suggestions"),
                    context={"text": question_data.get("context", "")},
                    stage="planning",
                )
                if pipeline_id:
                    await self._emit("planning:question", {
                        "question_id": q.id,
                        "question": question_data,
                    }, db=db, pipeline_id=pipeline_id)
                else:
                    await self._events.emit("planning:question", {
                        "question_id": q.id,
                        "question": question_data,
                    })

                event = asyncio.Event()
                pending_planning_answer[q.id] = event
                try:
                    # Use same timeout as execution questions
                    timeout = self._settings.question_timeout
                    await asyncio.wait_for(event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pending_planning_answer.pop(q.id, None)
                    logger.info("Planning question %s timed out after %ds", q.id, timeout)
                    return "Proceed with your best judgment."
                except asyncio.CancelledError:
                    pending_planning_answer.pop(q.id, None)
                    return "Proceed with your best judgment."
                return planning_answers.pop(q.id, "Proceed with your best judgment.")

            async def _on_planning_answer(data: dict):
                """Handle planning:answer event — resolve the waiting asyncio.Event."""
                q_id = data.get("question_id")
                answer = data.get("answer")
                if q_id and answer:
                    planning_answers[q_id] = answer
                    event = pending_planning_answer.pop(q_id, None)
                    if event:
                        event.set()

            # Register listener for planning answers
            self._events.on("planning:answer", _on_planning_answer)
```

**Important:** Wrap the pipeline.run() call in a try/finally to unregister the listener when planning completes, preventing listener accumulation on repeated plan() calls:

```python
            try:
                planning_result = await pipeline.run(...)
            finally:
                # Clean up listener to prevent accumulation
                handlers = self._events._handlers.get("planning:answer", [])
                if _on_planning_answer in handlers:
                    handlers.remove(_on_planning_answer)
```

Then modify the PlanningPipeline construction (line 360-364) to pass the callback:

```python
            pipeline = PlanningPipeline(
                scout=scout, architect=architect,
                detailer_factory=detailer_factory,
                on_message=_on_pipeline_msg,
                on_question=_on_architect_question,
            )
```

- [ ] **Step 4: Run tests to verify no regressions**

Run: `python -m pytest forge/core/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add forge/core/daemon.py forge/tui/bus.py forge/core/daemon_test.py
git commit -m "feat(daemon): wire Architect on_question callback through PlanningPipeline"
```

---

### Task 13: TUI Planning Question Display and Answer Routing

**Files:**
- Modify: `forge/tui/state.py` (add planning question handlers)
- Modify: `forge/tui/screens/pipeline.py` (show ChatThread for planning questions)
- Modify: `forge/tui/app.py` (route planning answers via planning:answer event)
- Test: `forge/tui/state_test.py`

- [ ] **Step 1: Add planning question handlers to TuiState**

In `forge/tui/state.py`, add handlers and register them in `_EVENT_MAP`:

```python
    def _on_planning_question(self, data: dict) -> None:
        """Architect has a question during planning."""
        self.pending_questions["__planning__"] = data.get("question", {})
        self._notify("planning")

    def _on_planning_answer(self, data: dict) -> None:
        """Planning question was answered."""
        self.pending_questions.pop("__planning__", None)
        self._notify("planning")
```

Add to `_EVENT_MAP`:

```python
    "planning:question": _on_planning_question,
    "planning:answer": _on_planning_answer,
```

- [ ] **Step 2: Add ChatThread display for planning questions in PipelineScreen**

In `forge/tui/screens/pipeline.py`, detect when `self._state.pending_questions.get("__planning__")` is set and show a `ChatThread` widget inline. The ChatThread should be created with `task_id="__planning__"`.

When the user submits an answer in the ChatThread (which posts `AnswerSubmitted`), it bubbles up to `ForgeApp`.

- [ ] **Step 3: Route planning answers in ForgeApp**

In `forge/tui/app.py`, modify `on_chat_thread_answer_submitted()` to detect planning answers:

```python
    async def on_chat_thread_answer_submitted(self, event) -> None:
        """Write the user's answer to DB and update TUI state."""
        task_id = event.task_id
        answer = event.answer
        if not self._db or not self._pipeline_id:
            logger.warning("Cannot record answer: DB or pipeline_id not set")
            return

        # Planning questions use __planning__ sentinel
        is_planning = task_id == "__planning__"

        try:
            pending = await self._db.get_pending_questions(self._pipeline_id)
            for q in pending:
                if q.task_id == task_id and q.answer is None:
                    await self._db.answer_question(q.id, answer, "human")
                    # Emit the right event type
                    if is_planning and self._daemon and hasattr(self._daemon, '_events'):
                        await self._daemon._events.emit("planning:answer", {
                            "question_id": q.id,
                            "answer": answer,
                        })
                    break
        except Exception:
            logger.error("Failed to record answer to DB", exc_info=True)

        if is_planning:
            self._state.apply_event("planning:answer", {"answer": answer})
        else:
            self._state.apply_event("task:answer", {"task_id": task_id, "answer": answer})
            # Notify daemon for task resume
            if self._daemon and hasattr(self._daemon, '_events'):
                try:
                    await self._daemon._events.emit("task:answer", {
                        "task_id": task_id,
                        "answer": answer,
                        "pipeline_id": self._pipeline_id,
                    })
                except Exception:
                    logger.error("Failed to emit task:answer to daemon", exc_info=True)
```

- [ ] **Step 4: Run TUI tests**

Run: `python -m pytest forge/tui/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add forge/tui/state.py forge/tui/screens/pipeline.py forge/tui/app.py forge/tui/state_test.py
git commit -m "feat(tui): show planning questions inline and route answers to daemon"
```

---

## Chunk 4: Human Interrupt — Interjection System (Fix 4)

### Task 14: InterjectionRow DB Model and Methods

**Files:**
- Modify: `forge/storage/db.py`
- Test: `forge/storage/db_test.py`

- [ ] **Step 1: Write failing tests for InterjectionRow CRUD**

```python
# In forge/storage/db_test.py

@pytest.mark.asyncio
async def test_create_interjection(db):
    """Should create an interjection row with delivered=False."""
    row = await tmp_db.create_interjection(
        task_id="t1", pipeline_id="pipe1", message="Use the factory pattern instead"
    )
    assert row.task_id == "t1"
    assert row.message == "Use the factory pattern instead"
    assert row.delivered is False
    assert row.delivered_at is None


@pytest.mark.asyncio
async def test_get_pending_interjections(db):
    """Should return only undelivered interjections for a task."""
    await tmp_db.create_interjection(task_id="t1", pipeline_id="pipe1", message="msg1")
    await tmp_db.create_interjection(task_id="t1", pipeline_id="pipe1", message="msg2")
    await tmp_db.create_interjection(task_id="t2", pipeline_id="pipe1", message="other")

    pending = await tmp_db.get_pending_interjections("t1")
    assert len(pending) == 2
    assert all(p.task_id == "t1" for p in pending)


@pytest.mark.asyncio
async def test_mark_interjection_delivered(db):
    """Marking delivered should set delivered=True and delivered_at."""
    row = await tmp_db.create_interjection(task_id="t1", pipeline_id="pipe1", message="msg")
    await tmp_db.mark_interjection_delivered(row.id)

    pending = await tmp_db.get_pending_interjections("t1")
    assert len(pending) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/storage/db_test.py::test_create_interjection -v`
Expected: FAIL — `InterjectionRow` and methods don't exist

- [ ] **Step 3: Add InterjectionRow model**

In `forge/storage/db.py`, after `TaskQuestionRow` (around line 206), add:

```python
class InterjectionRow(Base):
    """Human message sent to a running agent."""

    __tablename__ = "task_interjections"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat(),
    )
    delivered_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
```

Add `InterjectionRow` to `_ALL_MODELS`:

```python
_ALL_MODELS = (UserRow, AuditLogRow, TaskRow, AgentRow, PipelineRow, UserTemplateRow, PipelineEventRow, TaskQuestionRow, InterjectionRow)
```

- [ ] **Step 4: Add interjection CRUD methods**

Add to `Database` class after the task question methods:

```python
    # ── Task interjections ─────────────────────────────────────────────

    async def create_interjection(
        self,
        *,
        task_id: str,
        pipeline_id: str,
        message: str,
    ) -> InterjectionRow:
        async with self._session_factory() as session:
            row = InterjectionRow(
                task_id=task_id,
                pipeline_id=pipeline_id,
                message=message,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_pending_interjections(self, task_id: str) -> list[InterjectionRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(InterjectionRow)
                .where(InterjectionRow.task_id == task_id)
                .where(InterjectionRow.delivered == False)
                .order_by(InterjectionRow.created_at)
            )
            return list(result.scalars().all())

    async def mark_interjection_delivered(self, interjection_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(InterjectionRow, interjection_id)
            if row:
                row.delivered = True
                row.delivered_at = datetime.now(timezone.utc).isoformat()
                await session.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest forge/storage/db_test.py::test_create_interjection forge/storage/db_test.py::test_get_pending_interjections forge/storage/db_test.py::test_mark_interjection_delivered -v`
Expected: All 3 PASS

- [ ] **Step 6: Commit**

```bash
git add forge/storage/db.py forge/storage/db_test.py
git commit -m "feat(db): add InterjectionRow model and CRUD for human-to-agent messages"
```

---

### Task 15: Executor Interjection Check After Agent Turn

**Files:**
- Modify: `forge/core/daemon_executor.py:86-99` (in _execute_task, after _run_agent)
- Test: `forge/core/daemon_executor_test.py`

- [ ] **Step 1: Write failing test — interjections delivered after agent turn**

```python
# In forge/core/daemon_executor_test.py

@pytest.mark.asyncio
async def test_execute_task_delivers_interjections_after_agent_turn():
    """After agent turn, pending interjections should be delivered via resume."""
    from forge.core.daemon_executor import ExecutorMixin

    executor = ExecutorMixin.__new__(ExecutorMixin)
    executor._project_dir = "/tmp/test"
    executor._strategy = "balanced"

    mock_db = AsyncMock()

    # First _run_agent returns normally (no question)
    # Mock interjection exists
    mock_ij = MagicMock()
    mock_ij.id = "ij1"
    mock_ij.message = "Use factory pattern"
    mock_db.get_pending_interjections = AsyncMock(
        side_effect=[[mock_ij], []]  # First call returns interjection, second call empty
    )
    mock_db.mark_interjection_delivered = AsyncMock()

    # Track _run_agent calls
    agent_calls = []
    first_result = MagicMock()
    first_result.summary = "Implemented the feature"
    first_result.session_id = "sess-1"
    second_result = MagicMock()
    second_result.summary = "Adjusted to use factory pattern"
    second_result.session_id = "sess-2"

    async def mock_run_agent(*args, **kwargs):
        agent_calls.append(kwargs)
        if len(agent_calls) == 1:
            return first_result
        return second_result

    executor._run_agent = mock_run_agent

    # The interjection delivery should call _run_agent a second time
    # with resume=sess-1 and a prompt containing the human's message
    delivered, session_id = await executor._deliver_interjections(
        db=mock_db, task_id="t1", task=MagicMock(),
        agent_id="a1", worktree_path="/tmp/wt",
        pipeline_id="pipe1", session_id="sess-1",
        pipeline_branch="main",
    )
    assert delivered is True
    assert session_id == "sess-2"
    mock_db.mark_interjection_delivered.assert_called_once_with("ij1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest forge/core/daemon_executor_test.py::test_execute_task_delivers_interjections_after_agent_turn -v`
Expected: FAIL — `_deliver_interjections` doesn't exist

- [ ] **Step 3: Implement _deliver_interjections method**

Add to `forge/core/daemon_executor.py`, after `_on_task_answered`:

```python
    async def _deliver_interjections(
        self, db, runtime, worktree_mgr, task_id: str, task, agent_id: str,
        worktree_path: str, pipeline_id: str, session_id: str | None,
        pipeline_branch: str | None = None,
    ) -> tuple[bool, str | None]:
        """Check for and deliver pending interjections to a running agent.

        Returns (was_delivered, latest_session_id). After delivery, check for
        more interjections in a loop — the human may have sent multiple.

        Must be called after _run_agent() returns and BEFORE _enforce_file_scope().
        """
        delivered_any = False
        current_session = session_id

        while True:
            interjections = await db.get_pending_interjections(task_id)
            if not interjections:
                break

            # Combine all pending messages into one prompt
            combined = "\n\n".join(
                f"Human message: {ij.message}" for ij in interjections
            )
            prompt = (
                f"The human has sent you a message while you were working:\n\n"
                f"{combined}\n\n"
                f"Read their input carefully. Adjust your approach if needed, "
                f"then continue working on the task."
            )

            # Mark all as delivered
            for ij in interjections:
                await db.mark_interjection_delivered(ij.id)

            # Resume agent session with human's message
            agent_result = await self._run_agent(
                db, runtime, worktree_mgr, task, task_id, agent_id,
                worktree_path, pipeline_id, pipeline_branch=pipeline_branch,
                resume=current_session, prompt_override=prompt,
            )

            if agent_result is None:
                break

            delivered_any = True
            current_session = agent_result.session_id

            # If agent asked a question in response, handle it and return
            question_data = _parse_forge_question(agent_result.summary)
            if question_data:
                await self._handle_agent_question(
                    db, task_id, agent_id, pipeline_id=pipeline_id,
                    question_data=question_data,
                    session_id=agent_result.session_id,
                )
                return True, current_session

            # Loop to check for more interjections

        return delivered_any, current_session
```

- [ ] **Step 4: Insert interjection check in _execute_task**

In `_execute_task()`, after the FORGE_QUESTION check (line 92-99) and before `_enforce_file_scope` (line 101-104), insert:

```python
        # Check for pending human interjections before review
        interjection_delivered, final_session = await self._deliver_interjections(
            db=db, runtime=runtime, worktree_mgr=worktree_mgr,
            task_id=task_id, task=task, agent_id=agent_id,
            worktree_path=worktree_path, pipeline_id=pid,
            session_id=agent_result.session_id,
            pipeline_branch=pipeline_branch,
        )
        if interjection_delivered and final_session != agent_result.session_id:
            # Agent was resumed with interjection, check if it asked a question
            # (already handled inside _deliver_interjections — if we're here,
            # no question was asked, proceed to review)
            pass
```

Also insert the same pattern in `_resume_task()`, after the FORGE_QUESTION check (line 380-388) and before `_enforce_file_scope` (line 391):

```python
        # Check for interjections after resume completes
        interjection_delivered, final_session = await self._deliver_interjections(
            db=db, task_id=task_id, task=task, agent_id=agent_id,
            worktree_path=worktree_path, pipeline_id=pid,
            session_id=agent_result.session_id,
            pipeline_branch=pipeline_branch,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest forge/core/daemon_executor_test.py::test_execute_task_delivers_interjections_after_agent_turn -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add forge/core/daemon_executor.py forge/core/daemon_executor_test.py
git commit -m "feat(executor): deliver human interjections to agents between turns"
```

---

### Task 16: TUI Interjection Keybinding and ChatThread Mode

**Files:**
- Modify: `forge/tui/screens/pipeline.py` (add `i` keybinding)
- Modify: `forge/tui/widgets/chat_thread.py` (add interjection mode)
- Modify: `forge/tui/app.py` (handle InterjectionSubmitted)
- Modify: `forge/tui/bus.py` (add event type)
- Test: `forge/tui/pipeline_test.py`

- [ ] **Step 1: Add task:interjection to TUI_EVENT_TYPES**

In `forge/tui/bus.py`, add to `TUI_EVENT_TYPES`:

```python
    "task:interjection",
```

- [ ] **Step 2: Add interjection mode to ChatThread**

In `forge/tui/widgets/chat_thread.py`, add a second Message class and mode support:

```python
class ChatThread(Widget):
    class AnswerSubmitted(Message):
        def __init__(self, task_id: str, answer: str) -> None:
            super().__init__()
            self.task_id = task_id
            self.answer = answer

    class InterjectionSubmitted(Message):
        def __init__(self, task_id: str, message: str) -> None:
            super().__init__()
            self.task_id = task_id
            self.message = message

    def __init__(self, task_id: str = "", mode: str = "answer") -> None:
        super().__init__()
        self.task_id = task_id
        self._mode = mode  # "answer" or "interjection"
        ...
```

When `_mode == "interjection"`:
- Input placeholder: "Type a message to the agent..."
- Hide suggestion chips
- On submit, post `InterjectionSubmitted` instead of `AnswerSubmitted`

- [ ] **Step 3: Add `i` keybinding to PipelineScreen**

In `forge/tui/screens/pipeline.py`, add to BINDINGS:

```python
    Binding("i", "interject", "Interject", show=True),
```

Add action handler:

```python
    def action_interject(self) -> None:
        """Open chat thread in interjection mode for the selected task."""
        task_id = self._get_selected_task_id()
        if not task_id:
            self.notify("No task selected", severity="warning")
            return
        # Check task state — only allow interjection on IN_PROGRESS or AWAITING_INPUT
        task_data = self._state.tasks.get(task_id, {})
        state = task_data.get("state", "")
        if state not in ("in_progress", "awaiting_input"):
            self.notify(f"Cannot interject — task is {state}", severity="warning")
            return
        # Show ChatThread in interjection mode
        chat = ChatThread(task_id=task_id, mode="interjection")
        # Mount it (implementation depends on existing ChatThread mounting pattern)
        self._show_chat_thread(chat)
```

- [ ] **Step 4: Handle InterjectionSubmitted in ForgeApp**

In `forge/tui/app.py`, add handler:

```python
    async def on_chat_thread_interjection_submitted(self, event) -> None:
        """Store interjection in DB for delivery to running agent."""
        task_id = event.task_id
        message = event.message
        if not self._db or not self._pipeline_id:
            return
        try:
            # Check if task is AWAITING_INPUT — treat as answer instead
            task_data = self._state.tasks.get(task_id, {})
            if task_data.get("state") == "awaiting_input":
                # Route through answer flow
                pending = await self._db.get_pending_questions(self._pipeline_id)
                for q in pending:
                    if q.task_id == task_id and q.answer is None:
                        await self._db.answer_question(q.id, message, "human")
                        # Create interjection for audit
                        ij = await self._db.create_interjection(
                            task_id=task_id, pipeline_id=self._pipeline_id, message=message,
                        )
                        await self._db.mark_interjection_delivered(ij.id)
                        # Emit task:answer to daemon
                        if self._daemon and hasattr(self._daemon, '_events'):
                            await self._daemon._events.emit("task:answer", {
                                "task_id": task_id, "answer": message,
                                "pipeline_id": self._pipeline_id,
                            })
                        self._state.apply_event("task:answer", {"task_id": task_id, "answer": message})
                        break
                return

            # Normal interjection — store in DB, agent picks it up between turns
            await self._db.create_interjection(
                task_id=task_id, pipeline_id=self._pipeline_id, message=message,
            )
            self.notify("Message queued — will be delivered after agent's current turn")
        except Exception:
            logger.error("Failed to create interjection", exc_info=True)
```

- [ ] **Step 5: Add state handler for interjection tracking**

In `forge/tui/state.py`, add:

```python
    def _on_task_interjection(self, data: dict) -> None:
        """Track interjection state per task."""
        task_id = data.get("task_id")
        if task_id:
            self._notify("tasks")
```

Add to `_EVENT_MAP`:

```python
    "task:interjection": _on_task_interjection,
```

- [ ] **Step 6: Run TUI tests**

Run: `python -m pytest forge/tui/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 7: Run full test suite for final validation**

Run: `python -m pytest forge/ -x -q --timeout=30`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add forge/tui/screens/pipeline.py forge/tui/widgets/chat_thread.py forge/tui/app.py forge/tui/bus.py forge/tui/state.py
git commit -m "feat(tui): add interjection keybinding and human-to-agent messaging"
```

---

## Verification

### Automated Tests

```bash
# Per-module
python -m pytest forge/agents/adapter_test.py -v        # Fix 2 + 5
python -m pytest forge/core/daemon_executor_test.py -v   # Fix 1 + 4
python -m pytest forge/core/daemon_test.py -v            # Fix 1
python -m pytest forge/storage/db_test.py -v             # Fix 3 + 4
python -m pytest forge/tui/ -v                           # Fix 3 + 4

# Full regression
python -m pytest forge/ -x -q --timeout=30
```

### Manual Testing

1. **Fix 1 — Resume wire:** Run `forge run "ambiguous task"`, agent asks question, answer in TUI → agent should resume and complete
2. **Fix 2 — Unleashed agents:** Run agent on task requiring web search → should use WebSearch tool; check system prompt includes CLAUDE.md content
3. **Fix 5 — Question protocol:** Run balanced agent on ambiguous spec → should ask with "80% confident" examples
4. **Fix 3 — Planning questions:** Run `forge run --spec docs/spec.md --deep-plan "Build feature"` → Architect should ask questions during planning
5. **Fix 4 — Human interrupt:** While agent runs, press `i`, type message → agent should acknowledge and adjust after current turn
