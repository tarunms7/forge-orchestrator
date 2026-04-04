# Multi-Provider Architecture Design

**Date:** 2026-04-04
**Status:** Draft
**Scope:** Add OpenAI (Codex CLI + Agents SDK) as a second provider alongside Claude, with clean architecture for future providers.

---

## 1. Problem Statement

Forge hardcodes Claude as the sole LLM provider across the entire pipeline. 14 files import `claude_code_sdk` directly. 32 files reference Claude model names (`sonnet`, `opus`, `haiku`) as bare strings. Every system prompt, every tool restriction, every cost calculation assumes Claude.

This blocks:
- Users who want to use OpenAI models for some or all pipeline stages
- Per-stage model mixing (e.g., plan with Claude Opus, execute with Codex)
- Future provider support without invasive codebase changes

## 2. Design Constraints

- **Zero breakage for existing users.** Bare model names like `"sonnet"` continue to work everywhere (config, CLI, API, tests). Default behavior is identical to today.
- **Per-stage provider selection.** Users can assign different provider:model combinations to planner, agent, reviewer, and contract builder stages independently.
- **Native SDK calls, not a middleware layer.** Each provider uses its own SDK directly. No LiteLLM, no thick abstraction. LangChain's failure with thick abstractions is the cautionary tale.
- **MCP for tool extensibility, not for model routing.** MCP is used to expose Forge-specific custom tools to agents. Model selection and inference are handled by the provider layer.
- **Opt-in activation.** OpenAI provider requires explicit `openai_enabled=true`. Existing users see zero changes on upgrade.

## 3. Architecture Overview

Three-layer sandwich. Layer 1 and 3 are the existing Forge harness (unchanged). Layer 2 is the swappable provider slot (new).

```
┌─────────────────────────────────────────────┐
│  Layer 1: Forge Harness (Common, upstream)  │
│  Prompt assembly, context building,         │
│  safety boundaries, tool config,            │
│  file scope, question protocol              │
│  ── everything that exists today ──         │
│                    │                        │
│                    ▼                        │
│  ┌─────────────────────────────────┐       │
│  │  Layer 2: LLM Provider (Dynamic)│       │
│  │  Claude SDK  │  OpenAI SDK      │       │
│  │  (swappable, the only new code) │       │
│  └─────────────────────────────────┘       │
│                    │                        │
│                    ▼                        │
│  Layer 3: Forge Harness (Common, downstream)│
│  Error handling, retry, escalation,         │
│  cost tracking, streaming to UI,            │
│  file scope enforcement, review, merge      │
└─────────────────────────────────────────────┘
```

The provider layer is the only new code. Everything above it (prompt construction, context, safety config) and everything below it (retry, cost, streaming, review, merge) stays as-is.

## 4. Core Data Types

### 4.1 ModelSpec

Replaces every bare model string in the codebase.

```python
@dataclass(frozen=True)
class ModelSpec:
    provider: str   # "claude" | "openai"
    model: str      # "opus" | "sonnet" | "gpt-5.4" | "gpt-5.3-codex"

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, raw: str) -> "ModelSpec":
        """Parse 'claude:opus' or bare 'sonnet' (defaults to claude)."""
        if ":" in raw:
            provider, model = raw.split(":", 1)
            return cls(provider=provider, model=model)
        return cls(provider="claude", model=raw)
```

Backward compatibility: `ModelSpec.parse("sonnet")` returns `ModelSpec(provider="claude", model="sonnet")`. Every existing config value, test fixture, and CLI flag that passes a bare model name still works.

### 4.2 ModelDescriptor

Capabilities are per-model, not per-provider. Claude's `haiku` has different capabilities than `opus`. OpenAI's `gpt-5.4-nano` has different capabilities than `gpt-5.4`. The provider registers a descriptor for each model it supports.

```python
@dataclass(frozen=True)
class ModelDescriptor:
    """Resolved capabilities for a specific provider:model combination."""
    provider: str
    model: str
    backend: str               # "claude-code-sdk" | "codex-sdk" | "openai-agents-sdk"
    can_use_tools: bool = True
    can_stream: bool = True
    can_resume_session: bool = True
    can_run_shell: bool = True
    can_edit_files: bool = True
    supports_mcp_servers: bool = True
    max_context_tokens: int = 200_000
    supports_structured_output: bool = False
    supports_reasoning: bool = False

    @property
    def spec(self) -> "ModelSpec":
        return ModelSpec(provider=self.provider, model=self.model)
```

The `backend` field is explicit — it determines which SDK the provider uses to execute this model. This replaces the brittle heuristic of inferring backend from cwd/worktree shape.

Providers register descriptors at startup:

```python
class ClaudeProvider:
    def model_descriptors(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(provider="claude", model="opus", backend="claude-code-sdk",
                            max_context_tokens=1_000_000, supports_reasoning=True),
            ModelDescriptor(provider="claude", model="sonnet", backend="claude-code-sdk",
                            max_context_tokens=1_000_000),
            ModelDescriptor(provider="claude", model="haiku", backend="claude-code-sdk",
                            max_context_tokens=200_000),
        ]

class OpenAIProvider:
    def model_descriptors(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(provider="openai", model="gpt-5.4", backend="codex-sdk",
                            max_context_tokens=1_000_000, supports_reasoning=True),
            ModelDescriptor(provider="openai", model="gpt-5.4-mini", backend="codex-sdk",
                            max_context_tokens=1_000_000),
            ModelDescriptor(provider="openai", model="gpt-5.4-nano", backend="codex-sdk",
                            max_context_tokens=200_000, can_resume_session=False),
            # Agents SDK models (for planner/reviewer stages — no file editing needed)
            ModelDescriptor(provider="openai", model="o3", backend="openai-agents-sdk",
                            can_edit_files=False, can_run_shell=False,
                            supports_reasoning=True, supports_structured_output=True),
        ]
```

The registry validates at routing time that the selected model's descriptor supports the required capabilities for the stage:

```python
def validate_model_for_stage(descriptor: ModelDescriptor, stage: str) -> list[str]:
    """Returns list of validation errors. Empty = OK."""
    errors = []
    if stage == "agent":
        if not descriptor.can_edit_files:
            errors.append(f"{descriptor.spec} cannot edit files (required for agent stage)")
        if not descriptor.can_run_shell:
            errors.append(f"{descriptor.spec} cannot run shell (required for agent stage)")
    if stage in ("planner", "reviewer", "contract_builder"):
        if not descriptor.can_use_tools:
            errors.append(f"{descriptor.spec} cannot use tools (required for {stage})")
    return errors
```

This catches incompatible models before they reach execution, not after.

### 4.3 ProviderResult

Universal result from any provider. Replaces `SdkResult`.

