import json
from unittest.mock import AsyncMock, patch

from forge.agents.adapter import (
    AGENT_ALLOWED_TOOLS,
    AGENT_DISALLOWED_TOOLS,
    AgentAdapter,
    AgentResult,
    ClaudeAdapter,
    _build_conventions_block,
    _build_dependency_context,
    _build_question_protocol,
    _load_claude_md,
)


# --- AgentResult tests ---


def test_agent_result_fields():
    result = AgentResult(
        success=True,
        files_changed=["a.py", "b.py"],
        summary="Added user model",
        cost_usd=0.05,
    )
    assert result.success is True
    assert len(result.files_changed) == 2
    assert result.cost_usd == 0.05


def test_agent_result_failure():
    result = AgentResult(
        success=False,
        files_changed=[],
        summary="Could not parse requirements",
        error="ValueError: missing field",
    )
    assert result.success is False
    assert result.error is not None


def test_claude_adapter_is_agent_adapter():
    adapter = ClaudeAdapter()
    assert isinstance(adapter, AgentAdapter)


def test_adapter_has_run_method():
    adapter = ClaudeAdapter()
    assert callable(getattr(adapter, "run", None))


# --- _build_conventions_block tests ---


def test_conventions_block_both_none():
    result = _build_conventions_block(None, None)
    assert result == ""


def test_conventions_block_both_empty():
    result = _build_conventions_block("", "")
    assert result == ""


def test_conventions_block_only_md():
    md = "## Code Style\n\nUse 4-space indentation."
    result = _build_conventions_block(None, md)
    assert "## Project Conventions" in result
    assert "Use 4-space indentation" in result


def test_conventions_block_only_json():
    data = {"Naming": "Use snake_case for Python", "Testing": "100% coverage required"}
    result = _build_conventions_block(json.dumps(data), None)
    assert "## Project Conventions" in result
    assert "**Naming**" in result
    assert "snake_case" in result
    assert "**Testing**" in result


def test_conventions_block_both_with_overlap():
    """User conventions for 'Code Style' should suppress planner 'code style' entry."""
    md = "## Code Style\n\nUse black formatter."
    data = {"code style": "Use ruff formatter", "Testing": "pytest only"}
    result = _build_conventions_block(json.dumps(data), md)
    assert "## Project Conventions" in result
    # User convention wins
    assert "Use black formatter" in result
    # Overlapping planner entry is excluded
    assert "Use ruff formatter" not in result
    # Non-overlapping planner entry is included
    assert "**Testing**" in result
    assert "pytest only" in result


def test_conventions_block_overlap_via_bold_pattern():
    """User md with **Code Style** should also suppress planner 'code style'."""
    md = "**Code Style**: Use black formatter."
    data = {"code style": "Use ruff formatter", "Docs": "Always add docstrings"}
    result = _build_conventions_block(json.dumps(data), md)
    assert "Use black formatter" in result
    assert "Use ruff formatter" not in result
    assert "**Docs**" in result


def test_conventions_block_json_parse_error():
    result = _build_conventions_block("not valid json {{{", None)
    assert result == ""


def test_conventions_block_json_list_format():
    data = [{"Naming": "snake_case"}, {"Testing": "pytest"}]
    result = _build_conventions_block(json.dumps(data), None)
    assert "**Naming**" in result
    assert "**Testing**" in result


def test_conventions_block_json_string_list():
    data = ["Use type hints everywhere", "No global state"]
    result = _build_conventions_block(json.dumps(data), None)
    assert "Use type hints everywhere" in result
    assert "No global state" in result


# --- _build_dependency_context tests ---


def test_dependency_context_empty_list():
    assert _build_dependency_context([]) == ""


def test_dependency_context_none():
    assert _build_dependency_context(None) == ""


def test_dependency_context_single_dep():
    deps = [
        {
            "task_id": "task-1",
            "title": "Add user model",
            "implementation_summary": "Created User SQLAlchemy model with email field",
            "files_changed": ["models/user.py", "migrations/001.py"],
        }
    ]
    result = _build_dependency_context(deps)
    assert "## Completed Dependencies" in result
    assert "### Task: Add user model (task-1)" in result
    assert "Created User SQLAlchemy model" in result
    assert "- models/user.py" in result
    assert "- migrations/001.py" in result


