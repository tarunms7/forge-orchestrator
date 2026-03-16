import pytest
import re

from forge.core.daemon import _should_use_deep_planning


class TestShouldUseDeepPlanning:
    def test_with_spec(self):
        assert _should_use_deep_planning(
            planning_mode="auto", spec_path="/tmp/spec.md",
            user_input="Build it", total_files=50,
        ) is True

    def test_large_codebase(self):
        assert _should_use_deep_planning(
            planning_mode="auto", spec_path=None,
            user_input="Fix a bug", total_files=250,
        ) is True

    def test_medium_codebase(self):
        """Projects with >100 files trigger deep planning."""
        assert _should_use_deep_planning(
            planning_mode="auto", spec_path=None,
            user_input="Add a new feature", total_files=120,
        ) is True

    def test_small_task(self):
        assert _should_use_deep_planning(
            planning_mode="auto", spec_path=None,
            user_input="Fix the typo in README", total_files=50,
        ) is False

    def test_bullet_list_input(self):
        assert _should_use_deep_planning(
            planning_mode="auto", spec_path=None,
            user_input="- Add user auth\n- Add RBAC\n- Add session management",
            total_files=50,
        ) is True

    def test_long_input(self):
        """Input with >100 words triggers deep planning."""
        long_input = " ".join(["word"] * 120)
        assert _should_use_deep_planning(
            planning_mode="auto", spec_path=None,
            user_input=long_input, total_files=50,
        ) is True

    def test_force_deep(self):
        assert _should_use_deep_planning(
            planning_mode="deep", spec_path=None,
            user_input="Fix typo", total_files=10,
        ) is True

    def test_force_simple(self):
        assert _should_use_deep_planning(
            planning_mode="simple", spec_path=None,
            user_input="Build an entire SaaS platform with user auth, billing, and analytics",
            total_files=500,
        ) is False

    def test_structured_input_headers(self):
        assert _should_use_deep_planning(
            planning_mode="auto", spec_path=None,
            user_input="## Auth\nAdd JWT auth\n## Database\nAdd PostgreSQL",
            total_files=50,
        ) is True

    def test_structured_input_numbered_list(self):
        assert _should_use_deep_planning(
            planning_mode="auto", spec_path=None,
            user_input="1. Add user auth with JWT\n2. Add RBAC\n3. Add session management",
            total_files=50,
        ) is True
