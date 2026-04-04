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

### 4.2 ProviderCapabilities

Static declaration of what a provider can do. Queried at registration, not per-call.

```python
@dataclass
class ProviderCapabilities:
    can_use_tools: bool = True
    can_stream: bool = True
    can_resume_session: bool = True
    can_restrict_tools: bool = True
    can_run_shell: bool = True
    can_edit_files: bool = True
    supports_mcp_servers: bool = True
    max_context_tokens: int = 200_000
```

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

A single streaming event from any provider. Replaces duck-typed `AssistantMessage`/`ResultMessage` handling.

```python
@dataclass
class ProviderEvent:
    kind: str           # "text" | "tool_use" | "tool_result" | "status" | "error"
    text: str = ""
    tool_name: str = ""
    tool_input: dict | None = None
    raw: Any = None
```

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

Translation examples:
- `ClaudeProvider`: `"git:push"` → `"Bash(git push *)"` (Claude Code disallowed_tools syntax)
- `OpenAIProvider`: `"git:push"` → injected into developer message as explicit instruction + Codex sandbox config

These are not restrictions that hamper agents. Agents run with full bypass permissions — they can read/write any file, run any shell command, install packages, run tests, build. The safety boundary only prevents operations that should never happen autonomously in a worktree-scoped task (pushing to remote, escalating privileges, exfiltrating code via network).

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

The contract every provider implements. Single `execute()` method covers all use cases (agent execution, planning, review, contract building, follow-ups).

```python
class ProviderProtocol(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def available_models(self) -> list[str]: ...

    async def execute(
        self,
        *,
        prompt: str,
        system_prompt: str,
        model: str,
        cwd: str,
        max_turns: int,
        safety_boundary: SafetyBoundary,
        mcp_servers: list[MCPServerConfig] | None = None,
        resume: str | None = None,
        on_event: Callable[[ProviderEvent], Awaitable[None]] | None = None,
    ) -> ProviderResult: ...
```

Design decision: single `execute()` method, not separate methods per pipeline stage. All 7 current call sites (adapter, claude_planner, unified_planner, contract_builder, llm_review, synthesizer, followup) do the same thing: build system prompt, set tool restrictions, call SDK, get result. The differences are prompt content and tool configuration — both are parameters to `execute()`.

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
- Decide execution mode based on whether `cwd` points to a git worktree (Codex SDK for agent/CI-fix stages that work in worktrees) or not (Agents SDK for planner/reviewer/contract stages that don't modify files)
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
        self._settings = settings

    def register(self, provider: ProviderProtocol) -> None: ...
    def get(self, name: str) -> ProviderProtocol: ...
    def get_for_model(self, spec: ModelSpec) -> ProviderProtocol: ...
    def all_providers(self) -> list[ProviderProtocol]: ...
    def validate_model(self, spec: ModelSpec) -> bool: ...
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

Fallback chain: exact `provider:model` key → `provider:default` → zero with warning log. Unknown models report $0.00 and log a warning. Pipeline never stops due to missing cost rates.

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
sdk_query() yields messages → on_message callback → daemon_helpers extracts text/activity → WebSocket → UI
```

`_extract_text()` and `_extract_activity()` in `daemon_helpers.py` use `isinstance(message, AssistantMessage)` and `isinstance(message, ResultMessage)` — the only places Claude message types leak into the common harness.

### 10.2 New Flow

```
provider.execute() calls on_event(ProviderEvent) → daemon_helpers extracts text/activity → WebSocket → UI
```

Each provider converts its native messages to `ProviderEvent` internally. The harness only sees `ProviderEvent`.

### 10.3 Changes to daemon_helpers.py

```python
# Before:
def _extract_text(message) -> str:
    if isinstance(message, ResultMessage): return message.result
    if isinstance(message, AssistantMessage):
        return "".join(b.text for b in message.content if hasattr(b, "text"))

# After:
def _extract_text(event: ProviderEvent) -> str:
    if event.kind in ("text", "result"):
        return event.text
    return ""

# Before:
def _extract_activity(message) -> str:
    # checks hasattr(block, "name") for tool use

# After:
def _extract_activity(event: ProviderEvent) -> str:
    if event.kind == "tool_use":
        return f"Using {event.tool_name}"
    return ""
```

Two functions updated. Everything downstream (WebSocket emission, UI rendering) stays the same.

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
ALTER TABLE tasks ADD COLUMN provider TEXT DEFAULT 'claude';
ALTER TABLE pipelines ADD COLUMN provider_config TEXT;
```

- `tasks.provider`: which provider:model ran this task. For display in task detail view.
- `pipelines.provider_config`: JSON of per-stage provider:model resolved at pipeline creation. For audit trail.

### 13.2 No Other Schema Changes

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
      "models": ["opus", "sonnet", "haiku"],
      "capabilities": { "can_resume_session": true, ... }
    },
    {
      "name": "openai",
      "models": ["gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"],
      "capabilities": { "can_resume_session": true, ... }
    }
  ]
}
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

Add provider:model label to existing task card:

```
Model: claude:opus
```

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
    base.py             # Core types: ModelSpec, ProviderCapabilities, ProviderResult,
                        #   ProviderEvent, SafetyBoundary, MCPServerConfig, ProviderProtocol
    registry.py         # ProviderRegistry
    claude.py           # ClaudeProvider (extracted from sdk_helpers.py)
    openai.py           # OpenAIProvider
    restrictions.py     # AGENT_DENIED_OPERATIONS list + translation helpers

forge/core/
    cost_registry.py    # CostRegistry, ModelRates, resolve_cost(), _DEFAULT_RATES

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
forge/core/followup.py          # Same
forge/core/preflight.py         # Updated provider health checks
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
forge/providers/base_test.py        # ModelSpec, ProviderCapabilities, ProviderResult
forge/providers/registry_test.py    # Register, get, validate, error cases
forge/providers/claude_test.py      # Execute, safety boundary translation, event conversion
forge/providers/openai_test.py      # Execute, sandbox config, event conversion, resume
forge/core/cost_registry_test.py    # Rate lookup, calculation, legacy migration
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

### Phase 1: Provider Layer + Agent Execution (~1 week)
- `forge/providers/` package (base types, registry, ClaudeProvider, OpenAIProvider)
- Refactor `sdk_helpers.py` → `providers/claude.py`
- Refactor `adapter.py` to use provider protocol
- Refactor `daemon.py` to create and pass registry
- Refactor `daemon_executor.py` to use ModelSpec + provider
- Refactor `agents/runtime.py` retry wrapper
- Update `model_router.py` for ModelSpec returns
- All existing tests pass

### Phase 2: Cost + Config + Router (~3-4 days)
- `cost_registry.py` implementation
- `ForgeSettings` new fields
- `forge.toml` routing section
- `project_config.py` validation refactor
- Legacy cost settings migration
- `estimate_pipeline_cost()` update

### Phase 3: Planner + Reviewer + Remaining Call Sites (~1 week)
- `claude_planner.py` → provider protocol
- `unified_planner.py` → provider protocol
- `contract_builder.py` → provider protocol
- `llm_review.py` → provider protocol
- `synthesizer.py` → provider protocol
- `ci_watcher.py` → provider protocol
- `followup.py` → provider protocol
- `daemon_helpers.py` → ProviderEvent

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