def test_dependency_context_multiple_deps():
    deps = [
        {
            "task_id": "task-1",
            "title": "Add user model",
            "implementation_summary": "Created User model",
            "files_changed": ["models/user.py"],
        },
        {
            "task_id": "task-2",
            "title": "Add auth endpoints",
            "implementation_summary": "Created login/register routes",
            "files_changed": ["routes/auth.py", "routes/__init__.py"],
        },
    ]
    result = _build_dependency_context(deps)
    assert "### Task: Add user model (task-1)" in result
    assert "### Task: Add auth endpoints (task-2)" in result
    assert "- routes/auth.py" in result


def test_dependency_context_missing_summary():
    deps = [
        {
            "task_id": "task-3",
            "title": "Setup CI",
            "implementation_summary": None,
            "files_changed": [],
        }
    ]
    result = _build_dependency_context(deps)
    assert "No summary available" in result
    assert "- (none)" in result


# --- _build_options tests ---


def test_adapter_build_options_sets_cwd():
    adapter = ClaudeAdapter()
    options = adapter._build_options("/tmp/test-worktree", [])
    assert options.cwd == "/tmp/test-worktree"


def test_adapter_system_prompt_includes_directory_boundary():
    adapter = ClaudeAdapter()
    options = adapter._build_options("/tmp/test-worktree", [])
    assert "/tmp/test-worktree" in options.system_prompt


def test_adapter_system_prompt_includes_extra_dirs():
    adapter = ClaudeAdapter()
    options = adapter._build_options("/tmp/test-worktree", ["/tmp/shared-lib"])
    assert "/tmp/shared-lib" in options.system_prompt


def test_build_options_includes_conventions():
    adapter = ClaudeAdapter()
    conventions_md = "## Code Style\n\nUse black."
    options = adapter._build_options(
        "/tmp/wt", [],
        conventions_md=conventions_md,
    )
    assert "## Project Conventions" in options.system_prompt
    assert "Use black" in options.system_prompt


def test_build_options_includes_dependency_context():
    adapter = ClaudeAdapter()
    deps = [
        {
            "task_id": "task-1",
            "title": "Add models",
            "implementation_summary": "Created models",
            "files_changed": ["models.py"],
        }
    ]
    options = adapter._build_options(
        "/tmp/wt", [],
        completed_deps=deps,
    )
    assert "## Completed Dependencies" in options.system_prompt
    assert "Add models" in options.system_prompt


def test_build_options_conventions_rule_in_prompt():
    adapter = ClaudeAdapter()
    options = adapter._build_options("/tmp/wt", [])
    assert "existing code style" in options.system_prompt


def test_build_options_no_conventions_or_deps():
    """When no conventions or deps, prompt should still be valid."""
    adapter = ClaudeAdapter()
    options = adapter._build_options("/tmp/wt", [])
    # Should not error and should contain Boundaries section
    assert "## Boundaries" in options.system_prompt


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


# --- ClaudeAdapter.run tests ---


