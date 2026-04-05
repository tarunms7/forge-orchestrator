"""Curated model catalog and tool mappings for multi-provider support.

Contains the canonical list of supported models, their capabilities,
tool name normalization maps, and stage validation logic.
"""

from __future__ import annotations

from enum import Enum

from forge.providers.base import AuditVerdict, CatalogEntry


# ---------------------------------------------------------------------------
# CoreTool enum — normalized tool identifiers
# ---------------------------------------------------------------------------


class CoreTool(str, Enum):
    """Normalized Forge tool identifiers across providers."""

    BASH = "bash"
    READ = "read"
    WRITE = "write"
    EDIT = "edit"
    GLOB = "glob"
    GREP = "grep"
    MCP_TOOL = "mcp_tool"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Tool name mappings
# ---------------------------------------------------------------------------

CLAUDE_TOOL_MAP: dict[str, CoreTool] = {
    "Bash": CoreTool.BASH,
    "Read": CoreTool.READ,
    "Write": CoreTool.WRITE,
    "Edit": CoreTool.EDIT,
    "Glob": CoreTool.GLOB,
    "Grep": CoreTool.GREP,
}

CODEX_TOOL_MAP: dict[str, CoreTool] = {
    "command_execution": CoreTool.BASH,
    "file_read": CoreTool.READ,
    "file_write": CoreTool.WRITE,
    "file_change": CoreTool.EDIT,
    "glob": CoreTool.GLOB,
    "grep": CoreTool.GREP,
}


# ---------------------------------------------------------------------------
# Model catalog — 3 Claude + 4 OpenAI models
# ---------------------------------------------------------------------------

FORGE_MODEL_CATALOG: list[CatalogEntry] = [
    # ---- Claude models (claude-code-sdk) ----
    CatalogEntry(
        provider="claude",
        alias="sonnet",
        canonical_id="claude-sonnet-4-20250514",
        backend="claude-code-sdk",
        tier="primary",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=True,
        max_context_tokens=200_000,
        supports_structured_output=False,
        supports_reasoning=True,
        cost_key="claude:sonnet",
        validated_stages=frozenset(
            ["planner", "contract_builder", "agent", "reviewer", "ci_fix"]
        ),
    ),
    CatalogEntry(
        provider="claude",
        alias="opus",
        canonical_id="claude-opus-4-20250514",
        backend="claude-code-sdk",
        tier="primary",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=True,
        max_context_tokens=200_000,
        supports_structured_output=False,
        supports_reasoning=True,
        cost_key="claude:opus",
        validated_stages=frozenset(
            ["planner", "contract_builder", "agent", "reviewer", "ci_fix"]
        ),
    ),
    CatalogEntry(
        provider="claude",
        alias="haiku",
        canonical_id="claude-haiku-4-20250414",
        backend="claude-code-sdk",
        tier="supported",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=True,
        max_context_tokens=200_000,
        supports_structured_output=False,
        supports_reasoning=False,
        cost_key="claude:haiku",
        validated_stages=frozenset(["agent", "reviewer", "ci_fix"]),
    ),
    # ---- OpenAI models (codex-sdk) ----
    CatalogEntry(
        provider="openai",
        alias="gpt-5.4",
        canonical_id="gpt-5.4-0414",
        backend="codex-sdk",
        tier="supported",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=False,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=False,
        max_context_tokens=128_000,
        supports_structured_output=True,
        supports_reasoning=False,
        cost_key="openai:gpt-5.4",
        validated_stages=frozenset(["agent", "ci_fix"]),
    ),
    CatalogEntry(
        provider="openai",
        alias="gpt-5.4-mini",
        canonical_id="gpt-5.4-mini-0414",
        backend="codex-sdk",
        tier="supported",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=False,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=False,
        max_context_tokens=128_000,
        supports_structured_output=True,
        supports_reasoning=False,
        cost_key="openai:gpt-5.4-mini",
        validated_stages=frozenset(["agent", "ci_fix"]),
    ),
    CatalogEntry(
        provider="openai",
        alias="gpt-5.4-nano",
        canonical_id="gpt-5.4-nano-0414",
        backend="codex-sdk",
        tier="experimental",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=False,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=False,
        max_context_tokens=128_000,
        supports_structured_output=True,
        supports_reasoning=False,
        cost_key="openai:gpt-5.4-nano",
        validated_stages=frozenset(["agent"]),
    ),
    # ---- OpenAI reasoning model (openai-agents-sdk) ----
    CatalogEntry(
        provider="openai",
        alias="o3",
        canonical_id="o3-2025-04-16",
        backend="openai-agents-sdk",
        tier="experimental",
        can_use_tools=True,
        can_stream=True,
        can_resume_session=False,
        can_run_shell=False,
        can_edit_files=False,
        supports_mcp_servers=False,
        max_context_tokens=200_000,
        supports_structured_output=True,
        supports_reasoning=True,
        cost_key="openai:o3",
        validated_stages=frozenset(["planner", "reviewer"]),
    ),
]


# ---------------------------------------------------------------------------
# Stage validation
# ---------------------------------------------------------------------------

# Hard capability requirements per stage
_STAGE_REQUIREMENTS: dict[str, dict[str, bool]] = {
    "agent": {"can_run_shell": True, "can_edit_files": True},
    "ci_fix": {"can_run_shell": True, "can_edit_files": True},
}


def validate_model_for_stage(entry: CatalogEntry, stage: str) -> list[str]:
    """Validate whether a model is suitable for a given pipeline stage.

    Returns a list of warning/error strings. Empty list means the model
    is fully validated for the stage.

    Hard blocks (prefixed with 'BLOCKED:') indicate missing capabilities.
    Soft warnings indicate the model hasn't been validated for the stage.
    """
    issues: list[str] = []

    # Check hard capability requirements
    requirements = _STAGE_REQUIREMENTS.get(stage, {})
    for attr, required_val in requirements.items():
        actual = getattr(entry, attr, None)
        if actual != required_val:
            issues.append(
                f"BLOCKED: {entry.alias} lacks {attr} required for {stage}"
            )

    # Check validated stages (soft warning)
    if stage not in entry.validated_stages:
        issues.append(
            f"WARNING: {entry.alias} is not validated for {stage} stage"
        )

    return issues


# ---------------------------------------------------------------------------
# Unknown tool handling
# ---------------------------------------------------------------------------


def handle_unknown_tool(tool_name: str, catalog_entry: CatalogEntry) -> AuditVerdict:
    """Determine how to handle an unknown tool based on model tier.

    Primary/supported models fail closed (ABORT) for unknown tools.
    Experimental models fail open (WARN) to allow exploration.
    """
    if catalog_entry.tier in ("primary", "supported"):
        return AuditVerdict.ABORT
    # experimental — fail open
    return AuditVerdict.WARN
