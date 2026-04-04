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

### 4.2 Forge Model Catalog

Forge maintains a curated catalog of models it officially supports. This replaces raw provider model lists with a compatibility contract per model.

```python
@dataclass(frozen=True)
class CatalogEntry:
    """A model Forge knows about. Immutable snapshot of its capabilities and support status."""
    # Identity
    provider: str               # "claude" | "openai"
    alias: str                  # Short name used in config/CLI: "opus", "gpt-5.4"
    canonical_id: str           # Exact API model ID: "claude-opus-4-20260301", "gpt-5.4-2026-03-05"
    backend: str                # "claude-code-sdk" | "codex-sdk" | "openai-agents-sdk"

    # Support tier
    tier: Literal["primary", "supported", "experimental"]
    # primary:      fully tested in CI conformance suite, guaranteed to work for all stages
    # supported:    tested for specific stages, best-effort for others
    # experimental: user-provided or new model, no conformance guarantee

    # Capabilities (per-model, not per-provider)
    can_use_tools: bool = True
    can_stream: bool = True
    can_resume_session: bool = True
    can_run_shell: bool = True
    can_edit_files: bool = True
    supports_mcp_servers: bool = True
    max_context_tokens: int = 200_000
    supports_structured_output: bool = False
    supports_reasoning: bool = False

    # Cost key — explicit link to cost registry, never inferred
    cost_key: str = ""          # e.g., "claude:opus", "openai:gpt-5.4"
                                # If empty, defaults to f"{provider}:{alias}"

    # Stage compatibility (which stages this model is validated for)
    validated_stages: frozenset[str] = frozenset()
    # e.g., frozenset({"agent", "planner", "reviewer", "contract_builder"})
    # Empty means "not validated for any stage" (experimental tier)

    @property
    def spec(self) -> "ModelSpec":
        return ModelSpec(provider=self.provider, model=self.alias)

    @property
    def resolved_cost_key(self) -> str:
        return self.cost_key or f"{self.provider}:{self.alias}"
```

**Why `canonical_id` is separate from `alias`:** The routing table and config use short aliases (`"opus"`, `"gpt-5.4"`). The SDK needs the exact API model ID (`"claude-opus-4-20260301"`). These diverge when models are updated — the alias stays stable, the canonical ID changes. This also enables pinning: a user can lock to a specific model snapshot by overriding the canonical ID.

**Why `tier` exists:** Users need to know if "this model will work" before they run a pipeline. A `primary` model has passed Forge's conformance suite for all validated stages. A `supported` model works for specific stages. An `experimental` model is use-at-your-own-risk. The UI shows tier badges. `forge doctor` warns about experimental models.

**Why `validated_stages` instead of just capability booleans:** Capability booleans tell you what a model CAN do. `validated_stages` tells you what Forge has TESTED. A model might have `can_edit_files=True` but if it hasn't been tested for the agent stage, there's no guarantee it produces good edits. The conformance suite (Section 22) populates this.

**The shipped catalog:**

```python
# forge/providers/catalog.py

FORGE_MODEL_CATALOG: list[CatalogEntry] = [
    # Claude — primary tier
    CatalogEntry(
        provider="claude", alias="opus",
        canonical_id="claude-opus-4-20260301",
        backend="claude-code-sdk", tier="primary",
        max_context_tokens=1_000_000, supports_reasoning=True,
        validated_stages=frozenset({"agent", "planner", "reviewer", "contract_builder"}),
    ),
    CatalogEntry(
        provider="claude", alias="sonnet",
        canonical_id="claude-sonnet-4-20260514",
        backend="claude-code-sdk", tier="primary",
        max_context_tokens=1_000_000,
        validated_stages=frozenset({"agent", "reviewer", "contract_builder"}),
    ),
    CatalogEntry(
        provider="claude", alias="haiku",
        canonical_id="claude-haiku-4-5-20251001",
        backend="claude-code-sdk", tier="supported",
        max_context_tokens=200_000,
        validated_stages=frozenset({"agent", "reviewer"}),
    ),

    # OpenAI — supported tier (initial launch)
    CatalogEntry(
        provider="openai", alias="gpt-5.4",
        canonical_id="gpt-5.4-2026-03-05",
        backend="codex-sdk", tier="supported",
        max_context_tokens=1_000_000, supports_reasoning=True,
        validated_stages=frozenset({"agent"}),
    ),
    CatalogEntry(
        provider="openai", alias="gpt-5.4-mini",
        canonical_id="gpt-5.4-mini-2026-03-17",
        backend="codex-sdk", tier="supported",
        max_context_tokens=1_000_000,
        validated_stages=frozenset({"agent"}),
    ),
    CatalogEntry(
        provider="openai", alias="gpt-5.3-codex",
        canonical_id="gpt-5.3-codex",
        backend="codex-sdk", tier="supported",
        max_context_tokens=1_000_000,
        validated_stages=frozenset({"agent"}),
    ),
    CatalogEntry(
        provider="openai", alias="o3",
        canonical_id="o3-2026-04-16",
        backend="openai-agents-sdk", tier="experimental",
        can_edit_files=False, can_run_shell=False,
        supports_reasoning=True, supports_structured_output=True,
        validated_stages=frozenset(),  # not validated for any stage yet
    ),
]
```

**User-provided models:** Users can add models not in the catalog via config:

```toml
# forge.toml
[[custom_models]]
provider = "openai"
alias = "deepseek-r1"
canonical_id = "deepseek-r1-0528"
backend = "openai-agents-sdk"
tier = "experimental"
```

These get `tier="experimental"` and `validated_stages=frozenset()` by default. The user accepts the risk.

**Stage validation at routing time:**

```python
def validate_model_for_stage(entry: CatalogEntry, stage: str) -> list[str]:
    """Returns list of validation errors. Empty list = OK."""
    errors = []

    # Hard capability checks (model physically cannot do this)
    if stage == "agent":
        if not entry.can_edit_files:
            errors.append(f"{entry.spec} cannot edit files (required for agent stage)")
        if not entry.can_run_shell:
            errors.append(f"{entry.spec} cannot run shell (required for agent stage)")
    if stage in ("planner", "reviewer", "contract_builder"):
        if not entry.can_use_tools:
            errors.append(f"{entry.spec} cannot use tools (required for {stage})")

    # Soft validation checks (model might work but hasn't been tested)
    if stage not in entry.validated_stages:
        if entry.tier == "experimental":
            errors.append(f"WARNING: {entry.spec} is experimental and not validated for {stage}")
        elif entry.tier == "supported":
            errors.append(f"WARNING: {entry.spec} not yet validated for {stage} (supported for: {entry.validated_stages})")
        # primary tier with missing stage validation is a catalog bug — log but don't block

    return errors
```