async def test_claude_adapter_passes_on_message_to_sdk_query():
    """ClaudeAdapter.run() should forward on_message callback to sdk_query."""
    callback = AsyncMock()

    mock_result = AsyncMock()
    mock_result.result = "Done"
    mock_result.total_cost_usd = 0.01
    mock_result.is_error = False

    with patch("forge.agents.adapter.sdk_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_result
        with patch("forge.agents.adapter._get_changed_files", return_value=["a.py"]):
            adapter = ClaudeAdapter()
            result = await adapter.run(
                task_prompt="test",
                worktree_path="/tmp/test",
                allowed_files=["a.py"],
                timeout_seconds=60,
                on_message=callback,
            )

    # Verify on_message was passed through to sdk_query
    mock_query.assert_called_once()
    call_kwargs = mock_query.call_args[1]
    assert call_kwargs["on_message"] is callback
    assert result.success is True


async def test_claude_adapter_run_passes_conventions_and_deps():
    """ClaudeAdapter.run() should forward conventions and deps to _build_options."""
    mock_result = AsyncMock()
    mock_result.result = "Done"
    mock_result.total_cost_usd = 0.01
    mock_result.is_error = False
    mock_result.result_text = "Done"
    mock_result.cost_usd = 0.01
    mock_result.input_tokens = 100
    mock_result.output_tokens = 50

    conventions_md = "## Lint\n\nUse ruff."
    conventions_json = json.dumps({"Testing": "pytest"})
    deps = [{"task_id": "t1", "title": "Setup", "implementation_summary": "Init", "files_changed": ["a.py"]}]

    with patch("forge.agents.adapter.sdk_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_result
        with patch("forge.agents.adapter._get_changed_files", return_value=[]):
            adapter = ClaudeAdapter()
            result = await adapter.run(
                task_prompt="test",
                worktree_path="/tmp/test",
                allowed_files=["a.py"],
                timeout_seconds=60,
                conventions_json=conventions_json,
                conventions_md=conventions_md,
                completed_deps=deps,
            )

    assert result.success is True
    # Verify system prompt contains conventions and deps
    call_kwargs = mock_query.call_args[1]
    system_prompt = call_kwargs["options"].system_prompt
    assert "## Project Conventions" in system_prompt
    assert "Use ruff" in system_prompt
    assert "**Testing**" in system_prompt
    assert "## Completed Dependencies" in system_prompt
    assert "Setup" in system_prompt


def test_build_options_autonomy_settings():
    """_build_options should forward autonomy and questions_remaining to system prompt."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        "/tmp/wt", [],
        autonomy="full",
        questions_remaining=0,
    )
    assert "Autonomy level: full" in options.system_prompt
    assert "Questions remaining: 0" in options.system_prompt
    assert "NEVER ask questions" in options.system_prompt


def test_build_options_supervised_autonomy():
    """_build_options with supervised autonomy should include supervised protocol."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        "/tmp/wt", [],
        autonomy="supervised",
        questions_remaining=5,
    )
    assert "Autonomy level: supervised" in options.system_prompt
    assert "Questions remaining: 5" in options.system_prompt


async def test_claude_adapter_run_forwards_autonomy():
    """ClaudeAdapter.run() should forward autonomy params to _build_options."""
    mock_result = AsyncMock()
    mock_result.result_text = "Done"
    mock_result.cost_usd = 0.01
    mock_result.is_error = False
    mock_result.input_tokens = 100
    mock_result.output_tokens = 50

    with patch("forge.agents.adapter.sdk_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_result
        with patch("forge.agents.adapter._get_changed_files", return_value=[]):
            adapter = ClaudeAdapter()
            result = await adapter.run(
                task_prompt="test",
                worktree_path="/tmp/test",
                allowed_files=["a.py"],
                timeout_seconds=60,
                autonomy="supervised",
                questions_remaining=5,
            )

    assert result.success is True
    call_kwargs = mock_query.call_args[1]
    system_prompt = call_kwargs["options"].system_prompt
    assert "Autonomy level: supervised" in system_prompt
    assert "Questions remaining: 5" in system_prompt


async def test_claude_adapter_error_includes_cost():
    """ClaudeAdapter.run() should include cost_usd in error AgentResult."""
    mock_result = AsyncMock()
    mock_result.result_text = "Something went wrong"
    mock_result.cost_usd = 0.07
    mock_result.is_error = True
    mock_result.input_tokens = 500
    mock_result.output_tokens = 200

    with patch("forge.agents.adapter.sdk_query", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = mock_result
        with patch("forge.agents.adapter._get_changed_files", return_value=[]):
            adapter = ClaudeAdapter()
            result = await adapter.run(
                task_prompt="test",
                worktree_path="/tmp/test",
                allowed_files=["a.py"],
                timeout_seconds=60,
            )

    assert result.success is False
    assert result.error == "Something went wrong"
    assert result.cost_usd == 0.07
    assert result.input_tokens == 500
    assert result.output_tokens == 200


# --- _load_claude_md tests ---


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


# --- CLAUDE.md injection into system prompt tests ---


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


# --- _build_question_protocol tests ---


class TestQuestionProtocol:
    def test_balanced_encourages_questions(self):
        result = _build_question_protocol("balanced", 3)
        assert "SHOULD ask questions" in result

    def test_balanced_contains_examples(self):
        result = _build_question_protocol("balanced", 3)
        assert "caching" in result.lower()
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


# --- Turn budget and git access tests ---


def test_system_prompt_includes_turn_budget(tmp_path):
    """System prompt should include turn budget awareness."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path=str(tmp_path),
        allowed_dirs=[],
        agent_max_turns=30,
    )
    assert "30 turns" in options.system_prompt
    assert "turn 25" in options.system_prompt  # wrap_up_turn = 30 - 5
    assert options.max_turns == 30


def test_system_prompt_turn_budget_defaults(tmp_path):
    """Default max_turns=75 should produce wrap_up_turn=70."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path=str(tmp_path),
        allowed_dirs=[],
    )
    assert "75 turns" in options.system_prompt
    assert "turn 70" in options.system_prompt
    assert options.max_turns == 75


def test_system_prompt_allows_git_read_commands(tmp_path):
    """System prompt should allow git diff/status/log."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path=str(tmp_path),
        allowed_dirs=[],
    )
    assert "git diff" in options.system_prompt
    assert "git status" in options.system_prompt
    assert "git log" in options.system_prompt


def test_system_prompt_blocks_git_write_commands(tmp_path):
    """System prompt should block destructive git commands."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path=str(tmp_path),
        allowed_dirs=[],
    )
    assert "git push" in options.system_prompt
    assert "git rebase" in options.system_prompt


def test_system_prompt_no_working_effectively_section(tmp_path):
    """Old 'Working Effectively' section should be gone."""
    adapter = ClaudeAdapter()
    options = adapter._build_options(
        worktree_path=str(tmp_path),
        allowed_dirs=[],
    )
    assert "Working Effectively" not in options.system_prompt
    assert "F821" not in options.system_prompt


# --- Worktree permission tests ---


class TestAgentPermissions:
    """Permissions are passed via SDK options, not written to disk."""

    def test_allowed_tools_contains_git(self):
        """Git commands are in the allowed tools list."""
        from forge.agents.adapter import AGENT_ALLOWED_TOOLS
        assert "Bash(git *)" in AGENT_ALLOWED_TOOLS

    def test_allowed_tools_contains_file_ops(self):
        """rm, mv, cp, mkdir are allowed for refactoring."""
        from forge.agents.adapter import AGENT_ALLOWED_TOOLS
        assert "Bash(rm *)" in AGENT_ALLOWED_TOOLS
        assert "Bash(mv *)" in AGENT_ALLOWED_TOOLS
        assert "Bash(mkdir *)" in AGENT_ALLOWED_TOOLS

    def test_allowed_tools_contains_build_tools(self):
        """Common build/test tools are allowed."""
        from forge.agents.adapter import AGENT_ALLOWED_TOOLS
        assert "Bash(pytest *)" in AGENT_ALLOWED_TOOLS
        assert "Bash(npm *)" in AGENT_ALLOWED_TOOLS
        assert "Bash(make *)" in AGENT_ALLOWED_TOOLS
        assert "Bash(cargo *)" in AGENT_ALLOWED_TOOLS

    def test_disallowed_tools_blocks_network(self):
        """Network commands are blocked."""
        from forge.agents.adapter import AGENT_DISALLOWED_TOOLS
        assert "Bash(curl *)" in AGENT_DISALLOWED_TOOLS
        assert "Bash(wget *)" in AGENT_DISALLOWED_TOOLS
        assert "Bash(ssh *)" in AGENT_DISALLOWED_TOOLS

    def test_disallowed_tools_blocks_privilege_escalation(self):
        """sudo and friends are blocked."""
        from forge.agents.adapter import AGENT_DISALLOWED_TOOLS
        assert "Bash(sudo *)" in AGENT_DISALLOWED_TOOLS
        assert "Bash(chmod *)" in AGENT_DISALLOWED_TOOLS

    def test_no_settings_file_written(self, tmp_path):
        """Permissions are NOT written to disk — no file pollution."""
        # Build options and verify no file is created
        adapter = ClaudeAdapter()
        options = adapter._build_options(
            str(tmp_path), [],
            model="sonnet",
            project_context="",
        )
        assert not (tmp_path / ".claude" / "settings.json").exists()
        assert options.allowed_tools == list(AGENT_ALLOWED_TOOLS)
        assert options.disallowed_tools == list(AGENT_DISALLOWED_TOOLS)

    def test_build_options_passes_permissions(self):
        """_build_options includes allowed_tools and disallowed_tools."""
        adapter = ClaudeAdapter()
        options = adapter._build_options(
            "/tmp/test", [],
            model="sonnet",
            project_context="",
        )
        assert "Bash(git *)" in options.allowed_tools
        assert "Bash(curl *)" in options.disallowed_tools