```python
@dataclass
class ProviderResult:
    text: str
    is_error: bool
    input_tokens: int
    output_tokens: int
    session_id: str | None                  # opaque, provider-specific
    duration_ms: int
    provider_reported_cost_usd: float | None = None   # from SDK if available
    raw: Any = None                         # provider-specific raw response
```

Design decision: `session_id` is a plain string, not a typed object. Session IDs are opaque provider-specific tokens (Claude UUID vs Codex thread ID). The orchestrator stores them in the DB and passes them back on resume. No inspection, no parsing.

Design decision: `provider_reported_cost_usd` is optional. Claude SDK reports cost directly — we trust it. OpenAI returns tokens but not cost — we calculate from the cost registry. The cost resolution layer uses provider-reported cost when available, falls back to calculation.

### 4.4 ProviderEvent

A single streaming event from any provider. Replaces duck-typed `AssistantMessage`/`ResultMessage` handling. Must carry enough information to support all 11 current message handling locations (see Section 10.2 for the full audit).

```python
@dataclass
class ProviderEvent:
    kind: str               # "text" | "tool_use" | "tool_result" | "status" | "error" | "usage"
    text: str = ""
    tool_name: str = ""     # normalized: "Bash", "Read", "Edit", etc.
    tool_input: dict | None = None
    tool_call_id: str = ""          # for RuntimeGuard's pending_bash tracking
    is_tool_error: bool = False     # for RuntimeGuard's error detection
    token_count: int = 0            # for progress display (replaces len(block.text)//4)
    raw: Any = None                 # provider-specific raw message (for debugging)
```

Design decision: `tool_name` is normalized to a common vocabulary. Claude uses `"Bash"`, Codex uses `"command_execution"`. The provider normalizes to a common set (`"Bash"`, `"Read"`, `"Write"`, `"Edit"`, `"Glob"`, `"Grep"`) so the `SafetyAuditor` and `RuntimeGuard` don't need provider-specific checks.

### 4.5 SafetyBoundary

Operations agents must never perform autonomously. Provider-agnostic expression of the existing `AGENT_DISALLOWED_TOOLS` list.

```python
@dataclass
class SafetyBoundary:
    denied_operations: list[str] = field(default_factory=list)
```

Operations use Forge's own syntax. Each provider translates to its native mechanism.

```python
AGENT_DENIED_OPERATIONS = [
    "git:push", "git:rebase", "git:checkout", "git:reset_hard",
    "git:branch_delete", "git:merge", "git:clean", "git:stash",
    "git:cherry_pick", "git:tag", "git:remote",
    "net:curl", "net:wget", "net:ssh", "net:scp", "net:rsync",
    "net:nc", "net:telnet", "net:ftp",
    "priv:sudo", "priv:su", "priv:doas",
    "perm:chmod", "perm:chown", "perm:chgrp",
    "proc:kill", "proc:pkill", "proc:killall",
    "container:docker", "container:podman",
    "sys:systemctl", "sys:service", "sys:mount",
    "env:export", "env:unset",
    "read:.env", "read:.env.*",
]
```

### Safety Enforcement: Three-Layer Model

The safety model must produce identical behavior regardless of provider. A hallucinating agent must be blocked the same way whether it runs on Claude or Codex. This is achieved through three enforcement layers, all Forge-owned:

**Layer A: Provider-native enforcement (best effort, provider-specific)**
Each provider translates `denied_operations` to its strongest native mechanism:
- `ClaudeProvider`: `disallowed_tools` in `ClaudeCodeOptions` (hard SDK-level block)
- `OpenAIProvider`: Codex `sandbox_mode` + explicit deny rules in developer message

**Layer B: Forge-owned output audit (hard, provider-agnostic)**
After every `on_event` callback with `kind="tool_use"`, Forge's `SafetyAuditor` checks the tool call against the `ToolPolicy` BEFORE the tool executes. This is NOT inside the provider — it wraps the provider.

```python
class SafetyAuditor:
    """Forge-owned hard enforcement. Runs on every tool_use event from any provider."""

    def __init__(self, policy: ToolPolicy):
        self._policy = policy

    def check(self, event: ProviderEvent) -> AuditResult:
        """Returns ALLOW, BLOCK, or WARN. BLOCK raises and terminates the agent."""
        if event.kind != "tool_use":
            return AuditResult.ALLOW

        # Check against denied operations
        for pattern in self._policy.denied_operations:
            if self._matches(event.tool_name, event.tool_input, pattern):
                return AuditResult.BLOCK

        # Check against allowlist if in allowlist mode
        if self._policy.mode == "allowlist":
            if event.tool_name not in self._policy.allowed_tools:
                return AuditResult.BLOCK

        return AuditResult.ALLOW
```

This means even if a provider's native enforcement fails (e.g., the prompt-based instruction is ignored by a hallucinating model), Forge catches it. The auditor runs in the common harness, not inside the provider.

**Layer C: Post-execution file scope enforcement (existing, unchanged)**
`daemon_executor.py` already reverts out-of-scope file changes after agent execution. This layer stays as-is and catches anything that slipped through A and B.

The three layers together guarantee: provider-native block (fastest, catches most) → Forge audit (catches provider failures) → post-execution revert (catches everything else). The behavior is identical across providers because layers B and C are provider-agnostic.

Agents are not restricted beyond these safety boundaries. They can read/write any file, run any shell command, install packages, run tests, build — full power within their worktree.

### 4.6 MCPServerConfig

Configuration for MCP servers that agents can connect to.

```python
@dataclass
class MCPServerConfig:
    name: str
    command: str          # e.g., "python"
    args: list[str]       # e.g., ["-m", "forge.mcp.server"]
    env: dict[str, str] = field(default_factory=dict)
```

## 5. Provider Protocol

The contract every provider implements.

### 5.1 Execution Mode

Explicit enum, not inferred from filesystem state.

```python
class ExecutionMode(str, Enum):
    """How the agent should operate. Determines backend selection and permissions."""
    CODING = "coding"          # Full file editing + shell access in a worktree
                               # Claude: claude-code-sdk, OpenAI: codex-sdk
    INTELLIGENCE = "intelligence"  # Read-only analysis, planning, review, structured output
                                   # Claude: claude-code-sdk, OpenAI: openai-agents-sdk
```

The daemon sets this explicitly per stage:
- `agent`, `ci_fix`: `ExecutionMode.CODING`
- `planner`, `unified_planner`, `contract_builder`, `reviewer`, `synthesizer`, `followup`: `ExecutionMode.INTELLIGENCE`

The provider uses `execution_mode` + the model's `backend` field from `ModelDescriptor` to pick the right SDK.

### 5.2 Tool Policy

Explicit, not just a denylist. Forge owns enforcement; the provider is one enforcement layer, not the only one.