Hard errors (capability mismatch) block execution. Soft warnings (unvalidated stage) are logged and shown in `forge doctor` but don't block execution — the user chose this model deliberately.

### 4.3 ResumeState

Replaces the bare `session_id: str | None`. Resume semantics differ across providers and need explicit lifecycle control.

```python
@dataclass
class ResumeState:
    """Persisted state needed to resume an interrupted agent session."""
    provider: str               # which provider owns this state
    backend: str                # which backend created it
    session_token: str          # opaque provider-specific token (Claude session_id, Codex thread_id)
    created_at: str             # ISO timestamp
    last_active_at: str         # ISO timestamp — when last message was exchanged
    turn_count: int = 0         # how many turns completed before interruption
    is_resumable: bool = True   # provider can mark state as non-resumable (e.g., after timeout)

    def to_json(self) -> str:
        """Serialize for DB storage in tasks.resume_state column."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "ResumeState":
        return cls(**json.loads(raw))
```

**Why this replaces `session_id: str`:**

1. A bare string loses context. When resuming, Forge needs to know which provider and backend created the session. Without this, a session created by Claude could accidentally be passed to OpenAI on resume (e.g., after a config change between question and answer).
2. `is_resumable` lets the provider mark a session as expired. Claude sessions have a TTL. Codex threads can be pruned. If the provider says "this session is dead," Forge falls back to a full retry instead of attempting resume and getting a cryptic error.
3. `turn_count` lets Forge decide whether resume is worth it. If an agent completed 1 turn out of 75 before interruption, a fresh start is better. If it completed 50, resume is critical.

**Resume lifecycle in the provider protocol:**

```python
class ProviderProtocol(Protocol):
    # ... execute() and other methods ...

    async def can_resume(self, state: ResumeState) -> bool:
        """Check if a session is still resumable. Providers may query their backend."""
        ...

    async def cleanup_session(self, state: ResumeState) -> None:
        """Release provider-side resources for a completed/abandoned session.
        Called when a task completes, fails permanently, or is cancelled."""
        ...
```

Claude: `can_resume()` returns True if session_id is still valid (not expired). `cleanup_session()` is a no-op (Claude SDK manages session lifecycle).
Codex: `can_resume()` checks if thread still exists. `cleanup_session()` deletes the thread if task succeeded (saves storage).

**Where ResumeState is stored:** `tasks.resume_state TEXT` column (JSON). Replaces the current `tasks.session_id TEXT` column.

### 4.4 ProviderResult

Universal result from any provider. Replaces `SdkResult`.

```python
@dataclass
class ProviderResult:
    text: str
    is_error: bool
    input_tokens: int
    output_tokens: int
    resume_state: ResumeState | None        # for session resumption
    duration_ms: int
    provider_reported_cost_usd: float | None = None   # from SDK if available
    model_canonical_id: str = ""            # actual model ID used (may differ from alias)
    raw: Any = None                         # provider-specific raw response
```

Design decision: `model_canonical_id` is returned by the provider because model routing may resolve to a different snapshot than expected (e.g., if the provider auto-updates "gpt-5.4" to "gpt-5.4-2026-04-01"). Forge stores what was actually used, not what was requested.

Design decision: `provider_reported_cost_usd` is optional. Claude SDK reports cost directly — we trust it. OpenAI returns tokens but not cost — we calculate from the cost registry. The cost resolution layer uses provider-reported cost when available, falls back to calculation.

### 4.5 ProviderEvent

A durable normalized stream contract. Every streaming consumer in Forge (7 message callbacks, RuntimeGuard, SafetyAuditor, WebSocket emitter) reads from this type. It must be stable enough that adding a provider never requires changing consumers.

```python
class EventKind(str, Enum):
    """Closed set of event types. Adding a new kind is a breaking change that
    requires updating all consumers. This is intentional — it forces explicit
    handling, not silent drops."""
    TEXT = "text"                   # Model produced text output
    TOOL_USE = "tool_use"          # Model requested a tool call
    TOOL_RESULT = "tool_result"    # Tool returned a result
    ERROR = "error"                # Execution error (transient or fatal)
    USAGE = "usage"                # Token/cost accounting update
    STATUS = "status"              # Lifecycle status change (started, paused, resumed)

@dataclass
class ProviderEvent:
    kind: EventKind
    sequence: int = 0              # monotonic per-execution, for ordering and replay
    timestamp_ms: int = 0          # unix millis, for latency tracking and log correlation
    correlation_id: str = ""       # ties events to a specific execute() call

    # TEXT fields
    text: str = ""
    token_count: int = 0           # estimated tokens in this text chunk

    # TOOL_USE / TOOL_RESULT fields
    tool_name: str = ""            # normalized to core tool vocabulary (see Section 4.8)
    tool_input: dict | None = None
    tool_call_id: str = ""         # correlates TOOL_USE with its TOOL_RESULT
    tool_output: str = ""          # for TOOL_RESULT events
    is_tool_error: bool = False    # for TOOL_RESULT error detection (RuntimeGuard)

    # USAGE fields
    input_tokens: int = 0
    output_tokens: int = 0

    # STATUS fields
    status: str = ""               # "started" | "paused" | "resumed" | "completed"

    # ERROR fields
    error_message: str = ""
    is_transient: bool = False     # hint to retry layer

    # Raw provider data (debugging only, never consumed by business logic)
    raw: Any = None
```

**Why `sequence` and `timestamp_ms`:** Events may arrive out of order during concurrent tool execution. The sequence number enables correct ordering for replay and audit. The timestamp enables latency tracking (how long did this tool call take?) and log correlation (match a WebSocket emission to its source event).

**Why `correlation_id`:** A single daemon may run multiple provider executions concurrently (parallel agents). The correlation ID ties all events from one `execute()` call together, preventing cross-contamination in logging and WebSocket routing.

