"""Tests for forge/providers/catalog.py — model catalog and tool mappings."""

from __future__ import annotations

from forge.providers.base import AuditVerdict, CatalogEntry
from forge.providers.catalog import (
    CLAUDE_TOOL_MAP,
    CODEX_TOOL_MAP,
    FORGE_MODEL_CATALOG,
    CoreTool,
    handle_unknown_tool,
    validate_model_for_stage,
)

# ---------------------------------------------------------------------------
# Catalog completeness
# ---------------------------------------------------------------------------


class TestCatalogCompleteness:
    def test_has_seven_models(self) -> None:
        assert len(FORGE_MODEL_CATALOG) == 7

    def test_three_claude_models(self) -> None:
        claude = [e for e in FORGE_MODEL_CATALOG if e.provider == "claude"]
        assert len(claude) == 3
        aliases = {e.alias for e in claude}
        assert aliases == {"sonnet", "opus", "haiku"}

    def test_four_openai_models(self) -> None:
        openai = [e for e in FORGE_MODEL_CATALOG if e.provider == "openai"]
        assert len(openai) == 4
        aliases = {e.alias for e in openai}
        assert aliases == {"gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "o3"}

    def test_all_entries_are_catalog_entry(self) -> None:
        for entry in FORGE_MODEL_CATALOG:
            assert isinstance(entry, CatalogEntry)

    def test_all_have_cost_key(self) -> None:
        for entry in FORGE_MODEL_CATALOG:
            assert entry.cost_key, f"{entry.alias} missing cost_key"

    def test_all_have_canonical_id(self) -> None:
        for entry in FORGE_MODEL_CATALOG:
            assert entry.canonical_id, f"{entry.alias} missing canonical_id"

    def test_primary_models(self) -> None:
        primary = [e for e in FORGE_MODEL_CATALOG if e.tier == "primary"]
        assert len(primary) == 2
        aliases = {e.alias for e in primary}
        assert aliases == {"sonnet", "opus"}

    def test_experimental_models(self) -> None:
        exp = [e for e in FORGE_MODEL_CATALOG if e.tier == "experimental"]
        aliases = {e.alias for e in exp}
        assert "gpt-5.4-nano" in aliases
        assert "o3" in aliases


# ---------------------------------------------------------------------------
# validate_model_for_stage
# ---------------------------------------------------------------------------


def _find_entry(alias: str) -> CatalogEntry:
    for e in FORGE_MODEL_CATALOG:
        if e.alias == alias:
            return e
    raise ValueError(f"No entry for {alias}")


class TestValidateModelForStage:
    def test_sonnet_validated_for_agent(self) -> None:
        issues = validate_model_for_stage(_find_entry("sonnet"), "agent")
        assert issues == []

    def test_sonnet_validated_for_planner(self) -> None:
        issues = validate_model_for_stage(_find_entry("sonnet"), "planner")
        assert issues == []

    def test_haiku_not_validated_for_planner(self) -> None:
        issues = validate_model_for_stage(_find_entry("haiku"), "planner")
        assert any("WARNING" in i for i in issues)

    def test_o3_blocked_for_agent(self) -> None:
        """o3 lacks can_run_shell and can_edit_files — hard block for agent."""
        issues = validate_model_for_stage(_find_entry("o3"), "agent")
        blocked = [i for i in issues if "BLOCKED" in i]
        assert len(blocked) >= 1

    def test_o3_validated_for_planner(self) -> None:
        issues = validate_model_for_stage(_find_entry("o3"), "planner")
        # No hard blocks for planner (no shell/edit requirements)
        blocked = [i for i in issues if "BLOCKED" in i]
        assert blocked == []

    def test_gpt54_not_validated_for_planner(self) -> None:
        issues = validate_model_for_stage(_find_entry("gpt-5.4"), "planner")
        assert any("WARNING" in i for i in issues)

    def test_empty_issues_for_valid_stage(self) -> None:
        issues = validate_model_for_stage(_find_entry("opus"), "planner")
        assert issues == []


# ---------------------------------------------------------------------------
# Tool mappings
# ---------------------------------------------------------------------------


class TestToolMappings:
    def test_claude_map_completeness(self) -> None:
        expected = {"Bash", "Read", "Write", "Edit", "Glob", "Grep"}
        assert set(CLAUDE_TOOL_MAP.keys()) == expected

    def test_codex_map_completeness(self) -> None:
        expected = {
            "command_execution",
            "file_read",
            "file_write",
            "file_change",
            "glob",
            "grep",
        }
        assert set(CODEX_TOOL_MAP.keys()) == expected

    def test_claude_bash_maps_to_bash(self) -> None:
        assert CLAUDE_TOOL_MAP["Bash"] == CoreTool.BASH

    def test_codex_command_execution_maps_to_bash(self) -> None:
        assert CODEX_TOOL_MAP["command_execution"] == CoreTool.BASH

    def test_all_values_are_core_tool(self) -> None:
        for v in CLAUDE_TOOL_MAP.values():
            assert isinstance(v, CoreTool)
        for v in CODEX_TOOL_MAP.values():
            assert isinstance(v, CoreTool)


# ---------------------------------------------------------------------------
# handle_unknown_tool
# ---------------------------------------------------------------------------


class TestHandleUnknownTool:
    def test_primary_fails_closed(self) -> None:
        entry = _find_entry("sonnet")
        assert entry.tier == "primary"
        verdict = handle_unknown_tool("WeirdTool", entry)
        assert verdict == AuditVerdict.ABORT

    def test_supported_fails_closed(self) -> None:
        entry = _find_entry("haiku")
        assert entry.tier == "supported"
        verdict = handle_unknown_tool("WeirdTool", entry)
        assert verdict == AuditVerdict.ABORT

    def test_experimental_fails_open(self) -> None:
        entry = _find_entry("gpt-5.4-nano")
        assert entry.tier == "experimental"
        verdict = handle_unknown_tool("WeirdTool", entry)
        assert verdict == AuditVerdict.WARN