```python
@dataclass
class ToolPolicy:
    """What tools the agent may use. Forge enforces this; the provider also enforces where possible."""
    mode: Literal["unrestricted", "allowlist", "denylist"]
    allowed_tools: list[str] = field(default_factory=list)    # only if mode="allowlist"
    denied_operations: list[str] = field(default_factory=list)  # only if mode="denylist"
```

Stage-specific policies (replacing the current scattered tool lists):

```python
PLANNER_TOOL_POLICY = ToolPolicy(mode="allowlist", allowed_tools=["Read", "Glob", "Grep", "Bash"])
CONTRACT_TOOL_POLICY = ToolPolicy(mode="allowlist", allowed_tools=["Read", "Glob", "Grep", "Bash"])
AGENT_TOOL_POLICY = ToolPolicy(mode="denylist", denied_operations=AGENT_DENIED_OPERATIONS)
REVIEWER_TOOL_POLICY = ToolPolicy(mode="allowlist", allowed_tools=["Read", "Glob", "Grep", "Bash"])
```

### 5.3 Output Contract

What the caller expects back. Enables the provider to optimize (e.g., use structured output when available).

```python
@dataclass
class OutputContract:
    """What the caller expects in the result text."""
    format: Literal["freeform", "json", "forge_question_capable"]
    json_schema: dict | None = None     # if format="json", the expected schema
```

Stage-specific contracts:
- `planner`, `contract_builder`: `OutputContract(format="json", json_schema=TASK_GRAPH_SCHEMA)`
- `agent`: `OutputContract(format="forge_question_capable")`
- `reviewer`: `OutputContract(format="json", json_schema=REVIEW_VERDICT_SCHEMA)`
- `followup`, `synthesizer`: `OutputContract(format="freeform")`

### 5.4 Protocol Definition

```python
class ProviderProtocol(Protocol):
    @property
    def name(self) -> str: ...

    def model_descriptors(self) -> list[ModelDescriptor]: ...

    async def execute(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        execution_mode: ExecutionMode,
        tool_policy: ToolPolicy,
        output_contract: OutputContract,
        cwd: str,
        max_turns: int,
        mcp_servers: list[MCPServerConfig] | None = None,
        resume: str | None = None,
        on_event: Callable[[ProviderEvent], Awaitable[None]] | None = None,
    ) -> ProviderResult: ...
```

Design decision: single `execute()` method, not separate methods per pipeline stage. All 7 current call sites (adapter, claude_planner, unified_planner, contract_builder, llm_review, synthesizer, followup) do the same thing: build system prompt, set tool restrictions, call SDK, get result. The differences are captured in `execution_mode`, `tool_policy`, and `output_contract`.

Design decision: `on_event` is async. The daemon's event emission is always async. Making the callback async by contract eliminates the sync/async mismatch in the current codebase.

Design decision: `system_prompt` is a plain string. The caller assembles it using existing prompt templates. The provider puts it in the right place internally (Claude: `append_system_prompt`, Codex: `instructions` / developer message).

## 6. Provider Implementations

### 6.1 ClaudeProvider

Wraps `claude-code-sdk`. This is the current `sdk_helpers.py` code extracted into the provider protocol.

```python
class ClaudeProvider:
    name = "claude"

    capabilities = ProviderCapabilities(
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_restrict_tools=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=True,
        max_context_tokens=1_000_000,
    )
```

Internal responsibilities:
- Remove `CLAUDECODE` env var before SDK calls (nested session guard)
- Apply monkey-patch for unknown message types (`rate_limit_event`, etc.)
- Translate `SafetyBoundary.denied_operations` to Claude `disallowed_tools` syntax
- Build `ClaudeCodeOptions(append_system_prompt=..., permission_mode="bypassPermissions", ...)`
- Async iterate over `query()`, convert each `AssistantMessage`/`ResultMessage` to `ProviderEvent`
- Extract `ResultMessage` fields into `ProviderResult` including `provider_reported_cost_usd` from `total_cost_usd`

`available_models()`: returns from a config-driven list in `ForgeSettings`. Claude Code SDK does not expose a model list API.

### 6.2 OpenAIProvider

Wraps OpenAI Codex SDK and Agents SDK via the Responses API.

```python
class OpenAIProvider:
    name = "openai"

    capabilities = ProviderCapabilities(
        can_use_tools=True,
        can_stream=True,
        can_resume_session=True,
        can_restrict_tools=True,
        can_run_shell=True,
        can_edit_files=True,
        supports_mcp_servers=True,
        max_context_tokens=1_000_000,
    )
```

Internal responsibilities:
- Select backend based on `execution_mode` parameter: `CODING` → Codex SDK, `INTELLIGENCE` → Agents SDK. No heuristics, no filesystem inspection.
- Translate `SafetyBoundary` to Codex sandbox config (`sandbox_mode="workspace-write"`, `approval_policy="on-request"`) + developer message instructions for git operations
- Configure MCP servers via Codex thread options or Agents SDK `MCPServerStdio`
- Start or resume thread: new via `codex.startThread()`, resume via `codex.resumeThread(session_id)`
- Stream execution via `thread.runStreamed()`, convert Codex events to `ProviderEvent`
- Extract usage from `turn.completed` event, calculate cost from cost registry (OpenAI does not report cost directly)

`available_models()`: fetched from OpenAI API (`GET /v1/models`), cached with periodic refresh. Static fallback list if API call fails.

### 6.3 Event Mapping

| Claude event | OpenAI event | ProviderEvent.kind |
|---|---|---|
| `AssistantMessage` with text blocks | `item.completed` type=`agent_message` | `"text"` |
| `AssistantMessage` with tool_use block | `item.started` type=`command_execution`/`file_change` | `"tool_use"` |
| `ResultMessage` | `turn.completed` | `"result"` (terminal) |
| SDK exception | `turn.failed` or `error` | `"error"` |

Conversion happens inside each provider. The common harness only sees `ProviderEvent`.

## 7. Provider Registry

Created once at daemon startup, passed to every component that needs a provider.

```python
class ProviderRegistry:
    def __init__(self, settings: ForgeSettings):
        self._providers: dict[str, ProviderProtocol] = {}
        self._descriptors: dict[str, ModelDescriptor] = {}   # keyed by "provider:model"
        self._settings = settings

    def register(self, provider: ProviderProtocol) -> None:
        """Register provider and index all its model descriptors."""
        self._providers[provider.name] = provider
        for desc in provider.model_descriptors():
            self._descriptors[str(desc.spec)] = desc

    def get(self, name: str) -> ProviderProtocol: ...
    def get_for_model(self, spec: ModelSpec) -> ProviderProtocol: ...
    def get_descriptor(self, spec: ModelSpec) -> ModelDescriptor: ...
    def all_providers(self) -> list[ProviderProtocol]: ...
    def validate_model(self, spec: ModelSpec) -> bool: ...
    def validate_model_for_stage(self, spec: ModelSpec, stage: str) -> list[str]:
        """Returns validation errors if this model can't handle this stage."""
        desc = self.get_descriptor(spec)
        return _validate_model_for_stage(desc, stage)
```

