"""Multi-provider architecture — public API.

Re-exports all core types, catalog entries, restrictions, and safety auditor.
"""

from forge.providers.base import (
    AuditVerdict,
    CatalogEntry,
    EventKind,
    ExecutionHandle,
    ExecutionMode,
    MCPServerConfig,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    ProviderHealthStatus,
    ProviderProtocol,
    ProviderResult,
    ResumeState,
    SafetyBoundary,
    SafetyViolation,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.catalog import (
    CLAUDE_TOOL_MAP,
    CODEX_TOOL_MAP,
    FORGE_MODEL_CATALOG,
    CoreTool,
    handle_unknown_tool,
    validate_model_for_stage,
)
from forge.providers.restrictions import (
    AGENT_DENIED_OPERATIONS,
    AGENT_TOOL_POLICY,
    CONTRACT_TOOL_POLICY,
    PLANNER_TOOL_POLICY,
    REVIEWER_TOOL_POLICY,
)
from forge.providers.safety_auditor import SafetyAuditor

__all__ = [
    # base types
    "AuditVerdict",
    "CatalogEntry",
    "EventKind",
    "ExecutionHandle",
    "ExecutionMode",
    "MCPServerConfig",
    "ModelSpec",
    "OutputContract",
    "ProviderEvent",
    "ProviderHealthStatus",
    "ProviderProtocol",
    "ProviderResult",
    "ResumeState",
    "SafetyBoundary",
    "SafetyViolation",
    "ToolPolicy",
    "WorkspaceRoots",
    # catalog
    "CLAUDE_TOOL_MAP",
    "CODEX_TOOL_MAP",
    "CoreTool",
    "FORGE_MODEL_CATALOG",
    "handle_unknown_tool",
    "validate_model_for_stage",
    # restrictions
    "AGENT_DENIED_OPERATIONS",
    "AGENT_TOOL_POLICY",
    "CONTRACT_TOOL_POLICY",
    "PLANNER_TOOL_POLICY",
    "REVIEWER_TOOL_POLICY",
    # safety
    "SafetyAuditor",
]