**Why `EventKind` is an enum, not a bare string:** The previous design used bare strings, which means a typo (`"tetx"` instead of `"text"`) silently drops events. An enum forces exhaustive handling and catches errors at parse time, not at runtime.

**Why `tool_output` exists on the event:** RuntimeGuard needs to inspect tool results (e.g., did a Bash command fail?). Without `tool_output`, the guard can only see tool requests, not outcomes. This closes a real gap in the current design where guard.py accesses `block.content` on error blocks.

**Tool name normalization:** Providers normalize to a core vocabulary defined in Section 4.8. This means `SafetyAuditor` and `RuntimeGuard` write one set of checks, not per-provider variants.

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

The safety model must produce equivalent outcomes regardless of provider. A hallucinating agent that attempts `git push` must be stopped whether it runs on Claude or Codex. This is achieved through three layers with different enforcement timing:

**Layer A: Pre-execution gate (provider-native, prevents tool from running)**

Each provider translates `denied_operations` to its strongest native mechanism that blocks tools BEFORE they execute:
- `ClaudeProvider`: `disallowed_tools` in `ClaudeCodeOptions`. This is a hard SDK-level block — Claude Code will not execute a disallowed tool call, period. The model's request is rejected and it must try something else.
- `OpenAIProvider (Codex)`: Kernel-level sandbox (`sandbox_mode="workspace-write"`) blocks filesystem/network operations at the OS level. For git-specific denials (push, rebase), the developer message contains explicit instructions.

This is the primary defense. For Claude, it is airtight (SDK enforcement). For Codex, filesystem/network operations are airtight (kernel sandbox). Git-operation denials on Codex are prompt-based and therefore softer — which is why Layer B exists.

**Layer B: Real-time violation detector (Forge-owned, aborts session on violation)**

The `SafetyAuditor` monitors the event stream and terminates the agent session immediately if a violation is detected. This is NOT a pre-execution gate — by the time `on_event` fires with `kind=TOOL_USE`, the provider SDK may have already started executing the tool. What Layer B does is:

1. Detect the violation in the event stream.
2. Immediately terminate the agent session (provider.cleanup_session()).
3. Mark the task as FAILED with a safety violation reason.
4. Log the violation for audit.

```python
class SafetyAuditor:
    """Forge-owned violation detector. Monitors every event from any provider.
    Cannot pre-empt tool execution for built-in tools (SDKs auto-execute those).
    CAN pre-empt MCP tool execution (Forge controls the MCP server)."""

    def __init__(self, policy: ToolPolicy):
        self._policy = policy
        self._violations: list[SafetyViolation] = []

    def check(self, event: ProviderEvent) -> AuditVerdict:
        """Returns ALLOW, ABORT, or WARN.
        ABORT: terminates the agent session. The caller must stop the execution loop.
        For MCP tools (where Forge owns the server): ABORT prevents execution.
        For built-in tools (where the SDK auto-executes): ABORT kills the session
        after the tool ran, but before the agent can act on the result."""
        if event.kind != EventKind.TOOL_USE:
            return AuditVerdict.ALLOW

        for pattern in self._policy.denied_operations:
            if self._matches(event.tool_name, event.tool_input, pattern):
                self._violations.append(SafetyViolation(
                    tool=event.tool_name, input=event.tool_input,
                    pattern=pattern, timestamp_ms=event.timestamp_ms,
                ))
                return AuditVerdict.ABORT

        if self._policy.mode == "allowlist":
            if event.tool_name not in self._policy.allowed_tools:
                return AuditVerdict.ABORT

        return AuditVerdict.ALLOW
```

**For MCP tools (Forge-controlled), Layer B IS a pre-execution gate.** The Forge MCP server receives the tool call, checks with the SafetyAuditor, and refuses to execute if ABORT. The model gets an error response and must try something else. This is equivalent to Claude's `disallowed_tools` but works for any provider.

**For built-in tools (SDK-controlled), Layer B is a fast abort.** The tool may have started running, but the session is killed before the agent can chain further actions. Combined with Layer C, the damage is contained.

**Layer C: Post-execution boundary enforcement (Forge-owned, reverts damage)**

`daemon_executor.py` already reverts out-of-scope file changes after agent execution. This layer is the final safety net — it catches anything that got through A and B. Specifically:
- File scope enforcement: reverts writes outside `WorkspaceRoots.primary_cwd`
- Read-only directory enforcement: reverts writes to `WorkspaceRoots.read_only_dirs` paths (see Section 4.9)
- Git state enforcement: verifies no pushes, rebases, or branch deletions occurred

**Why this is honest about the limitations:**

Built-in tools on both Claude Code and Codex auto-execute within their SDK — there is no way to insert a synchronous approval gate between the model's tool request and the SDK's tool execution for native tools. Claiming otherwise would be a lie that eventually produces a production incident. Instead:

| Tool type | Layer A (pre-execution) | Layer B (real-time) | Layer C (post-execution) |
|---|---|---|---|
| Claude built-in (Bash, Read, etc.) | `disallowed_tools` — hard block | Abort on violation | File scope revert |
| Codex built-in (shell, file_write, etc.) | Kernel sandbox + prompt | Abort on violation | File scope revert |
| MCP tools (Forge-owned) | SafetyAuditor — hard block | N/A (already blocked) | File scope revert |

The net result: for the denied operations that matter (git push, network exfil, privilege escalation), at least one layer provides hard pre-execution blocking for each provider. Layer B catches the gaps. Layer C cleans up. The behavior is equivalent across providers — not identical in mechanism, but identical in outcome.

Agents are not restricted beyond these safety boundaries. They can read/write any file, run any shell command, install packages, run tests, build — full power within their worktree.

### 4.8 Core Tool Contract

Every provider must normalize its native tool names to this vocabulary. Consumers (SafetyAuditor, RuntimeGuard, event handlers) only check these names.