Initialization in daemon:

```python
def _init_providers(self) -> ProviderRegistry:
    registry = ProviderRegistry(self._settings)

    from forge.providers.claude import ClaudeProvider
    registry.register(ClaudeProvider(self._settings))

    if self._settings.openai_enabled:
        from forge.providers.openai import OpenAIProvider
        registry.register(OpenAIProvider(self._settings))

    return registry
```

Design decision: lazy imports inside `_init_providers()`. If a user doesn't have the OpenAI SDK installed, `import forge.providers.openai` would crash at startup. Lazy import means Forge works with just Claude. If the import fails when `openai_enabled=true`, Forge logs: "OpenAI provider enabled but SDK not installed."

Design decision: singleton-per-daemon, not module-level global. The registry needs settings (API keys, model lists). Creating it in the daemon constructor follows the existing pattern.

Flow through codebase:

```
ForgeDaemon(settings)
  └── self._providers = _init_providers()
      ├── passed to ExecutorMixin._execute_task()
      ├── passed to ReviewMixin._run_review()
      ├── passed to Planner()
      ├── passed to ContractBuilder()
      └── passed to CIWatcher()
```

Each component calls `providers.get_for_model(spec)` to get the right provider, then calls `provider.execute(...)`.

## 8. Model Router Refactor

`select_model()` returns `ModelSpec` instead of bare strings. Routing table supports `provider:model` values.

### 8.1 Default Routing Table

```python
_DEFAULT_ROUTING_TABLE = {
    "auto": {
        "planner":          {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "contract_builder": {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "agent":            {"low": "claude:sonnet", "medium": "claude:opus", "high": "claude:opus"},
        "reviewer":         {"low": "claude:sonnet", "medium": "claude:sonnet", "high": "claude:sonnet"},
    },
    "fast": {
        "planner":          {"low": "claude:sonnet", "medium": "claude:sonnet", "high": "claude:sonnet"},
        "contract_builder": {"low": "claude:sonnet", "medium": "claude:sonnet", "high": "claude:sonnet"},
        "agent":            {"low": "claude:haiku", "medium": "claude:haiku", "high": "claude:haiku"},
        "reviewer":         {"low": "claude:haiku", "medium": "claude:sonnet", "high": "claude:sonnet"},
    },
    "quality": {
        "planner":          {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "contract_builder": {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "agent":            {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "reviewer":         {"low": "claude:sonnet", "medium": "claude:sonnet", "high": "claude:sonnet"},
    },
}
```

Overridable via `ForgeSettings` or `forge.toml [routing]` section.

### 8.2 select_model() Signature

```python
def select_model(
    strategy: str,
    stage: str,
    complexity: str = "medium",
    overrides: dict[str, str] | None = None,
    retry_count: int = 0,
    routing_table: dict | None = None,
    registry: ProviderRegistry | None = None,
) -> ModelSpec:
```

### 8.3 Escalation Logic

Escalation stays within the same provider. If user configured OpenAI for agents, retry escalates within OpenAI's model line, not cross-provider.

```python
_ESCALATION_CHAINS = {
    "claude": {"haiku": "sonnet", "sonnet": "opus"},
    "openai": {"gpt-5.4-nano": "gpt-5.4-mini", "gpt-5.4-mini": "gpt-5.4"},
}
```

Configurable in settings. Cross-provider fallback is deliberately NOT built — automatic cross-provider fallback would hide failures behind degraded quality. If a provider is down, Forge fails clearly and lets the user decide.

### 8.4 Provider Tier Mapping

For `--provider openai` CLI shorthand, the router maps strategy tiers to the target provider's models:

```python
_PROVIDER_TIER_MAP = {
    "openai": {"high": "gpt-5.4", "medium": "gpt-5.4-mini", "low": "gpt-5.4-nano"},
    "claude": {"high": "opus", "medium": "sonnet", "low": "haiku"},
}
```

Configurable in settings. When `--provider openai` is set and the routing table says `"agent": {"medium": "claude:sonnet"}`, the router sees "medium tier" and maps to `openai:gpt-5.4-mini`.

### 8.5 Override Precedence

Highest wins:
1. CLI per-stage flags (`--agent openai:gpt-5.4`)
2. CLI `--provider` flag (sets default provider, tier map picks models)
3. `forge.toml` `[routing]` section
4. `ForgeSettings` per-stage override env vars
5. Default routing table

## 9. Cost Tracking

### 9.1 Cost Registry

Replaces the 6 hardcoded `cost_rate_*` fields in `ForgeSettings` with a lookup table.

```python
@dataclass(frozen=True)
class ModelRates:
    input_per_1k: float
    output_per_1k: float

class CostRegistry:
    def __init__(self, overrides: dict[str, ModelRates] | None = None): ...
    def get_rates(self, spec: ModelSpec) -> ModelRates: ...
    def calculate_cost(self, spec: ModelSpec, input_tokens: int, output_tokens: int) -> float: ...
```

Fallback chain: exact `provider:model` key → `provider:default` → budget-safe behavior.

```python
class UnknownCostBehavior(str, Enum):
    BLOCK = "block"            # Refuse to execute. Default when budget is set.
    ESTIMATE_HIGH = "estimate_high"  # Use the provider's most expensive model rates.
    ALLOW = "allow"            # Report $0.00 with warning. Only for dev/testing.

def get_rates(self, spec: ModelSpec) -> ModelRates:
    key = str(spec)
    if key in self._rates:
        return self._rates[key]
    provider_default = f"{spec.provider}:default"
    if provider_default in self._rates:
        return self._rates[provider_default]
    # Unknown model
    if self._unknown_behavior == UnknownCostBehavior.BLOCK:
        raise UnknownModelCostError(
            f"No cost rates for {spec}. Cannot execute with budget enforcement active. "
            f"Add rates via FORGE_COST_RATES or set FORGE_UNKNOWN_COST_BEHAVIOR=estimate_high."
        )
    elif self._unknown_behavior == UnknownCostBehavior.ESTIMATE_HIGH:
        logger.warning("No cost rates for %s, using provider's highest rate as estimate", spec)
        return self._get_highest_rate_for_provider(spec.provider)
    else:  # ALLOW
        logger.warning("No cost rates for %s, reporting $0.00 (UNSAFE for budget enforcement)", spec)
        return ModelRates(0.0, 0.0)
```

Default behavior: `BLOCK` when a pipeline budget is configured (`ForgeSettings.max_pipeline_cost_usd`), `ESTIMATE_HIGH` otherwise. The `ALLOW` mode exists only for development/testing. This prevents silent budget overruns from unknown models.

### 9.2 Shipped Default Rates

