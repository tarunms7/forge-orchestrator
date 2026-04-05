"""Core data types for the multi-provider architecture.

All shared types used across providers, the catalog, cost registry,
safety auditor, and pipeline stages.
"""

from __future__ import annotations

import abc
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# ModelSpec
# ---------------------------------------------------------------------------

# Default provider for bare aliases (no 'provider:' prefix)
_DEFAULT_PROVIDER = "claude"

# Known bare aliases -> provider mapping
_BARE_ALIAS_PROVIDERS: dict[str, str] = {
    "sonnet": "claude",
    "opus": "claude",
    "haiku": "claude",
    "gpt-5.4": "openai",
    "gpt-5.4-mini": "openai",
    "gpt-5.4-nano": "openai",
    "o3": "openai",
}


@dataclass(frozen=True)
class ModelSpec:
    """Identifies a provider and model. Used as key for catalog lookups."""

    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, raw: str) -> ModelSpec:
        """Parse a model specification string.

        Accepts:
          - 'sonnet'          -> ModelSpec('claude', 'sonnet')
          - 'claude:opus'     -> ModelSpec('claude', 'opus')
          - 'openai:gpt-5.4'  -> ModelSpec('openai', 'gpt-5.4')
        """
        raw = raw.strip()
        if not raw:
            raise ValueError("Empty model spec")

        if ":" in raw:
            provider, model = raw.split(":", 1)
            if not provider or not model:
                raise ValueError(f"Invalid model spec: {raw!r}")
            return cls(provider=provider, model=model)

        # Bare alias — look up known providers, default to claude
        provider = _BARE_ALIAS_PROVIDERS.get(raw, _DEFAULT_PROVIDER)
        return cls(provider=provider, model=raw)


# ---------------------------------------------------------------------------
# CatalogEntry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogEntry:
    """Describes a model's capabilities, backend, tier, and validated stages."""

    provider: str
    alias: str
    canonical_id: str
    backend: str  # 'claude-code-sdk', 'codex-sdk', or 'openai-agents-sdk'
    tier: Literal["primary", "supported", "experimental"]

    # Capability flags
    can_use_tools: bool
    can_stream: bool
    can_resume_session: bool
    can_run_shell: bool
    can_edit_files: bool
    supports_mcp_servers: bool
    max_context_tokens: int
    supports_structured_output: bool
    supports_reasoning: bool

    cost_key: str
    validated_stages: frozenset[str]

    @property
    def spec(self) -> ModelSpec:
        return ModelSpec(provider=self.provider, model=self.alias)

    @property
    def resolved_cost_key(self) -> str:
        return self.cost_key or str(self.spec)


# ---------------------------------------------------------------------------
# EventKind / ProviderEvent
# ---------------------------------------------------------------------------


class EventKind(str, Enum):
    """Event types emitted during provider execution."""

    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    USAGE = "usage"
    STATUS = "status"


@dataclass
class ProviderEvent:
    """Normalized event emitted during provider execution."""

    kind: EventKind
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_input: str | None = None
    tool_output: str | None = None
    is_tool_error: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    token_count: int | None = None
    status: str | None = None
    raw: Any = None


# ---------------------------------------------------------------------------
# ResumeState
# ---------------------------------------------------------------------------


@dataclass
class ResumeState:
    """Serializable session state for resuming a provider execution."""

    provider: str
    backend: str
    session_token: str
    created_at: str  # ISO 8601
    last_active_at: str  # ISO 8601
    turn_count: int
    is_resumable: bool

    def to_json(self) -> str:
        return json.dumps({
            "provider": self.provider,
            "backend": self.backend,
            "session_token": self.session_token,
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "turn_count": self.turn_count,
            "is_resumable": self.is_resumable,
        })

    @classmethod
    def from_json(cls, raw: str) -> ResumeState:
        data = json.loads(raw)
        return cls(
            provider=data["provider"],
            backend=data["backend"],
            session_token=data["session_token"],
            created_at=data["created_at"],
            last_active_at=data["last_active_at"],
            turn_count=data["turn_count"],
            is_resumable=data["is_resumable"],
        )


# ---------------------------------------------------------------------------
# ProviderResult
# ---------------------------------------------------------------------------


@dataclass
class ProviderResult:
    """Final result from a provider execution."""

    text: str
    is_error: bool
    input_tokens: int
    output_tokens: int
    resume_state: ResumeState | None
    duration_ms: int
    provider_reported_cost_usd: float | None
    model_canonical_id: str
    raw: Any = None


# ---------------------------------------------------------------------------
# ExecutionMode / ToolPolicy / OutputContract
# ---------------------------------------------------------------------------


class ExecutionMode(str, Enum):
    """Coding vs intelligence execution mode."""

    CODING = "coding"
    INTELLIGENCE = "intelligence"


@dataclass(frozen=True)
class ToolPolicy:
    """Defines which tools are available during execution."""

    mode: Literal["unrestricted", "allowlist", "denylist"]
    allowed_tools: list[str] = field(default_factory=list)
    denied_operations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OutputContract:
    """Specifies the expected output format from the model."""

    format: Literal["freeform", "json", "forge_question_capable"]
    json_schema: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# WorkspaceRoots / MCPServerConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceRoots:
    """Workspace boundaries for a provider execution."""

    primary_cwd: str
    read_only_dirs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for an MCP server to attach to a provider execution."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# SafetyBoundary / AuditVerdict / SafetyViolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SafetyBoundary:
    """Defines denied operations for safety enforcement."""

    denied_operations: list[str] = field(default_factory=list)


class AuditVerdict(str, Enum):
    """Result from SafetyAuditor.check()."""

    ALLOW = "allow"
    ABORT = "abort"
    WARN = "warn"


@dataclass
class SafetyViolation:
    """Tracks a safety violation for logging/audit."""

    tool_name: str
    tool_input: str | None
    denied_pattern: str
    verdict: AuditVerdict
    reason: str


# ---------------------------------------------------------------------------
# ProviderHealthStatus
# ---------------------------------------------------------------------------


@dataclass
class ProviderHealthStatus:
    """Result of a provider health check."""

    healthy: bool
    provider: str
    details: str
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ExecutionHandle (abstract)
# ---------------------------------------------------------------------------


class ExecutionHandle(abc.ABC):
    """Abstract handle to a running provider execution."""

    @property
    @abc.abstractmethod
    def is_running(self) -> bool: ...

    @abc.abstractmethod
    async def abort(self) -> None: ...

    @abc.abstractmethod
    async def result(self) -> ProviderResult: ...


# ---------------------------------------------------------------------------
# ProviderProtocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ProviderProtocol(Protocol):
    """Interface every provider must implement."""

    @property
    def name(self) -> str: ...

    def catalog_entries(self) -> list[CatalogEntry]: ...

    def health_check(self, backend: str | None = None) -> ProviderHealthStatus: ...

    def start(
        self,
        prompt: str,
        system_prompt: str,
        catalog_entry: CatalogEntry,
        execution_mode: ExecutionMode,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
        workspace: WorkspaceRoots,
        max_turns: int,
        mcp_servers: list[MCPServerConfig] | None = None,
        resume_state: ResumeState | None = None,
        on_event: Callable[[ProviderEvent], None] | None = None,
    ) -> ExecutionHandle: ...

    def can_resume(self, state: ResumeState) -> bool: ...

    def cleanup_session(self, state: ResumeState) -> None: ...
