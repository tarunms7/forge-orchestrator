"""Tests for ClaudePlannerLLM prompt building and conventions injection."""



from forge.core.claude_planner import PLANNER_SYSTEM_PROMPT, ClaudePlannerLLM


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