```python
_DEFAULT_RATES = {
    "claude:opus":          ModelRates(0.015, 0.075),
    "claude:sonnet":        ModelRates(0.003, 0.015),
    "claude:haiku":         ModelRates(0.00025, 0.00125),
    "claude:default":       ModelRates(0.003, 0.015),
    "openai:gpt-5.4":      ModelRates(0.010, 0.030),
    "openai:gpt-5.4-mini": ModelRates(0.002, 0.008),
    "openai:gpt-5.4-nano": ModelRates(0.0005, 0.002),
    "openai:gpt-5.3-codex": ModelRates(0.006, 0.024),
    "openai:default":       ModelRates(0.003, 0.015),
}
```

### 9.3 Cost Resolution

```python
def resolve_cost(result: ProviderResult, spec: ModelSpec, cost_registry: CostRegistry) -> float:
    if result.provider_reported_cost_usd is not None:
        return result.provider_reported_cost_usd
    return cost_registry.calculate_cost(spec, result.input_tokens, result.output_tokens)
```

Claude SDK reports cost directly — trusted. OpenAI returns tokens but not cost — calculated from registry.

### 9.4 Legacy Settings Migration

Existing `cost_rate_sonnet_input` etc. fields are deprecated but still loaded. On startup, they're converted into `CostRegistry` overrides so existing configs work.

### 9.5 estimate_pipeline_cost()

Updated to accept `CostRegistry` and `ProviderRegistry`. Uses actual routing table to determine models per stage, then calculates from registry rates. Same estimation logic (1 planner + N agents + N reviewers), now provider-aware.

### 9.6 Database Impact

None. The DB stores `cost_usd`, `agent_cost_usd`, `review_cost_usd` as floats. Already provider-agnostic.

## 10. Streaming & Event Flow

### 10.1 Current Flow

```
sdk_query() yields messages → on_message callback → daemon extracts text/activity → WebSocket → UI
```

### 10.2 Full Inventory of Claude-Specific Message Handling

The scope is significantly larger than just two helper functions. A thorough audit found **11 locations** across **6 files** where Claude message types (`AssistantMessage`, `ResultMessage`, block objects with `.text`, `.name`, `.input`, `.tool_use_id`, `.is_error`) are accessed:

**daemon_helpers.py (4 locations):**
- `_extract_text()`: `isinstance(message, AssistantMessage)`, `isinstance(message, ResultMessage)`, iterates `message.content`, accesses `block.text`
- `_extract_activity()`: same isinstance checks, plus `hasattr(block, "name")`, accesses `block.name`, `block.input`

**daemon.py (2 locations):**
- `_on_planner_msg()`: `isinstance(msg, AssistantMessage)`, iterates `msg.content`, accesses `block.text`, token counting via `len(block.text) // 4`
- `_on_unified_msg()`: same pattern as planner

**daemon_executor.py (1 location):**
- `_on_msg()`: passes raw message to `guard.inspect(msg)`, calls `_extract_activity(msg)`

**daemon_review.py (1 location):**
- `_make_review_on_message._on_msg()`: calls `_extract_text(msg)`

**followup.py (1 location):**
- `on_message()`: `hasattr(msg, "content")`, `hasattr(msg, "result")`, accesses `msg.content` and `msg.result` directly

**learning/guard.py (RuntimeGuard, 1 location):**
- `inspect()`: `getattr(message, "content", None)`, iterates blocks checking `hasattr(block, "name")`, `hasattr(block, "tool_use_id")`, `hasattr(block, "is_error")`, accesses `block.name`, `block.id`, `block.input`, `block.tool_use_id`, `block.is_error`, `block.content`

**sdk_helpers.py (1 location):**
- Stream handler: `isinstance(message, ResultMessage)`, accesses `msg.usage`, `msg.result`, `msg.total_cost_usd`, `msg.session_id`, `msg.duration_ms`

### 10.3 New Flow

```
provider.execute() calls on_event(ProviderEvent) → Forge harness extracts text/activity → WebSocket → UI
```

Each provider converts its native messages to `ProviderEvent` **inside the provider**. The harness only ever sees `ProviderEvent`. This means ALL 11 locations above change to use `ProviderEvent` fields instead of Claude types.

### 10.4 ProviderEvent Must Carry Enough Information

The `ProviderEvent` type needs additional fields to support all current use cases:

```python
@dataclass
class ProviderEvent:
    kind: str               # "text" | "tool_use" | "tool_result" | "status" | "error" | "usage"
    text: str = ""
    tool_name: str = ""
    tool_input: dict | None = None
    tool_call_id: str = ""          # for RuntimeGuard's pending_bash tracking
    is_tool_error: bool = False     # for RuntimeGuard's error detection
    token_count: int = 0            # for progress/token counting (replaces len(block.text)//4)
    raw: Any = None
```

### 10.5 Migration of Each Location

| Location | Current | After |
|---|---|---|
| `_extract_text()` | isinstance + block.text | `event.kind in ("text", "result")` → `event.text` |
| `_extract_activity()` | isinstance + block.name | `event.kind == "tool_use"` → `event.tool_name` |
| `_on_planner_msg()` | isinstance + block iteration + token counting | `event.kind == "text"` → `event.text` + `event.token_count` |
| `_on_unified_msg()` | same as planner | same migration |
| `_on_msg()` (executor) | passes raw to guard | passes `ProviderEvent` to guard |
| `_on_msg()` (review) | calls _extract_text | same as _extract_text migration |
| `on_message()` (followup) | hasattr checks | `event.kind` checks |
| `guard.inspect()` | deep block introspection | `event.kind == "tool_use"` + `event.tool_call_id` + `event.is_tool_error` |
| `sdk_helpers.py` stream | isinstance ResultMessage | moves inside ClaudeProvider |

### 10.6 RuntimeGuard Migration

`RuntimeGuard.inspect()` currently does deep block-level introspection (checking for Bash tool use, tracking pending commands, detecting errors). After migration:

```python
# Before:
def inspect(self, message):
    content = getattr(message, "content", None)
    for block in content:
        if hasattr(block, "name") and block.name == "Bash":
            tool_id = getattr(block, "id", None)
            command = (getattr(block, "input", None) or {}).get("command", "")
            ...

# After:
def inspect(self, event: ProviderEvent):
    if event.kind == "tool_use" and event.tool_name == "Bash":
        command = (event.tool_input or {}).get("command", "")
        self._pending_bash[event.tool_call_id] = command
    elif event.kind == "tool_result" and event.is_tool_error:
        if event.tool_call_id in self._pending_bash:
            # error in a bash command we were tracking
            ...
```

Note: `tool_name == "Bash"` is Claude Code's tool name. Codex uses `command_execution`. The `SafetyAuditor` (Section 4.5) normalizes tool names before they reach the guard, OR the guard checks both names. This must be tested explicitly.