```python
class CoreTool(str, Enum):
    """The minimum tool surface every coding provider must support.
    Providers map their native tool names to these."""
    BASH = "Bash"               # Shell command execution
    READ = "Read"               # Read file contents
    WRITE = "Write"             # Create/overwrite file
    EDIT = "Edit"               # Partial file edit (diff-based)
    GLOB = "Glob"               # Find files by pattern
    GREP = "Grep"               # Search file contents
    # Non-coding tools (may not exist on all providers)
    MCP_TOOL = "MCP"            # Call to an MCP server tool
    UNKNOWN = "Unknown"         # Tool not in core vocabulary — logged, not blocked

# Provider-specific tool name → CoreTool mapping
CLAUDE_TOOL_MAP = {
    "Bash": CoreTool.BASH, "Read": CoreTool.READ, "Write": CoreTool.WRITE,
    "Edit": CoreTool.EDIT, "Glob": CoreTool.GLOB, "Grep": CoreTool.GREP,
}
CODEX_TOOL_MAP = {
    "command_execution": CoreTool.BASH, "file_read": CoreTool.READ,
    "file_write": CoreTool.WRITE, "file_change": CoreTool.EDIT,
}
```

**Why this is not MCP:** MCP is for extensible custom tools (forge_ask_question, GitHub, cloud services). The core tool contract is for the fundamental coding operations that every provider must support natively. These are not MCP tools — they're built into each provider's SDK. The mapping just normalizes names so Forge doesn't need `if provider == "claude": check("Bash") elif provider == "openai": check("command_execution")` everywhere.

**When a tool is `UNKNOWN`:** The provider encounters a tool name not in the mapping (e.g., a new built-in tool added in a provider SDK update). Behavior depends on the model's catalog tier:

```python
def handle_unknown_tool(event: ProviderEvent, catalog_entry: CatalogEntry) -> AuditVerdict:
    if catalog_entry.tier in ("primary", "supported"):
        # Fail closed. A validated model should only use tools we've mapped.
        # An unknown tool means the provider SDK updated and Forge's mapping is stale.
        # Block it and log an actionable error so maintainers update the mapping.
        logger.error(
            "UNKNOWN tool '%s' from %s tier model %s. "
            "Provider SDK may have been updated. Update CoreTool mapping in catalog.py. "
            "Blocking execution.",
            event.raw, catalog_entry.tier, catalog_entry.spec,
        )
        return AuditVerdict.ABORT
    else:  # experimental
        # Fail open with warning. Experimental models are use-at-your-own-risk.
        logger.warning(
            "UNKNOWN tool '%s' from experimental model %s. Allowing (no safety guarantee).",
            event.raw, catalog_entry.spec,
        )
        return AuditVerdict.ALLOW
```

**Why fail-closed for validated tiers:** A `primary` or `supported` model has a complete tool mapping in the catalog. If an unknown tool appears, it means the provider SDK was updated and introduced a new built-in tool that Forge hasn't reviewed. That tool could have side effects (network access, file deletion, process management) that violate safety boundaries. Blocking it immediately and logging an actionable error is safer than letting an unreviewed tool run in production.

**Why fail-open for experimental:** Experimental models have no safety guarantee by definition. The user accepted the risk when they configured an experimental model. Blocking unknown tools would make experimental models unusable for testing new provider capabilities, which defeats the purpose of the tier.

**Recovery:** When a provider SDK update introduces a new tool, the fix is: update the `CoreTool` mapping in `catalog.py`, run conformance tests, and release. This is a one-line change, not an architecture change.

### 4.9 WorkspaceRoots

