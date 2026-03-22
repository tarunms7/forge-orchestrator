"""Tests for ClaudePlannerLLM prompt building and conventions injection."""

import pytest
from unittest.mock import AsyncMock, patch

from forge.core.claude_planner import PLANNER_SYSTEM_PROMPT, ClaudePlannerLLM, _extract_json
from forge.core.errors import SdkCallError


class TestPlannerSystemPrompt:
    """Verify the system prompt includes the conventions schema."""

    def test_prompt_includes_conventions_block(self):
        assert '"conventions"' in PLANNER_SYSTEM_PROMPT

    def test_prompt_includes_conventions_keys(self):
        for key in (
            "styling",
            "state_management",
            "component_patterns",
            "naming",
            "testing",
            "imports",
            "error_handling",
            "other",
        ):
            assert f'"{key}"' in PLANNER_SYSTEM_PROMPT

    def test_prompt_includes_conventions_instruction(self):
        assert "conventions will be forwarded to every coding agent" in PLANNER_SYSTEM_PROMPT

    def test_prompt_still_includes_tasks_schema(self):
        assert '"tasks"' in PLANNER_SYSTEM_PROMPT
        assert '"depends_on"' in PLANNER_SYSTEM_PROMPT
        assert '"complexity"' in PLANNER_SYSTEM_PROMPT


class TestBuildPrompt:
    """Test _build_prompt with and without conventions.md."""

    def test_basic_prompt(self):
        planner = ClaudePlannerLLM(cwd="/nonexistent")
        prompt = planner._build_prompt("add auth", "ctx", None)
        assert "User request: add auth" in prompt
        assert "Project context:\nctx" in prompt
        assert "Existing project conventions" not in prompt

    def test_prompt_with_feedback(self):
        planner = ClaudePlannerLLM(cwd="/nonexistent")
        prompt = planner._build_prompt("add auth", "", "fix deps")
        assert "Previous attempt feedback:\nfix deps" in prompt

    def test_prompt_injects_conventions_file(self, tmp_path):
        conventions = tmp_path / ".forge" / "conventions.md"
        conventions.parent.mkdir(parents=True)
        conventions.write_text("Use pytest for all tests.\nPrefer dataclasses.")

        planner = ClaudePlannerLLM(cwd=str(tmp_path))
        prompt = planner._build_prompt("add feature", "ctx", None)
        assert "Existing project conventions (from .forge/conventions.md):" in prompt
        assert "Use pytest for all tests." in prompt
        assert "Prefer dataclasses." in prompt

    def test_prompt_skips_empty_conventions_file(self, tmp_path):
        conventions = tmp_path / ".forge" / "conventions.md"
        conventions.parent.mkdir(parents=True)
        conventions.write_text("   \n  ")

        planner = ClaudePlannerLLM(cwd=str(tmp_path))
        prompt = planner._build_prompt("add feature", "ctx", None)
        assert "Existing project conventions" not in prompt

    def test_prompt_skips_missing_conventions_file(self, tmp_path):
        planner = ClaudePlannerLLM(cwd=str(tmp_path))
        prompt = planner._build_prompt("add feature", "ctx", None)
        assert "Existing project conventions" not in prompt

    def test_prompt_skips_missing_forge_dir(self, tmp_path):
        # .forge dir doesn't exist at all
        planner = ClaudePlannerLLM(cwd=str(tmp_path))
        prompt = planner._build_prompt("add feature", "", None)
        assert "Existing project conventions" not in prompt

    def test_conventions_appears_between_context_and_feedback(self, tmp_path):
        conventions = tmp_path / ".forge" / "conventions.md"
        conventions.parent.mkdir(parents=True)
        conventions.write_text("PEP8 style")

        planner = ClaudePlannerLLM(cwd=str(tmp_path))
        prompt = planner._build_prompt("task", "context here", "feedback here")

        ctx_pos = prompt.index("Project context:")
        conv_pos = prompt.index("Existing project conventions")
        fb_pos = prompt.index("Previous attempt feedback:")
        assert ctx_pos < conv_pos < fb_pos

    def test_default_cwd_used_when_none(self):
        """When cwd is None, conventions path defaults to ./.forge/conventions.md."""
        planner = ClaudePlannerLLM(cwd=None)
        # Should not raise even if file doesn't exist
        prompt = planner._build_prompt("task", "", None)
        assert "Respond with ONLY the TaskGraph JSON." in prompt


class TestExtractJson:
    """Tests for _extract_json string-aware brace matching."""

    def test_plain_json(self):
        assert _extract_json('{"tasks": []}') == '{"tasks": []}'

    def test_json_in_markdown_fence(self):
        text = '```json\n{"tasks": []}\n```'
        assert _extract_json(text) == '{"tasks": []}'

    def test_trailing_text_stripped(self):
        """Text after the JSON object should be excluded."""
        text = '{"tasks": []} some trailing commentary'
        assert _extract_json(text) == '{"tasks": []}'

    def test_braces_in_strings_not_confused(self):
        """Braces inside JSON string values should not break extraction."""
        text = '{"description": "handle {edge} cases", "id": 1} extra text here'
        result = _extract_json(text)
        assert result == '{"description": "handle {edge} cases", "id": 1}'

    def test_escaped_quotes_in_strings(self):
        """Escaped quotes should not break string tracking."""
        text = r'{"msg": "say \"hello\"", "n": 1} trailing'
        result = _extract_json(text)
        assert '"msg"' in result
        assert result.endswith("}")
        assert "trailing" not in result

    def test_nested_objects(self):
        text = '{"a": {"b": {"c": 1}}} after'
        assert _extract_json(text) == '{"a": {"b": {"c": 1}}}'

    def test_no_json(self):
        assert _extract_json("no json here") == "no json here"

    def test_fallback_unbalanced(self):
        """Unbalanced braces should fall back to rfind."""
        text = '{"key": "value"'
        result = _extract_json(text)
        # No closing brace at depth 0, falls back — still returns something
        assert result == text


class TestGeneratePlanSdkError:
    """Test that SDK exceptions are re-raised as SdkCallError."""

    @pytest.mark.asyncio
    async def test_sdk_exception_raises_sdk_call_error(self):
        """When sdk_query raises, generate_plan should raise SdkCallError."""
        planner = ClaudePlannerLLM(cwd="/nonexistent")
        with patch("forge.core.claude_planner.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.side_effect = RuntimeError("rate limit exceeded")
            with pytest.raises(SdkCallError, match="SDK call failed"):
                await planner.generate_plan("build auth", "context", None)

    @pytest.mark.asyncio
    async def test_sdk_call_error_wraps_original(self):
        """SdkCallError should chain the original exception."""
        planner = ClaudePlannerLLM(cwd="/nonexistent")
        original = ConnectionError("network down")
        with patch("forge.core.claude_planner.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.side_effect = original
            with pytest.raises(SdkCallError) as exc_info:
                await planner.generate_plan("build auth", "context", None)
            assert exc_info.value.original_error is original
            assert exc_info.value.__cause__ is original