Everything downstream of these 11 locations (WebSocket emission, UI rendering) stays the same because they already consume plain strings.

## 11. Error Handling & Retry

Three existing layers. Layer 1 moves inside providers. Layers 2 and 3 stay in the common harness.

### 11.1 Layer 1: Provider-Internal Error Handling

Each provider handles its own internal quirks:
- `ClaudeProvider`: monkey-patch for unknown message types, CLAUDECODE env var guard, ResultMessage parsing
- `OpenAIProvider`: Codex SDK connection failures, malformed event streams, thread creation errors

Providers catch internal errors, log provider-specific details, and re-raise clean exceptions.

### 11.2 Layer 2: Transient Retry (agents/runtime.py)

```python
async def run_with_retry(
    provider: ProviderProtocol,
    *,
    prompt, system_prompt, spec, cwd, max_turns,
    safety_boundary, mcp_servers, resume, on_event,
    max_retries: int = 2,
) -> ProviderResult:
```

- Retries on transient errors (rate_limit, 429, 503, connection reset, timeout)
- Exponential backoff with jitter: `5 * (2^attempt) + random(0, 5)` seconds
- `resume=None` on retry (don't continue from potentially corrupted session state)
- Non-transient errors propagate immediately

Transient detection via string matching on common signals across both SDKs: `"rate_limit"`, `"overloaded"`, `"429"`, `"503"`, `"502"`, `"connection"`, `"reset"`, `"timeout"`, `"retry"`, `"server_error"`.

### 11.3 Layer 3: Task-Level Retry with Escalation (daemon_executor.py)

Unchanged. `select_model(retry_count=N)` now returns `ModelSpec` with escalated model within the same provider. Up to 5 task-level retries. This layer is already provider-agnostic — it calls `_run_agent()` which now calls through the provider protocol.

### 11.4 Cross-Provider Fallback

Deliberately NOT built. If Claude is down, Forge does not automatically try OpenAI. The user configured providers for specific stages for a reason. Automatic cross-provider fallback hides failures behind degraded quality. Forge fails clearly and lets the user decide.

## 12. Settings & Configuration

### 12.1 New ForgeSettings Fields

```python
class ForgeSettings(BaseSettings):
    # --- Existing (unchanged) ---
    model_strategy: str = "auto"
    planning_mode: str = "auto"
    max_agents: int = 5
    agent_timeout_seconds: int = 600
    agent_max_turns: int = 75
    autonomy: str = "balanced"
    question_limit: int = 3
    ci_fix_enabled: bool = True

    # --- Existing (deprecated, backward compat) ---
    cost_rate_sonnet_input: float = 0.003
    cost_rate_sonnet_output: float = 0.015
    cost_rate_haiku_input: float = 0.00025
    cost_rate_haiku_output: float = 0.00125
    cost_rate_opus_input: float = 0.015
    cost_rate_opus_output: float = 0.075

    # --- New ---
    openai_enabled: bool = False

    # Per-stage overrides (now accept "provider:model" format)
    planner_model: str | None = None
    agent_model_low: str | None = None
    agent_model_medium: str | None = None
    agent_model_high: str | None = None
    reviewer_model: str | None = None
    contract_builder_model: str | None = None
    ci_fix_model: str | None = None

    # Cost rate overrides (supplement defaults)
    cost_rates: dict[str, dict[str, float]] | None = None
```

### 12.2 Environment Variables

```
FORGE_OPENAI_ENABLED=true
OPENAI_API_KEY=sk-...        # Standard OpenAI env var, not FORGE-prefixed
```

`OPENAI_API_KEY` is not `FORGE_OPENAI_API_KEY` because the OpenAI SDK reads `OPENAI_API_KEY` by default. Fighting this convention creates friction.

### 12.3 forge.toml Changes

```toml
# Existing (still works)
[agents]
model = "sonnet"

# New format (provider-aware)
[agents]
model = "claude:sonnet"

# Per-stage overrides (new section, optional)
[routing]
planner = "claude:opus"
agent_low = "openai:gpt-5.4-mini"
agent_medium = "openai:gpt-5.4"
agent_high = "claude:opus"
reviewer = "claude:sonnet"
contract_builder = "claude:opus"
```

### 12.4 Config Validation

Model validation moves from `__post_init__` (which can't access the registry) to an explicit `validate()` method called during daemon startup after providers are registered.

```python
@dataclass
class AgentConfig:
    model: str = "sonnet"

    def validate(self, registry: ProviderRegistry) -> None:
        spec = ModelSpec.parse(self.model)
        if not registry.validate_model(spec):
            available = [f"{p.name}:{m}" for p in registry.all_providers() for m in p.available_models()]
            raise ConfigError(f"Unknown model '{self.model}'. Available: {available}")
```

## 13. Database Changes

### 13.1 Schema Additions

```sql
-- Tasks: full model tracking per execution attempt
ALTER TABLE tasks ADD COLUMN provider_model TEXT DEFAULT 'claude:sonnet';
ALTER TABLE tasks ADD COLUMN backend TEXT DEFAULT 'claude-code-sdk';
ALTER TABLE tasks ADD COLUMN model_history TEXT;  -- JSON array of escalation history

-- Pipelines: resolved config snapshot at creation time
ALTER TABLE pipelines ADD COLUMN provider_config TEXT;  -- JSON of per-stage provider:model:backend
```

- `tasks.provider_model`: full `provider:model` string (e.g., `"claude:opus"`, `"openai:gpt-5.4"`). Not just provider — the exact model.
- `tasks.backend`: which SDK was used (`"claude-code-sdk"`, `"codex-sdk"`, `"openai-agents-sdk"`).
- `tasks.model_history`: JSON array tracking escalation across retries. Example: `["claude:sonnet", "claude:opus"]` means the task started on sonnet and escalated to opus on retry. Critical for debugging cost spikes and understanding which models struggle with which tasks.
- `pipelines.provider_config`: JSON snapshot of the per-stage routing resolved at pipeline creation time. Includes provider, model, and backend for each stage (planner, agent per complexity tier, reviewer, contract_builder).

### 13.2 model_history Format

```json
[
    {"attempt": 1, "model": "claude:sonnet", "backend": "claude-code-sdk", "result": "retry", "cost_usd": 0.12},
    {"attempt": 2, "model": "claude:opus", "backend": "claude-code-sdk", "result": "success", "cost_usd": 0.45}
]
```

Written to by `daemon_executor.py` after each agent execution attempt, before the retry decision. This is append-only — each attempt adds an entry.

### 13.3 No Other Schema Changes

`cost_usd`, `agent_cost_usd`, `review_cost_usd`, `session_id`, `input_tokens`, `output_tokens` are already provider-agnostic.

## 14. API Changes

### 14.1 Settings Endpoint

`GET /api/settings` response adds:
- `openai_enabled: bool`
- `available_providers: list[str]` (populated from registry)
- `available_models: dict[str, list[str]]` (populated from registry)

Per-stage model values now accept `"provider:model"` format. Bare values like `"opus"` still work.

### 14.2 New Provider Info Endpoint

```
GET /api/providers
```

Returns registered providers, their models, and capabilities. Used by UI for dropdown population.

```json
{
  "providers": [
    {
      "name": "claude",
      "models": [
        {"model": "opus", "backend": "claude-code-sdk", "can_edit_files": true, "can_run_shell": true, "max_context_tokens": 1000000},
        {"model": "sonnet", "backend": "claude-code-sdk", "can_edit_files": true, "can_run_shell": true, "max_context_tokens": 1000000},
        {"model": "haiku", "backend": "claude-code-sdk", "can_edit_files": true, "can_run_shell": true, "max_context_tokens": 200000}
      ]
    },
    {
      "name": "openai",
      "models": [
        {"model": "gpt-5.4", "backend": "codex-sdk", "can_edit_files": true, "can_run_shell": true, "max_context_tokens": 1000000},
        {"model": "gpt-5.4-mini", "backend": "codex-sdk", "can_edit_files": true, "can_run_shell": true, "max_context_tokens": 1000000},
        {"model": "o3", "backend": "openai-agents-sdk", "can_edit_files": false, "can_run_shell": false, "supports_reasoning": true}
      ]
    }
  ]
}
```

The UI uses `can_edit_files` and `can_run_shell` to gray out incompatible models for the agent stage dropdown. A model with `can_edit_files=false` cannot be selected for agent execution.
```

### 14.3 Backward Compatibility

`UpdateSettingsRequest` accepts `planner_model`, `agent_model_low`, etc. as strings. Both `"opus"` and `"claude:opus"` and `"openai:gpt-5.4"` are valid. No breaking API change.

## 15. Web UI Changes

### 15.1 Settings Page

Each stage gets a two-part selector:

```
Planner:          [Claude ▼]  [opus ▼]
Agent (low):      [OpenAI ▼]  [gpt-5.4-mini ▼]
Agent (medium):   [OpenAI ▼]  [gpt-5.4 ▼]
Agent (high):     [Claude ▼]  [opus ▼]
Reviewer:         [Claude ▼]  [sonnet ▼]
Contract Builder: [Claude ▼]  [opus ▼]
```

Provider dropdown populated from `GET /api/providers`. Model dropdown refreshes when provider changes.

### 15.2 Task Detail View

Add provider:model label and escalation history to existing task card:

```
Model: claude:opus (via claude-code-sdk)
Escalation: sonnet → opus (retry 2)
```

If no escalation occurred, show only the model line. The escalation history is populated from `tasks.model_history`.

### 15.3 Pipeline Summary

Show resolved provider config:

```
Planner: claude:opus | Agents: openai:gpt-5.4 | Reviewer: claude:sonnet
```

No new pages. These are additions to existing views.

## 16. CLI Changes

### 16.1 New Options

```bash
forge run "task" --planner claude:opus --agent openai:gpt-5.4 --reviewer claude:sonnet
forge run "task" --provider openai
forge run "task" --strategy fast --agent openai:gpt-5.4
```

### 16.2 Precedence

Highest wins:
1. CLI per-stage flags
2. CLI `--provider` flag
3. `forge.toml` `[routing]` section
4. `ForgeSettings` env vars
5. Default routing table

### 16.3 forge doctor

Add provider health checks:

```
Claude:    OK (claude-code-sdk v1.x, authenticated)
OpenAI:    OK (codex-sdk v1.x, OPENAI_API_KEY set)
```

Or on failure:

```
OpenAI:    FAIL — openai_enabled=true but OPENAI_API_KEY not set
```

## 17. Forge MCP Server

Optional (can ship after core provider layer). FastMCP server exposing Forge-specific custom tools to agents via MCP.

### 17.1 Tools

```python
@forge_mcp.tool()
async def forge_ask_question(question, context, suggestions, impact) -> dict: ...

@forge_mcp.tool()
async def forge_check_scope(file_path) -> dict: ...

@forge_mcp.tool()
async def forge_get_lessons(pattern, file_path) -> dict: ...
```

### 17.2 Lifecycle

Started per-agent as a stdio subprocess scoped to the task. Passed to `provider.execute(mcp_servers=[config])`. Both Claude and Codex connect natively. Server process dies when agent finishes.

### 17.3 Why Optional for Launch

The FORGE_QUESTION protocol currently works via text parsing. Not elegant, but tested and working. The MCP server is an improvement, not a requirement for the provider refactor.

## 18. File Structure

### 18.1 New Files

```
forge/providers/
    __init__.py         # Exports: ProviderProtocol, ProviderResult, ProviderEvent, etc.
    base.py             # Core types: ModelSpec, ModelDescriptor, ExecutionMode, ToolPolicy,
                        #   OutputContract, ProviderResult, ProviderEvent, SafetyBoundary,
                        #   MCPServerConfig, ProviderProtocol
    registry.py         # ProviderRegistry (with descriptor indexing + stage validation)
    claude.py           # ClaudeProvider (extracted from sdk_helpers.py)
    openai.py           # OpenAIProvider
    restrictions.py     # AGENT_DENIED_OPERATIONS list + per-stage ToolPolicy constants
    safety_auditor.py   # SafetyAuditor — Forge-owned hard enforcement on tool_use events

forge/core/
    cost_registry.py    # CostRegistry, ModelRates, resolve_cost(), UnknownCostBehavior

forge/mcp/              # Optional, phase 2
    __init__.py
    server.py           # FastMCP server for Forge-specific tools
```

### 18.2 Modified Files

```
forge/core/sdk_helpers.py       # Gutted — logic moves to providers/claude.py
                                # File kept as thin re-export for any external consumers
forge/agents/adapter.py         # AgentAdapter.run() takes ProviderRegistry, uses provider.execute()
forge/core/model_router.py      # select_model() returns ModelSpec, table uses provider:model values
forge/core/cost_estimator.py    # Delegates to CostRegistry
forge/core/daemon.py            # Creates ProviderRegistry, passes to components
forge/core/daemon_executor.py   # Uses ModelSpec + provider.execute() instead of direct SDK calls
forge/core/daemon_helpers.py    # _extract_text/_extract_activity take ProviderEvent
forge/core/claude_planner.py    # Uses provider.execute() instead of direct SDK calls
forge/core/planning/unified_planner.py  # Same
forge/core/contract_builder.py  # Same
forge/review/llm_review.py      # Same
forge/review/synthesizer.py     # Same
forge/core/ci_watcher.py        # Same
forge/core/followup.py          # Same + message handling migration
forge/core/preflight.py         # Updated provider health checks
forge/learning/guard.py         # RuntimeGuard.inspect() migrated to ProviderEvent
forge/agents/runtime.py         # run_with_retry wraps provider.execute()
forge/config/settings.py        # New fields: openai_enabled, per-stage overrides
forge/config/project_config.py  # Validation via registry.validate_model()
forge/cli/main.py               # New CLI flags: --provider, --planner, --agent, --reviewer
forge/api/routes/settings.py    # Updated response with provider info
forge/api/routes/tasks.py       # Pass provider registry through
forge/storage/db.py             # New columns: tasks.provider, pipelines.provider_config
```

### 18.3 Unchanged Files

```
forge/core/daemon_merge.py      # Pure git operations
forge/merge/                    # Worktree + git operations
forge/storage/ (schema aside)   # Query layer unchanged
forge/learning/                 # Model-agnostic lesson system
forge/api/ws/                   # WebSocket layer consumes plain strings
forge/web/                      # Frontend consumes API responses
```

## 19. Test Strategy

### 19.1 Principle

Every existing test passes on day one with zero changes to test logic. The refactor is structural, not behavioral.

### 19.2 How Existing Tests Continue to Pass

- `ClaudeProvider` wraps the exact same code path as current `sdk_query()`.
- `ModelSpec.parse("sonnet")` returns `ModelSpec(provider="claude", model="sonnet")`.
- `select_model()` returns `ModelSpec` — tests update assertions mechanically.
- Tests that mock `sdk_query()` now mock `ClaudeProvider.execute()`.

### 19.3 New Test Files

```
forge/providers/base_test.py            # ModelSpec, ModelDescriptor, ProviderResult, ProviderEvent
forge/providers/registry_test.py        # Register, get, validate, stage validation, descriptor indexing
forge/providers/claude_test.py          # Execute, safety boundary translation, event conversion
forge/providers/openai_test.py          # Execute, sandbox config, event conversion, resume
forge/providers/safety_auditor_test.py  # Tool policy enforcement across all policy modes
                                        # CRITICAL: must test that blocked operations are blocked
                                        # identically for both providers
forge/core/cost_registry_test.py        # Rate lookup, calculation, legacy migration,
                                        # unknown cost behavior (BLOCK/ESTIMATE_HIGH/ALLOW)
```

### 19.3.1 Safety Auditor Test Cases (Required)

These tests must pass before the spec can be considered implemented:

```python
# Identical behavior regardless of which provider emitted the event
def test_git_push_blocked_from_claude_event(): ...
def test_git_push_blocked_from_openai_event(): ...
def test_allowlist_mode_blocks_unlisted_tools(): ...
def test_denylist_mode_allows_unlisted_tools(): ...
def test_unrestricted_mode_allows_everything(): ...
```

### 19.4 Updated Test Files

```
forge/core/model_router_test.py     # 147 assertions updated for ModelSpec return type
forge/core/cost_estimator_test.py   # Delegates to CostRegistry
forge/core/sdk_helpers_test.py      # Redirects to providers/claude_test.py or kept as integration
```

### 19.5 Integration Testing

Existing e2e test runs through `ClaudeProvider` with zero behavioral change. A second e2e variant uses `MockOpenAIProvider` returning canned responses — validates provider selection and event flow without requiring an OpenAI API key in CI.

### 19.6 CI Requirements

No OpenAI API key needed in CI. All OpenAI provider tests use mocks. Real provider calls happen only in manual e2e testing.

## 20. Migration Path

### 20.1 For Existing Users

- Upgrade Forge → zero behavior change. All defaults resolve to Claude. All bare model names work.
- To enable OpenAI: set `FORGE_OPENAI_ENABLED=true` and `OPENAI_API_KEY=sk-...`
- To use OpenAI for agents: set `FORGE_AGENT_MODEL_MEDIUM=openai:gpt-5.4` or use `--agent openai:gpt-5.4`

### 20.2 For Existing Configs

- `forge.toml` with `model = "sonnet"` continues to work.
- Environment variables like `FORGE_MODEL_STRATEGY=auto` continue to work.
- Cost rate fields like `FORGE_COST_RATE_SONNET_INPUT=0.003` continue to work (deprecated, migrated at load time).

### 20.3 For Existing API Consumers

- `PUT /api/settings` with `{"agent_model_medium": "opus"}` continues to work.
- New format `{"agent_model_medium": "openai:gpt-5.4"}` also accepted.
- No breaking API changes.

## 21. Implementation Phases

### Phase 1: Provider Layer + Agent Execution (~1.5 weeks)
- `forge/providers/` package (base types, ModelDescriptor, registry, ClaudeProvider, OpenAIProvider)
- `SafetyAuditor` implementation with full test coverage
- Refactor `sdk_helpers.py` → `providers/claude.py`
- Refactor `adapter.py` to use provider protocol with ExecutionMode, ToolPolicy, OutputContract
- Refactor `daemon.py` to create and pass registry
- Refactor `daemon_executor.py` to use ModelSpec + provider + model_history tracking
- Refactor `agents/runtime.py` retry wrapper
- Update `model_router.py` for ModelSpec returns + stage validation
- All existing tests pass

### Phase 2: Cost + Config + Router (~3-4 days)
- `cost_registry.py` implementation
- `ForgeSettings` new fields
- `forge.toml` routing section
- `project_config.py` validation refactor
- Legacy cost settings migration
- `estimate_pipeline_cost()` update

### Phase 3: Planner + Reviewer + Streaming Migration (~1.5 weeks)
- `claude_planner.py` → provider protocol
- `unified_planner.py` → provider protocol
- `contract_builder.py` → provider protocol
- `llm_review.py` → provider protocol
- `synthesizer.py` → provider protocol
- `ci_watcher.py` → provider protocol
- `followup.py` → provider protocol + message handling migration
- `daemon_helpers.py` → all message handlers migrated to ProviderEvent
- `daemon.py` → `_on_planner_msg()` and `_on_unified_msg()` migrated
- `daemon_executor.py` → `_on_msg()` migrated
- `daemon_review.py` → `_make_review_on_message()` migrated
- `learning/guard.py` → `RuntimeGuard.inspect()` migrated to ProviderEvent
- Safety auditor integration tests: verify identical blocking behavior across providers

### Phase 4: CLI + API + Database (~3-4 days)
- CLI flags: `--provider`, `--planner`, `--agent`, `--reviewer`
- `GET /api/providers` endpoint
- Settings endpoint update
- DB schema additions (tasks.provider, pipelines.provider_config)
- `forge doctor` provider checks

### Phase 5: Web UI (~3-4 days)
- Settings page per-stage provider:model dropdowns
- Task detail provider label
- Pipeline summary provider config display

### Phase 6: Forge MCP Server (optional, ~3-4 days)
- FastMCP server with forge_ask_question, forge_check_scope, forge_get_lessons
- Per-agent stdio lifecycle
- Integration with provider.execute(mcp_servers=[...])