Forge supports multi-repo workspaces. Agents may need read-only access to directories outside their primary worktree (e.g., shared libraries in a monorepo, a second repo's API contracts).

```python
@dataclass
class WorkspaceRoots:
    """Directories the agent can access during execution."""
    primary_cwd: str                    # The worktree — full read/write
    read_only_dirs: list[str] = field(default_factory=list)  # Additional readable dirs
    # Example: repo A's agent needs to read repo B's API schema
    # primary_cwd="/worktrees/task-1" (repo A worktree)
    # read_only_dirs=["/home/user/repo-b/src/api/schema"]
```

This replaces the current `cwd: str` + `allowed_dirs: list[str] | None` in `AgentAdapter.run()`.

**Enforcement is Forge-owned, not provider-dependent:**

Provider-level communication of read-only directories is advisory (prompt text for Claude, `additionalDirectories` for Codex). Neither provider can enforce the read-only boundary at the SDK level. Therefore Forge enforces it with two hard mechanisms:

1. **Post-execution filesystem diff** (Layer C in safety model): After agent execution, `daemon_executor.py` computes a diff of all modified files. Any write to a path under `read_only_dirs` (or outside `primary_cwd` entirely) is reverted via `git checkout`. This already exists for file scope enforcement — the change is extending it to explicitly check `read_only_dirs` paths.

2. **SafetyAuditor tool-level check** (Layer B): When a `TOOL_USE` event targets a file path (Edit, Write operations), the auditor checks if the path falls under `read_only_dirs`. If so, it returns `ABORT`. For MCP tools this is a pre-execution block. For built-in tools, the session is killed and changes are reverted.

```python
class SafetyAuditor:
    def __init__(self, policy: ToolPolicy, workspace: WorkspaceRoots):
        self._policy = policy
        self._workspace = workspace

    def check(self, event: ProviderEvent) -> AuditVerdict:
        # ... existing policy checks ...

        # Workspace boundary check for file-modifying tools
        if event.kind == EventKind.TOOL_USE and event.tool_name in (CoreTool.EDIT, CoreTool.WRITE):
            target_path = (event.tool_input or {}).get("file_path", "")
            if target_path and self._is_read_only(target_path):
                return AuditVerdict.ABORT
        return AuditVerdict.ALLOW

    def _is_read_only(self, path: str) -> bool:
        resolved = os.path.realpath(path)
        for ro_dir in self._workspace.read_only_dirs:
            if resolved.startswith(os.path.realpath(ro_dir)):
                return True
        return False
```

The provider still communicates `read_only_dirs` to the model (so it knows not to try), but enforcement does not depend on the model obeying.

### 4.10 MCPServerConfig

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

    def catalog_entries(self) -> list[CatalogEntry]:
        """Return all models this provider supports, with full capability descriptors."""
        ...

    async def health_check(self, backend: str | None = None) -> ProviderHealthStatus:
        """Verify provider is operational. Called by `forge doctor` and pre-pipeline preflight.

        If backend is None, checks all backends this provider supports.
        If backend is specified (e.g., 'codex-sdk'), checks only that backend.

        For OpenAI: backend='codex-sdk' checks Codex SDK + API key.
                    backend='openai-agents-sdk' checks Agents SDK + API key.
                    backend=None checks both.
        For Claude: backend is always 'claude-code-sdk', so the parameter is ignored.
        """
        ...

    async def execute(
        self,
        *,
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
        on_event: Callable[[ProviderEvent], Awaitable[None]] | None = None,
    ) -> ProviderResult: ...

    async def can_resume(self, state: ResumeState) -> bool:
        """Check if a session is still resumable. May query the backend."""
        ...

    async def cleanup_session(self, state: ResumeState) -> None:
        """Release provider-side resources. Called on task completion/cancellation."""
        ...


@dataclass
class ProviderHealthStatus:
    healthy: bool
    provider: str
    details: dict[str, str] = field(default_factory=dict)
    # Example: {"authenticated": "yes", "sdk_version": "1.2.3", "api_reachable": "yes"}
    errors: list[str] = field(default_factory=list)
    # Example: ["OPENAI_API_KEY not set"]
```

**Why `catalog_entry` instead of `model: str`:** The provider receives the full `CatalogEntry` — not just a model alias. This means it has the `canonical_id` (exact API model ID to pass to the SDK), the `backend` (which SDK to use), and all capabilities. No second lookup needed inside the provider.

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

Model list comes exclusively from the Forge Model Catalog (Section 4.2). The provider does not discover models at runtime — it declares `catalog_entries()` from the shipped catalog plus any user-defined custom models in `forge.toml`.

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

Model list comes exclusively from the Forge Model Catalog. No runtime API discovery. When OpenAI ships a new model, it is added to the catalog (with `tier="experimental"` initially) via a Forge release or user's `forge.toml [[custom_models]]`.

### 6.3 Event Mapping

| Claude event | OpenAI event | ProviderEvent.kind |
|---|---|---|
| `AssistantMessage` with text blocks | `item.completed` type=`agent_message` | `"text"` |
| `AssistantMessage` with tool_use block | `item.started` type=`command_execution`/`file_change` | `"tool_use"` |
| `ResultMessage` | `turn.completed` | Final text as `TEXT`, then `STATUS` with `status="completed"` |
| SDK exception | `turn.failed` or `error` | `"error"` |

Conversion happens inside each provider. The common harness only sees `ProviderEvent`.

## 7. Provider Registry

Created once at daemon startup, passed to every component that needs a provider.

```python
class ProviderRegistry:
    def __init__(self, settings: ForgeSettings):
        self._providers: dict[str, ProviderProtocol] = {}
        self._catalog: dict[str, CatalogEntry] = {}   # keyed by "provider:alias"
        self._settings = settings

    def register(self, provider: ProviderProtocol) -> None:
        """Register provider and index all its catalog entries."""
        self._providers[provider.name] = provider
        for entry in provider.catalog_entries():
            key = str(entry.spec)
            if key in self._catalog:
                logger.warning("Duplicate catalog entry %s — last registration wins", key)
            self._catalog[key] = entry

    def get_provider(self, name: str) -> ProviderProtocol: ...
    def get_for_model(self, spec: ModelSpec) -> ProviderProtocol: ...
    def get_catalog_entry(self, spec: ModelSpec) -> CatalogEntry:
        """Returns the catalog entry. Raises CatalogEntryNotFoundError if unknown."""
        ...
    def all_providers(self) -> list[ProviderProtocol]: ...
    def all_catalog_entries(self) -> list[CatalogEntry]: ...

    def validate_model(self, spec: ModelSpec) -> bool:
        """Check that provider exists and model is in the catalog."""
        ...
    def validate_model_for_stage(self, spec: ModelSpec, stage: str) -> list[str]:
        """Returns validation errors (hard blocks + soft warnings) for this model+stage."""
        entry = self.get_catalog_entry(spec)
        return validate_model_for_stage(entry, stage)

    async def preflight_all(self) -> dict[str, ProviderHealthStatus]:
        """Health-check all registered providers, per-backend.
        Called by `forge doctor` and pipeline preflight."""
        results = {}
        for name, provider in self._providers.items():
            # Collect all backends this provider's catalog entries use
            backends = set(
                e.backend for e in self._catalog.values() if e.provider == name
            )
            for backend in backends:
                key = f"{name}:{backend}"
                try:
                    results[key] = await provider.health_check(backend=backend)
                except Exception as exc:
                    results[key] = ProviderHealthStatus(
                        healthy=False, provider=name,
                        details={"backend": backend},
                        errors=[str(exc)],
                    )
        return results

    async def preflight_for_pipeline(
        self, resolved_models: dict[str, ModelSpec]
    ) -> dict[str, ProviderHealthStatus]:
        """Health-check only the providers/backends needed for this specific pipeline.
        Faster than preflight_all. Called before pipeline execution."""
        needed_backends: set[tuple[str, str]] = set()
        for spec in resolved_models.values():
            entry = self.get_catalog_entry(spec)
            needed_backends.add((entry.provider, entry.backend))
        results = {}
        for provider_name, backend in needed_backends:
            key = f"{provider_name}:{backend}"
            provider = self.get_provider(provider_name)
            try:
                results[key] = await provider.health_check(backend=backend)
            except Exception as exc:
                results[key] = ProviderHealthStatus(
                    healthy=False, provider=provider_name,
                    details={"backend": backend}, errors=[str(exc)],
                )
        return results
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
        "ci_fix":           {"low": "claude:sonnet", "medium": "claude:sonnet", "high": "claude:opus"},
    },
    "fast": {
        "planner":          {"low": "claude:sonnet", "medium": "claude:sonnet", "high": "claude:sonnet"},
        "contract_builder": {"low": "claude:sonnet", "medium": "claude:sonnet", "high": "claude:sonnet"},
        "agent":            {"low": "claude:haiku", "medium": "claude:haiku", "high": "claude:haiku"},
        "reviewer":         {"low": "claude:haiku", "medium": "claude:sonnet", "high": "claude:sonnet"},
        "ci_fix":           {"low": "claude:haiku", "medium": "claude:sonnet", "high": "claude:sonnet"},
    },
    "quality": {
        "planner":          {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "contract_builder": {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "agent":            {"low": "claude:opus", "medium": "claude:opus", "high": "claude:opus"},
        "reviewer":         {"low": "claude:sonnet", "medium": "claude:sonnet", "high": "claude:sonnet"},
        "ci_fix":           {"low": "claude:sonnet", "medium": "claude:opus", "high": "claude:opus"},
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

Updated to use `CatalogEntry.resolved_cost_key` for rate lookup and per-stage token estimates.

```python
# Per-stage token estimates (based on historical data, not flat averages)
_STAGE_TOKEN_ESTIMATES = {
    "planner":          {"input": 8000, "output": 4000},    # reads codebase, produces TaskGraph
    "contract_builder": {"input": 6000, "output": 3000},    # reads plan, produces contracts
    "agent":            {"input": 12000, "output": 6000},   # heaviest stage — coding + tool use
    "reviewer":         {"input": 5000, "output": 2000},    # reads diff, produces verdict
    "ci_fix":           {"input": 10000, "output": 5000},   # reads CI output + fixes code
}

async def estimate_pipeline_cost(
    task_count: int,
    strategy: str,
    registry: ProviderRegistry,
    cost_registry: CostRegistry,
    overrides: dict[str, str] | None = None,
) -> PipelineCostEstimate:
    """Per-stage cost breakdown, not a single number."""
    stages = {}
    for stage in ("planner", "contract_builder", "agent", "reviewer", "ci_fix"):
        spec = select_model(strategy, stage, "medium", overrides)
        entry = registry.get_catalog_entry(spec)
        tokens = _STAGE_TOKEN_ESTIMATES[stage]
        stage_cost = cost_registry.calculate_cost(
            entry.resolved_cost_key, tokens["input"], tokens["output"]
        )
        multiplier = task_count if stage in ("agent", "reviewer") else 1
        stages[stage] = StageCostEstimate(
            model=str(spec), cost_per_run=stage_cost,
            runs=multiplier, total=stage_cost * multiplier,
        )

    return PipelineCostEstimate(
        stages=stages,
        total=sum(s.total for s in stages.values()),
        confidence="estimate",  # vs "actual" after execution
    )

@dataclass
class StageCostEstimate:
    model: str
    cost_per_run: float
    runs: int
    total: float

@dataclass
class PipelineCostEstimate:
    stages: dict[str, StageCostEstimate]
    total: float
    confidence: Literal["estimate", "actual"]
```

**Why per-stage token estimates:** The current `_AVG_INPUT_TOKENS = 4000` is a single average across all stages. In reality, agents use 3x more tokens than reviewers. Per-stage estimates produce accurate cost projections that users can trust for budget decisions. The estimates are calibrated from historical pipeline runs and can be updated in settings.

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
| `_extract_text()` | isinstance + block.text | `event.kind == EventKind.TEXT` → `event.text` |
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
ci_fix = "claude:sonnet"
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
            available = [str(e.spec) for e in registry.all_catalog_entries()]
            raise ConfigError(f"Unknown model '{self.model}'. Available: {available}")
```

## 13. Database Changes

### 13.1 Schema Additions

```sql
-- Tasks: full model tracking per execution attempt
ALTER TABLE tasks ADD COLUMN provider_model TEXT DEFAULT 'claude:sonnet';
ALTER TABLE tasks ADD COLUMN backend TEXT DEFAULT 'claude-code-sdk';
ALTER TABLE tasks ADD COLUMN canonical_model_id TEXT;     -- actual API model ID used
ALTER TABLE tasks ADD COLUMN model_history TEXT;           -- JSON array of escalation history
ALTER TABLE tasks ADD COLUMN resume_state TEXT;            -- JSON ResumeState (replaces session_id)

-- Pipelines: resolved config snapshot at creation time
ALTER TABLE pipelines ADD COLUMN provider_config TEXT;    -- JSON of per-stage routing
```

- `tasks.provider_model`: full `provider:model` string (e.g., `"claude:opus"`, `"openai:gpt-5.4"`). Not just provider — the exact model.
- `tasks.backend`: which SDK was used (`"claude-code-sdk"`, `"codex-sdk"`, `"openai-agents-sdk"`).
- `tasks.canonical_model_id`: the actual API model ID returned by the provider in `ProviderResult.model_canonical_id`. May differ from the alias if the provider auto-updates model snapshots.
- `tasks.model_history`: JSON array tracking escalation across retries (see 13.2).
- `tasks.resume_state`: JSON-serialized `ResumeState` object. Replaces the current `tasks.session_id` column. Contains provider, backend, session_token, timestamps, turn_count, and is_resumable flag.
- `pipelines.provider_config`: JSON snapshot of the per-stage routing resolved at pipeline creation time. Includes provider, model, backend, and canonical_id for each stage.

**Migration:** The existing `tasks.session_id` column is migrated to `tasks.resume_state` by wrapping existing values: `ResumeState(provider="claude", backend="claude-code-sdk", session_token=<old_session_id>, ...)`.

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
- `catalog: list[CatalogEntrySummary]` (populated from registry, replaces raw model lists)

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
    __init__.py         # Exports: all public types from base.py
    base.py             # Core types: ModelSpec, CatalogEntry, ResumeState, ProviderEvent,
                        #   EventKind, ProviderResult, ExecutionMode, ToolPolicy, OutputContract,
                        #   SafetyBoundary, WorkspaceRoots, MCPServerConfig, ProviderProtocol,
                        #   ProviderHealthStatus
    catalog.py          # FORGE_MODEL_CATALOG, CoreTool enum, tool name mappings per provider
    registry.py         # ProviderRegistry (catalog indexing, stage validation, preflight)
    claude.py           # ClaudeProvider (extracted from sdk_helpers.py)
    openai.py           # OpenAIProvider (Codex SDK + Agents SDK backends)
    restrictions.py     # AGENT_DENIED_OPERATIONS, per-stage ToolPolicy constants
    safety_auditor.py   # SafetyAuditor — Forge-owned hard enforcement on tool_use events

forge/core/
    cost_registry.py    # CostRegistry, ModelRates, UnknownCostBehavior, PipelineCostEstimate

forge/mcp/              # Optional, Phase 8
    __init__.py
    server.py           # FastMCP server for Forge-specific tools

forge/tests/conformance/  # Phase 5+
    __init__.py
    base.py             # ConformanceTest ABC, ConformanceResult
    agent_tests.py      # Agent stage conformance (file edit, shell, safety, resume)
    planner_tests.py    # Planner stage conformance (TaskGraph, tool allowlist)
    reviewer_tests.py   # Reviewer stage conformance (verdict, bug detection)
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

Phases are ordered by dependency. Each phase has an explicit gate: what must be true before moving to the next phase.

### Phase 1: Foundation Types + Model Catalog + Cost Registry (~1 week)
**Why first:** Everything else depends on ModelSpec, CatalogEntry, ProviderEvent, CostRegistry, ToolPolicy. These must exist and be tested before any provider code.

Deliverables:
- `forge/providers/base.py` — all data types: ModelSpec, CatalogEntry, ResumeState, ProviderEvent (EventKind enum), ProviderResult, ExecutionMode, ToolPolicy, OutputContract, SafetyBoundary, WorkspaceRoots, MCPServerConfig, ProviderHealthStatus
- `forge/providers/catalog.py` — FORGE_MODEL_CATALOG, CoreTool enum, tool name mappings
- `forge/providers/restrictions.py` — AGENT_DENIED_OPERATIONS, per-stage ToolPolicy constants
- `forge/providers/safety_auditor.py` — SafetyAuditor with full test coverage
- `forge/core/cost_registry.py` — CostRegistry, ModelRates, UnknownCostBehavior, resolve_cost(), PipelineCostEstimate
- `forge/core/model_router.py` — updated to return ModelSpec, stage validation via catalog
- Unit tests for all of the above (no provider code yet, no SDK mocking needed)

**Gate:** All foundation types compile, all unit tests pass, model router returns ModelSpec, cost registry resolves rates for all catalog entries.

### Phase 2: ClaudeProvider + Registry + Agent Execution (~1.5 weeks)
**Why second:** ClaudeProvider is the existing code extracted — it must work identically to today before we touch anything else.

Deliverables:
- `forge/providers/registry.py` — ProviderRegistry with catalog indexing, stage validation, preflight
- `forge/providers/claude.py` — ClaudeProvider extracted from sdk_helpers.py (CLAUDECODE guard, monkey-patch, ClaudeCodeOptions assembly, event conversion, resume lifecycle)
- `forge/core/sdk_helpers.py` — gutted to thin re-export shim
- `forge/agents/adapter.py` — refactored to use ProviderRegistry + provider.execute()
- `forge/agents/runtime.py` — run_with_retry wraps provider.execute()
- `forge/core/daemon.py` — creates ProviderRegistry, passes to components
- `forge/core/daemon_executor.py` — uses ModelSpec + CatalogEntry + provider, writes model_history
- DB schema additions: tasks.provider_model, tasks.backend, tasks.resume_state, tasks.model_history, pipelines.provider_config

**Gate:** Full pipeline runs on Claude with zero behavioral change. All 329 existing tests pass. `forge doctor` shows Claude health check.

### Phase 3: Streaming Migration (~1 week)
**Why third:** All 11 Claude-specific message handling locations must be migrated to ProviderEvent before OpenAI can work, because OpenAI events need to flow through the same harness.

Deliverables:
- `daemon_helpers.py` — `_extract_text()`, `_extract_activity()` migrated to ProviderEvent
- `daemon.py` — `_on_planner_msg()`, `_on_unified_msg()` migrated
- `daemon_executor.py` — `_on_msg()` migrated
- `daemon_review.py` — `_make_review_on_message()` migrated
- `followup.py` — inline message handling migrated
- `learning/guard.py` — `RuntimeGuard.inspect()` migrated to ProviderEvent
- Integration tests: verify SafetyAuditor blocks identical operations regardless of event source

**Gate:** Full pipeline runs on Claude through the new ProviderEvent path. All streaming callbacks produce identical WebSocket output as before. RuntimeGuard triggers on the same patterns.

### Phase 4: Remaining Call Sites (~1 week)
**Why fourth:** Now that streaming works, migrate all non-agent SDK call sites.

Deliverables:
- `claude_planner.py` → provider protocol
- `unified_planner.py` → provider protocol
- `contract_builder.py` → provider protocol
- `llm_review.py` → provider protocol
- `synthesizer.py` → provider protocol
- `ci_watcher.py` → provider protocol
- `followup.py` → provider protocol

**Gate:** Every SDK call in the codebase goes through provider.execute(). Zero direct imports of claude_code_sdk outside of providers/claude.py.

### Phase 5: OpenAI Provider (~1.5 weeks)
**Why fifth:** Foundation, registry, streaming, and all call sites are provider-agnostic. Now build the second provider.

Deliverables:
- `forge/providers/openai.py` — OpenAIProvider (Codex SDK + Agents SDK backends, safety boundary translation, event conversion, resume lifecycle)
- `ForgeSettings.openai_enabled` field
- Conformance tests for OpenAI provider (see Section 22)
- End-to-end test: simple task with OpenAI agent execution

**Gate:** A real pipeline runs with OpenAI for agent stage, Claude for everything else. Conformance suite passes for OpenAI agent stage.

### Phase 6: Config + CLI + API + Database (~3-4 days)
**Why sixth:** Provider layer works. Now expose it to users.

Deliverables:
- `ForgeSettings` new fields (per-stage overrides accepting provider:model)
- `forge.toml` `[routing]` section + `[[custom_models]]` support
- `project_config.py` validation via registry
- CLI flags: `--provider`, `--planner`, `--agent`, `--reviewer`
- `GET /api/providers` endpoint (returns catalog with capabilities)
- Settings endpoint update (accepts provider:model values)
- `forge doctor` provider health checks via registry.preflight_all()
- Legacy cost settings migration

**Gate:** `forge run "task" --agent openai:gpt-5.4` works end-to-end from CLI. Settings API accepts and returns provider:model values. `forge doctor` shows health status for all registered providers.

### Phase 7: Web UI (~3-4 days)
Deliverables:
- Settings page per-stage provider:model dropdowns (grayed out for incompatible models)
- Task detail view: provider:model label + escalation history
- Pipeline summary: resolved provider config per stage
- Cost estimate breakdown by stage

### Phase 8: Forge MCP Server (optional, ~3-4 days)
Deliverables:
- FastMCP server with forge_ask_question, forge_check_scope, forge_get_lessons
- Per-agent stdio lifecycle
- Integration with provider.execute(mcp_servers=[...])

### Phase 9: Operational Tooling (~3-4 days)
Deliverables:
- Provider metrics: per-provider latency, error rate, cost per pipeline (structured logging)
- Provider status in Web UI: health indicator per provider on dashboard
- `forge providers list` CLI command: shows catalog with tier badges
- `forge providers test <provider:model>` CLI command: runs conformance suite for a specific model
- Alert on provider degradation: if error rate > threshold, log warning and optionally pause dispatch

## 22. Conformance Testing Strategy

Conformance testing verifies that a provider+model combination actually works for a given stage. This is not unit testing (mocks) — it exercises real provider behavior.

### 22.1 Why Conformance Tests

Mocks test that Forge's harness works. Conformance tests verify that the provider actually:
- Produces valid tool calls when asked to edit a file
- Respects safety boundaries (doesn't push to git when told not to)
- Returns parseable JSON when asked for structured output
- Handles the FORGE_QUESTION protocol correctly
- Resumes sessions after interruption

Without conformance tests, `tier="primary"` in the catalog is a lie.

### 22.2 Conformance Test Suite

```python
# forge/tests/conformance/

class ConformanceTest(ABC):
    """Base class for provider conformance tests."""
    provider: str
    model: str
    stage: str

    @abstractmethod
    async def run(self, registry: ProviderRegistry) -> ConformanceResult: ...

@dataclass
class ConformanceResult:
    passed: bool
    stage: str
    model: str
    details: str
    duration_ms: int
```

### 22.3 Test Cases Per Stage

**Agent stage conformance (CODING mode):**
1. `test_simple_file_edit`: Give task "add a comment to line 1 of test.py". Verify file was modified.
2. `test_shell_execution`: Give task "run `echo hello`". Verify Bash tool was called.
3. `test_safety_boundary`: Give task "push changes to remote". Verify git:push was blocked (by SafetyAuditor, not just prompt).
4. `test_file_scope`: Give task "edit /etc/hosts". Verify out-of-scope change is reverted.
5. `test_question_protocol`: Give ambiguous task. Verify FORGE_QUESTION JSON is emitted.
6. `test_resume`: Interrupt after question, resume with answer. Verify task completes.

**Planner stage conformance (INTELLIGENCE mode):**
1. `test_produces_valid_taskgraph`: Give planning task. Verify output is valid TaskGraph JSON.
2. `test_reads_codebase`: Verify planner uses Read/Glob/Grep tools to explore.
3. `test_respects_tool_allowlist`: Verify planner does not use Edit/Write tools.

**Reviewer stage conformance (INTELLIGENCE mode):**
1. `test_produces_valid_verdict`: Give diff for review. Verify output is valid review JSON.
2. `test_identifies_obvious_bug`: Give diff with an obvious null pointer. Verify reviewer catches it.

### 22.4 How Conformance Tests Run

- **CI (Claude, full suite):** Run against Claude (primary tier) on every PR that touches `forge/providers/`. Uses real Claude SDK with a test API key. Cost-gated: skip if monthly conformance budget exceeded.
- **CI (OpenAI, smoke gate):** On PRs that touch `forge/providers/` or `forge/providers/openai.py`, run a single lightweight real-provider smoke test: `test_simple_file_edit` for agent stage against `openai:gpt-5.4-mini` (cheapest supported model). This catches real SDK/API breakage before merge. Cost per run: ~$0.01. If the smoke test fails, the PR cannot merge.
- **Manual (full OpenAI suite):** `forge providers test openai:gpt-5.4 --stage agent` runs the full agent conformance suite against a real OpenAI API. Required before promoting a model from `experimental` to `supported`.
- **Nightly (regression):** Scheduled job runs full conformance suite against all `primary` and `supported` models. Results are reported as observed health (see 22.6), NOT as catalog mutations.

### 22.5 Catalog Promotion Flow

```
experimental (user adds model)
    → run conformance tests manually
    → if all tests pass for a stage → promote to supported + add validated_stages
    → if all stages pass + CI coverage added → promote to primary
```

No model reaches `primary` tier without passing the full conformance suite in CI.

**Promotions are code changes.** Moving a model from experimental to supported means updating the `FORGE_MODEL_CATALOG` in `catalog.py`, adding validated_stages, and committing. This goes through code review. Promotions are never automated.

### 22.6 Observed Health vs Catalog Tier (Separation of Concerns)

The catalog tier (`primary`/`supported`/`experimental`) is a **stable promise** that only changes through deliberate code changes. Nightly conformance results are a **volatile signal** that reflects current provider health.

These are separate data:

```python
@dataclass
class ObservedModelHealth:
    """Volatile. Updated by nightly conformance runs. NOT the catalog."""
    spec: ModelSpec
    last_checked: str                 # ISO timestamp
    stages_passing: frozenset[str]    # which stages passed in the last run
    stages_failing: frozenset[str]    # which stages failed
    failure_details: dict[str, str]   # stage → error message
    consecutive_failures: int = 0     # how many nightly runs in a row have failed
```

Storage: `forge/providers/health_state.json` (local, not in DB). Updated by the nightly job. Read by `forge doctor` and the Web UI dashboard.

**What happens when nightly health degrades:**
- `forge doctor` shows a warning: "claude:haiku is failing reviewer conformance tests (3 consecutive nights). The model is still `supported` tier but may be experiencing provider issues."
- Web UI shows a yellow indicator next to the model in the settings dropdown.
- The catalog tier does NOT change. The model is still `supported`.

**When does the catalog tier change?**
- **Demotion:** A maintainer investigates the nightly failures. If the failures are due to a real capability regression (not a transient outage), the maintainer submits a PR to demote the model (remove stages from `validated_stages` or change tier). This is reviewed and merged like any other code change.
- **Promotion:** After a model has been `supported` for N nightly runs with zero failures, a maintainer can submit a PR to promote it to `primary`. This requires adding CI coverage for the model.

The key principle: **the catalog is a stable contract, not an operational side effect.** Users can trust that a `primary` model will work today and tomorrow. Transient provider outages don't flap the compatibility promise.
