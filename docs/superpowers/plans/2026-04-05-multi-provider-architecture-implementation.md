# Multi-Provider Architecture Implementation

Source design: `/Users/mtarun/Desktop/SideHustles/claude-does/docs/superpowers/specs/2026-04-04-multi-provider-architecture-design.md`

## Objective

- Implement the multi-provider architecture exactly as specified in the design document.
- Replace direct Claude SDK coupling with a provider layer built around catalog-driven model selection, normalized events, provider-owned execution, and provider-agnostic safety enforcement.
- Preserve current Claude behavior during migration, add OpenAI only behind explicit enablement, and avoid inventing any runtime behavior not stated in the design.

## Scope

- Add the provider package described by the design under `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/`.
- Migrate all current LLM execution paths to `ProviderProtocol.start(...)`.
- Replace raw Claude message consumption with `ProviderEvent`.
- Introduce provider-aware routing, cost tracking, database fields, API responses, CLI flags, UI selectors, and conformance tests.
- Keep backward compatibility for bare model aliases such as `"sonnet"` and `"opus"`.
- Keep paused legacy tasks resumable during migration.

## Non-Goals

- No provider beyond Claude and OpenAI.
- No raw provider model discovery outside the Forge catalog.
- No automatic cross-provider fallback.
- No launch dependency on the optional Forge MCP server.
- No same-release hard removal of legacy persistence fields until compatibility bridges are proven.

## Current State in Code

- There is no provider package today. `/Users/mtarun/Desktop/SideHustles/claude-does/forge/` has no `providers/` directory.
- Direct `claude_code_sdk` imports exist in:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/adapter.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/tasks.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/ci_watcher.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/claude_planner.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/contract_builder.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_helpers.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/followup.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/planning/unified_planner.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/review/llm_review.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/review/synthesizer.py`
- `sdk_query()` in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/sdk_helpers.py` is the current Claude execution seam and is still called from agent, planner, contract builder, follow-up, review, CI fix, branch naming, and PR title generation paths.
- Routing today is Claude-only in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/model_router.py`.
  - `select_model()` returns bare strings.
  - Supported stages are `planner`, `contract_builder`, `agent`, and `reviewer`.
  - `ci_fix` is not present.
  - Retry escalation exists only for `agent`.
- Agent execution today is Claude-specific in:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/adapter.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/runtime.py`
  - `AgentResult` carries `session_id`.
  - `AgentAdapter.run()` takes `model: str`, `allowed_dirs`, and `resume: str | None`.
- The current daemon execution and resume flow is:
  - task row stores `session_id` in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py` `_handle_agent_question()` writes `session_id`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py` `_resume_task()` reads it and passes `resume=session_id`
- Current streaming consumers inspect Claude-specific message shapes in:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_helpers.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_review.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/followup.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/learning/guard.py`
- Current safety behavior is incomplete:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/adapter.py` has a Claude-specific denylist.
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py` `_enforce_file_scope()` reverts some file changes after execution.
  - There is no provider-agnostic `SafetyAuditor`.
  - There is no read-only directory snapshot/restore path.
  - There is no full git metadata rollback plan for branch/head/remote/tag/stash changes.
- Persistence today lacks provider-layer fields:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db.py` `TaskRow` has `session_id`, not `resume_state`
  - `TaskRow` has no `provider_model`, `backend`, `canonical_model_id`, or `model_history`
  - `PipelineRow` has no `provider_config`
- Web settings are disconnected from execution:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/settings.py` stores per-user overrides
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/app.py` `daemon_factory()` builds `ForgeSettings()` from env and only applies `model_strategy`
  - Saved web settings do not currently affect new pipelines
- The web UI is Claude-only:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/app/settings/page.tsx` uses `MODEL_OPTIONS = ["opus", "sonnet", "haiku"]`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/stores/taskStore.ts` has no provider/backend/model-history fields
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/components/task/TaskDetailPanel.tsx` and `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/components/task/CompletionSummary.tsx` do not display provider data
- CLI and preflight are Claude-only:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/main.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/doctor.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/preflight.py`
- Packaging is Claude-only:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/pyproject.toml` includes `claude-code-sdk>=0.0.25`
  - no OpenAI provider dependency is present

## Design-to-Code Mapping

### Existing Files To Modify

- Provider extraction and runtime cutover:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/sdk_helpers.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/adapter.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/runtime.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_review.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_helpers.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/learning/guard.py`
- Intelligence-stage migration:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/claude_planner.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/planning/unified_planner.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/contract_builder.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/followup.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/review/llm_review.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/review/synthesizer.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/ci_watcher.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/tasks.py`
- Routing, config, and cost:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/model_router.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/settings.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/project_config.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/cost_estimator.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/budget.py`
- Persistence and API:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/app.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/models/schemas.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/settings.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/tasks.py`
- CLI and preflight:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/main.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/doctor.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/preflight.py`
- UI and display:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/app/settings/page.tsx`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/stores/taskStore.ts`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/components/task/TaskDetailPanel.tsx`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/components/task/CompletionSummary.tsx`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tui/app.py`

### New Files To Add

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/__init__.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/catalog.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/registry.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/claude.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/openai.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/restrictions.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/safety_auditor.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/cost_registry.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/providers.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/__init__.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/agent_tests.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/planner_tests.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/reviewer_tests.py`
- Optional later phase:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/mcp/__init__.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/mcp/server.py`

### Obsolete Paths Or Legacy Seams To Preserve Temporarily

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/sdk_helpers.py` remains only as an extraction seam until Claude provider logic is fully moved into `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/claude.py`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/adapter.py` may remain as a compatibility wrapper during rollout, but daemon execution must stop instantiating `ClaudeAdapter` directly.
- Bare aliases such as `"opus"` and `"sonnet"` remain valid through `ModelSpec.parse(...)`.
- `tasks.session_id` remains dual-read and dual-write during migration until the schema cutover question is resolved.
- The current text-based FORGE_QUESTION flow remains the launch implementation; the optional MCP server is deferred.

### Tests That Must Change

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/model_router_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/settings_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/project_config_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/sdk_helpers_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/adapter_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/runtime_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/claude_planner_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/planning/unified_planner_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/review/llm_review_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/learning/guard_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor_question_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_helpers_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/settings_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/tasks_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db_test.py`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db_question_test.py`

### Design-to-Code Gaps

- The design inventory is stale. The current repo has more Claude-specific call sites than the design enumerates. The implementation must also migrate:
  - `_generate_branch_name()` in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py`
  - `_generate_pr_title()` in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/tasks.py`
  - `classify_questions()` in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/followup.py`
- The design assumes settings affect execution, but today saved web settings do not feed `daemon_factory()`.
- The design replaces `allowed_dirs` with `WorkspaceRoots.read_only_dirs`, but the current `extra_dirs` request field is not wired into execution.
- The design assumes pristine worktrees before agent start, but `/Users/mtarun/Desktop/SideHustles/claude-does/forge/merge/worktree.py` currently patches `.gitignore` in the worktree.
- The design describes semantic DB migration, but `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db.py` currently only adds missing columns; it does not perform backfill transforms.
- The design file itself contains a stale mention of `tasks.provider`; the schema section correctly uses `tasks.provider_model`. Implementation must use `provider_model`.

## Required Types and Contracts

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ModelSpec`.
  - Fields: `provider`, `model`
  - `parse("claude:opus") -> ModelSpec("claude", "opus")`
  - `parse("sonnet") -> ModelSpec("claude", "sonnet")`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `CatalogEntry`.
  - Identity: `provider`, `alias`, `canonical_id`, `backend`
  - Support: `tier`
  - Capabilities: `can_use_tools`, `can_stream`, `can_resume_session`, `can_run_shell`, `can_edit_files`, `supports_mcp_servers`, `max_context_tokens`, `supports_structured_output`, `supports_reasoning`
  - Cost: `cost_key`
  - Validation: `validated_stages`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ResumeState`.
  - Fields: `provider`, `backend`, `session_token`, `created_at`, `last_active_at`, `turn_count`, `is_resumable`
  - Methods: `to_json()`, `from_json()`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ProviderResult`.
  - Fields: `text`, `is_error`, `input_tokens`, `output_tokens`, `resume_state`, `duration_ms`, `provider_reported_cost_usd`, `model_canonical_id`, `raw`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `EventKind`.
  - Closed set: `TEXT`, `TOOL_USE`, `TOOL_RESULT`, `ERROR`, `USAGE`, `STATUS`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ProviderEvent`.
  - Core fields: `kind`, `sequence`, `timestamp_ms`, `correlation_id`
  - Text fields: `text`, `token_count`
  - Tool fields: `tool_name`, `tool_input`, `tool_call_id`, `tool_output`, `is_tool_error`
  - Usage fields: `input_tokens`, `output_tokens`
  - Status field: `status`
  - Error fields: `error_message`, `is_transient`
  - Debug field: `raw`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `SafetyBoundary`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/catalog.py` must define `CoreTool` and provider tool-name mappings.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `WorkspaceRoots(primary_cwd, read_only_dirs)` and `MCPServerConfig(name, command, args, env)`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ExecutionMode` with only `CODING` and `INTELLIGENCE`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ToolPolicy`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/restrictions.py` must define:
  - `AGENT_DENIED_OPERATIONS`
  - `PLANNER_TOOL_POLICY`
  - `CONTRACT_TOOL_POLICY`
  - `AGENT_TOOL_POLICY`
  - `REVIEWER_TOOL_POLICY`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `OutputContract`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ExecutionHandle`.
  - `abort()`
  - `result()`
  - `is_running`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ProviderProtocol`.
  - `catalog_entries()`
  - `health_check()`
  - `start(...)`
  - `can_resume()`
  - `cleanup_session()`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py` must define `ProviderHealthStatus`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/model_router.py` `select_model()` must return `ModelSpec`, not `str`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db.py` `TaskRow` and `PipelineRow` must add the provider persistence fields described below.

## Provider Layer Implementation Plan

- Create `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/registry.py` as a daemon-owned registry.
- Add `_init_providers()` to `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py`.
  - always register `ClaudeProvider`
  - register `OpenAIProvider` only when `ForgeSettings.openai_enabled` is true
- Move all direct Claude SDK behavior into `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/claude.py`.
  - message translation
  - usage parsing
  - session/resume handling
  - disallowed tool translation
  - execution handle construction
- Implement `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/openai.py` around the design’s backend split.
  - `codex-sdk` for coding-capable entries
  - `openai-agents-sdk` for intelligence entries
  - backend selection comes from `CatalogEntry.backend`, not from ad hoc stage rules
- All callers must resolve `ModelSpec -> CatalogEntry -> ProviderProtocol`.
- No caller outside the provider package may inspect provider-native response objects.
- `cleanup_session()` runs only after terminal success, permanent failure, or cancellation.
- The cancellation surface is `ExecutionHandle.abort()` only.
- `/Users/mtarun/Desktop/SideHustles/claude-does/pyproject.toml` and `/Users/mtarun/Desktop/SideHustles/claude-does/install.sh` must be updated once exact OpenAI package names are confirmed.

## Execution Flow Changes

### Before

- Planner and unified planner call `sdk_query()` directly.
- Agent execution instantiates `ClaudeAdapter` directly and streams Claude-native messages.
- Review and synthesizer call `sdk_query()` directly.
- Follow-up classification, branch naming, and PR title generation bypass any provider abstraction.
- Resume uses bare `session_id`.

### After

- Pipeline creation resolves and persists `provider_config`.
- Each stage resolves `ModelSpec`, gets `CatalogEntry`, selects a provider through `ProviderRegistry`, and calls `provider.start(...)`.
- Stage execution modes are fixed:
  - `agent`, `ci_fix` -> `ExecutionMode.CODING`
  - `planner`, `unified_planner`, `contract_builder`, `reviewer`, `synthesizer`, `followup` -> `ExecutionMode.INTELLIGENCE`
- Utility helpers also migrate to intelligence mode:
  - branch naming
  - PR title generation
- Streaming always uses `ProviderEvent`.
- Resume always uses `ResumeState`.
- After each attempt, task persistence records:
  - `provider_model`
  - `backend`
  - `canonical_model_id`
  - `model_history`
  - tokens
  - duration
  - cost

## Safety Enforcement Plan

- Layer A is provider-native enforcement.
  - `ClaudeProvider` maps denied operations to `disallowed_tools`.
  - `OpenAIProvider` uses the strongest native sandbox available for filesystem and network restrictions and uses provider instructions for residual git denials.
- Layer B is Forge-owned enforcement in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/safety_auditor.py`.
  - It consumes `ProviderEvent`.
  - It returns allow or abort decisions.
  - It does not call provider SDKs directly.
- Cancellation ownership is unambiguous.
  - user cancel
  - timeout
  - runtime guard violation
  - safety violation
  - all terminate through `ExecutionHandle.abort()`
- Layer C is post-execution rollback in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py`.
  - It must run after success, failure, abort, and retry.
  - It must cover both file and git metadata recovery.

### Provider-Native Enforcement

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/restrictions.py` is the only source of denied operations.
- The current `AGENT_DISALLOWED_TOOLS` list in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/adapter.py` must be removed or replaced by imports from `restrictions.py`.

### Abort Semantics

- The executor owns the handle returned from `provider.start(...)`.
- The first event onward must be abortable.
- A safety violation must:
  - call `handle.abort()`
  - mark the task failed with an explicit safety reason
  - trigger rollback

### Layer C Rollback

- Primary worktree rollback must restore the pre-run state after managed setup writes are complete.
- Snapshot must include:
  - current branch
  - HEAD SHA
  - worktree status
  - remote configuration
  - tag refs
  - stash state
  - recoverability state for every mounted read-only directory
- If the agent mutates branch/head/remotes/tags/stash despite policy, rollback restores them before marking the task terminal.

### Git Safety

- Destructive git operations remain denied.
- The rollback path must explicitly undo:
  - branch switches
  - head changes
  - new or modified remotes
  - new or modified tags
  - new stashes created during execution

### Read-Only Dirs

- `WorkspaceRoots.read_only_dirs` are advisory to the model but enforced by Forge.
- Each read-only dir must be recoverable at mount time.
  - clean git repo -> recover with git
  - non-git dir under size threshold -> recover from temp backup
  - otherwise reject mount
- The current `CreateTaskRequest.extra_dirs` field must map to `read_only_dirs`.

### MCP And Tool Behavior

- MCP tool calls must flow through the same safety policy.
- When the optional Forge MCP server exists, Forge-owned MCP dispatch checks policy before invoking the tool.
- Built-in provider tools may already be in flight when a `TOOL_USE` event arrives; the compensating action is abort plus rollback.

## Streaming/Event Migration Plan

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_helpers.py`
  - remove Claude-specific business logic
  - any surviving helpers operate on `ProviderEvent` only
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py`
  - `_on_planner_msg()` and `_on_unified_msg()` consume `ProviderEvent.TEXT` and `ProviderEvent.USAGE`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py`
  - `_stream_agent()` consumes `ProviderEvent`
  - no direct SDK message inspection remains
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_review.py`
  - `_make_review_on_message()` consumes normalized events only
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/followup.py`
  - accumulate text from normalized text events only
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/learning/guard.py`
  - switch from Claude block inspection to `tool_name`, `tool_input`, `tool_output`, and `is_tool_error`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/claude.py`
  - is the only place allowed to inspect Claude SDK message classes after migration

### Current Claude-Specific Message Consumers And Migration Target

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_helpers.py` `_extract_text()`, `_extract_activity()` -> delete Claude block parsing; replace with `ProviderEvent` formatting helpers if still needed
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py` `_on_planner_msg()`, `_on_unified_msg()` -> consume `ProviderEvent.TEXT` and `ProviderEvent.USAGE`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py` `_stream_agent()` -> consume `ProviderEvent`, feed `RuntimeGuard` and `SafetyAuditor`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_review.py` `_make_review_on_message()` -> consume `ProviderEvent.TEXT`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/followup.py` `on_message()` -> consume `ProviderEvent.TEXT`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/learning/guard.py` `RuntimeGuard.inspect()` -> consume normalized tool events
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/sdk_helpers.py` result parsing -> move entirely into `ClaudeProvider`

## Routing and Model Catalog Plan

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/catalog.py` becomes the sole source of model capabilities, backends, validated stages, and support tier.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/model_router.py` must:
  - replace the current Claude-only table with the provider-aware default table from the design
  - add `ci_fix`
  - return `ModelSpec`
  - accept routing overrides and a registry
- Retry escalation must remain intra-provider only.
- `--provider` uses provider tier mapping and never overrides explicit per-stage flags.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/project_config.py` must parse `[routing]`:
  - `planner`
  - `agent_low`
  - `agent_medium`
  - `agent_high`
  - `reviewer`
  - `contract_builder`
  - `ci_fix`
- New pipelines must persist the exact resolved snapshot to `pipelines.provider_config`.
- Task attempts must persist the actual resolved attempt to task-level fields.
- No API, UI, CLI, or runtime path may derive capabilities from a second model list.

## Cost/Budget Plan

- Add `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/cost_registry.py`.
  - `ModelRates`
  - `CostRegistry`
  - `UnknownCostBehavior`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/cost_estimator.py` must stop inferring rates from Claude model families.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/budget.py` must enforce budget behavior through the registry.
- Actual cost resolution order:
  - use provider-reported cost when present
  - else compute from provider/model token rates
- Legacy cost rate fields in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/settings.py` remain only as deprecated shims into the registry.
- Unknown-rate behavior must be budget-safe.
  - block when a pipeline budget is active
  - estimate high only when budget-safe behavior permits it
- Historical rows with defaulted provider/model values must not be presented as exact historical provenance.

## Config and Settings Plan

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/settings.py` must add:
  - `openai_enabled`
  - `planner_model`
  - `agent_model_low`
  - `agent_model_medium`
  - `agent_model_high`
  - `reviewer_model`
  - `contract_builder_model`
  - `ci_fix_model`
  - `cost_rates`
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/project_config.py` must:
  - keep `[agents].model` backward compatible
  - add `[routing]`
  - move validation to an explicit `validate(registry)` step
- Settings precedence must be:
  - CLI per-stage flags
  - CLI `--provider`
  - `forge.toml [routing]`
  - `ForgeSettings` overrides
  - default routing table
- Saved web settings must be merged into daemon creation for new pipelines only.
- Execution of an existing pipeline must use persisted `provider_config`, not current user settings.
- `FORGE_OPENAI_ENABLED` gates the OpenAI provider; Codex-backed models should use `codex login` or `CODEX_API_KEY`, while Responses API models still require `OPENAI_API_KEY`.

## Database Migration Plan

- Add task columns in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db.py`:
  - `provider_model TEXT DEFAULT 'claude:sonnet'`
  - `backend TEXT DEFAULT 'claude-code-sdk'`
  - `canonical_model_id TEXT`
  - `model_history TEXT`
  - `resume_state TEXT`
- Add pipeline column:
  - `provider_config TEXT`
- Extend `Database.initialize()` with an explicit semantic migration helper after `create_all()` and `_add_missing_columns()`.

### Backfill Behavior

- If `session_id` is present and `resume_state` is null:
  - wrap `session_id` into `ResumeState(provider="claude", backend="claude-code-sdk", session_token=<old>, created_at=<migration timestamp>, last_active_at=<migration timestamp>, turn_count=0, is_resumable=True)`
- If `backend` is null:
  - set `claude-code-sdk`
- If `provider_model` is null:
  - set `claude:sonnet` as compatibility default only
- If `model_history` is null:
  - set `[]`
- If `provider_config` is null on historical pipelines:
  - leave null unless an exact snapshot can be reconstructed

### Rollback And Compatibility

- Do not drop `session_id` in the initial migration.
- New code must dual-read:
  - prefer `resume_state`
  - fall back to `session_id`
- During migration window, new code dual-writes the provider session token to:
  - `resume_state`
  - `session_id` compatibility field
- API and UI must tolerate null `provider_config`, `canonical_model_id`, and empty `model_history`.

## API Changes

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/settings.py`
  - `GET /api/settings` adds:
    - `openai_enabled`
    - `available_providers`
    - `catalog`
  - `UpdateSettingsRequest` adds:
    - `contract_builder_model`
    - `ci_fix_model`
  - all stage model values accept `"provider:model"` and bare Claude aliases
- Add `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/providers.py`
  - `GET /api/providers`
  - response comes directly from `ProviderRegistry` and catalog entries
  - response also includes observed health data sourced from `forge/providers/health_state.json` so the Web UI can render the design-mandated health indicators without inventing a second provider-status source
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/models/schemas.py`
  - add typed response models for provider summaries and catalog summaries
  - extend task-status payloads with provider metadata
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/tasks.py`
  - persist `provider_config` at pipeline creation
  - return task-level `provider_model`, `backend`, `canonical_model_id`, `model_history`
  - return pipeline-level `provider_config`
- Existing API fields remain; new fields are additive.

## CLI Changes

- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/main.py` `forge run` must add:
  - `--provider`
  - `--planner`
  - `--agent`
  - `--reviewer`
  - `--contract-builder`
  - `--ci-fix`
- If `--agent` is supplied, it sets all agent complexity tiers in the resolved snapshot unless finer-grained support is later added.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/doctor.py` must report provider health by backend using `ProviderRegistry.preflight_all()` and must also surface observed-health warnings from `forge/providers/health_state.json` when nightly conformance has degraded a model without changing its catalog tier.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/preflight.py` must use `ProviderRegistry.preflight_for_pipeline(resolved_models)` for pipeline execution preflight, and may use `ProviderRegistry.preflight_all()` only for broad non-pipeline checks.
- Add `forge providers list` to `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/main.py`.
  - It shows catalog entries, tier badges, validated stages, and observed-health warnings when available.
- Add `forge providers test <provider:model>` once conformance tests exist.

## UI Changes

- `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/app/settings/page.tsx`
  - replace hardcoded Claude-only model selectors
  - add provider/model selector pairs for:
    - planner
    - agent low
    - agent medium
    - agent high
    - reviewer
    - contract builder
- Dropdowns must populate from `GET /api/providers`, not a hardcoded list.
- Incompatible models must be disabled using catalog capabilities.
- Settings dropdowns must also render observed-health indicators from `health_state.json` data returned by the provider payload.
  - degraded-but-still-supported models render a warning indicator rather than disappearing from selection
- `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/stores/taskStore.ts`
  - add task fields:
    - `providerModel`
    - `backend`
    - `canonicalModelId`
    - `modelHistory`
  - add pipeline field:
    - `providerConfig`
- `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/app/page.tsx`
  - add provider health indicators on the existing dashboard surface using observed-health data returned from the backend
  - dashboard health is informational and must not mutate catalog tier labels
- `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/components/task/TaskDetailPanel.tsx`
  - show `Model: provider:model (via backend)`
  - show escalation history when `modelHistory` has more than one attempt
- `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/components/task/CompletionSummary.tsx`
  - show the persisted resolved provider config
  - do not recompute from current settings
- There is no configured frontend test runner in `/Users/mtarun/Desktop/SideHustles/claude-does/web/package.json`; UI validation in this rollout is manual smoke testing unless a separate frontend harness is added.

## Test Strategy

### Unit

- `ModelSpec.parse()`
- catalog validation
- stage validation
- escalation chains
- provider tier mapping
- `CostRegistry` fallback and unknown-rate behavior
- `ResumeState` serialization
- `ProviderEvent` normalization
- `RuntimeGuard` on normalized events
- `SafetyAuditor` policy and path checks

### Integration

- daemon to provider flow with fake providers
- DB migration and dual-read behavior
- API responses for settings, providers, pipeline creation, status, restart, and execute
- API/provider payload handling for observed-health data consumed by `forge doctor`, `forge providers list`, settings dropdown warnings, and dashboard indicators
- safety abort plus rollback paths
- question pause and resume using `ResumeState`

### End-to-End

- full Claude-backed pipeline
- safety-aborted run with recovery
- question/resume run that survives process restart

### Real-Provider Conformance

- implement under `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/`
- run real Claude suite on provider-layer changes
- run OpenAI smoke gate for shared-layer trigger files
- require manual full OpenAI suite before promoting an OpenAI model beyond `experimental`

## Conformance Strategy

- Catalog tier and validated stages live in `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/catalog.py`.
- Conformance cases must match the design:
  - agent:
    - `test_simple_file_edit`
    - `test_shell_execution`
    - `test_safety_boundary`
    - `test_file_scope`
    - `test_question_protocol`
    - `test_resume`
  - planner:
    - `test_produces_valid_taskgraph`
    - `test_reads_codebase`
    - `test_respects_tool_allowlist`
  - reviewer:
    - `test_produces_valid_verdict`
    - `test_identifies_obvious_bug`
- Nightly observed health is stored separately in `forge/providers/health_state.json`.
- Observed health does not mutate catalog tier.
- Operational consumers of observed health are mandatory, not optional:
  - `forge doctor` warns on repeated failures while preserving catalog tier
  - `forge providers list` shows tier plus observed-health status
  - the Web UI settings dropdown and dashboard render degradation indicators
- Promotions and demotions are code changes to the catalog, not runtime side effects.

## Rollout Phases

### Phase 1

- Objective: land provider contracts, catalog, restrictions, registry skeleton, and cost registry without runtime cutover.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/base.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/catalog.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/restrictions.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/registry.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/cost_registry.py`
- Risks:
  - unused abstractions
  - type churn without execution adoption
- Exit criteria:
  - unit tests for types/catalog/cost registry pass
  - no runtime behavior changes

### Phase 2

- Objective: add routing, config parsing, settings fields, and DB compatibility columns.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/model_router.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/settings.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/config/project_config.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/settings.py`
- Risks:
  - backward compatibility break for bare aliases
  - in-flight paused tasks breaking on new schema
- Exit criteria:
  - old Claude-only settings still load
  - new fields persist
  - legacy rows remain readable

### Phase 3

- Objective: cut agent execution to provider protocol on Claude, including normalized events, resume state, abort, and safety layers.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/claude.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/runtime.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/agents/adapter.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/learning/guard.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/safety_auditor.py`
- Risks:
  - streaming regressions
  - resume breakage
  - incomplete rollback
- Exit criteria:
  - Claude agent tasks run through provider protocol
  - question/resume passes
  - safety abort plus rollback passes

### Phase 4

- Objective: migrate all intelligence-stage and stray helper call sites.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/claude_planner.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/planning/unified_planner.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/contract_builder.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/followup.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/review/llm_review.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/review/synthesizer.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/tasks.py`
- Risks:
  - structured-output regressions
  - hidden `sdk_query()` dependencies remaining
- Exit criteria:
  - no production call site outside providers uses `sdk_query()` or provider-native types

### Phase 5

- Objective: expose provider-aware config through API and CLI and make execution use persisted snapshots.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/app.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/models/schemas.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/providers.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/tasks.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/main.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/doctor.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/preflight.py`
- Risks:
  - UI-visible settings that still do not affect execution
- Exit criteria:
  - new pipelines persist `provider_config`
  - API exposes provider metadata
  - CLI provider flags resolve with the documented precedence
  - `forge doctor` uses `registry.preflight_all()`
  - pipeline preflight uses `registry.preflight_for_pipeline(resolved_models)`

### Phase 6

- Objective: add OpenAI provider implementation, backend health checks, and the minimal real-provider conformance scaffold required to gate the OpenAI path.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/openai.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/pyproject.toml`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/install.sh`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/doctor.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/preflight.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/__init__.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/agent_tests.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/main.py`
- Risks:
  - SDK/package mismatch
  - weaker git denial semantics on OpenAI side
  - resume divergence
  - incomplete conformance scaffold yielding a meaningless smoke gate
- Exit criteria:
  - provider registers only when enabled
  - health checks are accurate
  - minimal conformance scaffolding exists locally and in CI to execute `test_simple_file_edit` against `openai:gpt-5.4-mini`
  - OpenAI smoke conformance passes

### Phase 7

- Objective: add provider/model selectors, execution metadata, and observed-health indicators to the web UI.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/app/page.tsx`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/app/settings/page.tsx`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/stores/taskStore.ts`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/components/task/TaskDetailPanel.tsx`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/components/task/CompletionSummary.tsx`
- Risks:
  - stale or recomputed display values
  - no frontend automation
- Exit criteria:
  - manual smoke passes for settings, dashboard, task detail, and summary displays

### Phase 8

- Objective: expand the conformance suite beyond the Phase 6 smoke scaffold and add the optional Forge MCP server.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/agent_tests.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/planner_tests.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/tests/conformance/reviewer_tests.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/mcp/server.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/main.py`
- Risks:
  - additional scope delaying core cutover
- Exit criteria:
  - full stage conformance suite exists
  - `forge providers test <provider:model>` runs the conformance suite for the requested model and stage set
  - optional MCP integration works without changing default provider execution

### Phase 9

- Objective: land the operational tooling that consumes observed health and catalog state.
- Files touched:
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/doctor.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/cli/main.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/forge/api/routes/providers.py`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/app/page.tsx`
  - `/Users/mtarun/Desktop/SideHustles/claude-does/web/src/app/settings/page.tsx`
- Risks:
  - health indicators drifting from catalog data
  - operational status being mistaken for tier mutation
- Exit criteria:
  - `forge doctor` shows observed-health warnings when nightly failures accumulate
  - `forge providers list` shows catalog tier plus observed health
  - dashboard and settings surfaces display health indicators sourced from observed-health data
  - catalog tier remains stable unless changed in code

## Risk Register

- Technical: provider event normalization can diverge from backend reality and break guard or question handling.
  - Mitigation: fake-provider tests plus real-provider conformance on the shared-layer file set.
- Technical: CI fix currently assumes `git push`, which conflicts with denied git operations.
  - Mitigation: clarify intended CI fix handoff before implementing provider parity.
- Technical: read-only mount recovery can be incomplete if unrecoverable paths are allowed.
  - Mitigation: reject unrecoverable mounts at preflight.
- Migration: historical tasks do not record exact models.
  - Mitigation: treat backfilled provider/model values as compatibility defaults only.
- Migration: removing `session_id` too early can strand paused tasks.
  - Mitigation: dual-read and dual-write through migration window.
- Operational: enabling OpenAI without SDKs or credentials can break startup.
  - Mitigation: lazy imports and explicit health-check failures.
- Operational: web settings can diverge from pipeline execution.
  - Mitigation: persist `provider_config` at pipeline creation and execute from the snapshot.
- Safety: worktree bootstrap writes can invalidate rollback baselines.
  - Mitigation: define baseline after managed bootstrap writes or stop mutating the worktree before execution.
- Cost: unknown model rates can bypass budgets.
  - Mitigation: block unknown-cost models when a budget is active.
- Governance: nightly provider regressions can be confused with catalog changes.
  - Mitigation: keep observed health separate from catalog tier.

## Open Clarifications Required Before Implementation

- The design references `ForgeSettings.max_pipeline_cost_usd`, but current code and API use `budget_limit_usd`. One canonical budget field must be chosen.
- Section 13.1 says `resume_state` replaces `session_id`, while Section 13.3 still treats `session_id` as an unchanged provider-agnostic field. The cutover rule must be clarified.
- The design routes `ci_fix` through the provider layer with coding-mode safety, but the current CI fix flow assumes `git push`. The intended post-fix workflow must be defined.
- `openai_enabled` appears as a `ForgeSettings` field and a settings response field, but current `/api/settings` is per-user while `ForgeSettings` is process/env backed. Ownership of provider enablement must be clarified.
- Exact OpenAI Python package names and minimum versions for the design’s `codex-sdk` and `openai-agents-sdk` backends are not specified in the design and must be confirmed before coding.
- The mapping from current `allowed_dirs` and `CreateTaskRequest.extra_dirs` to `WorkspaceRoots.read_only_dirs` should be explicitly confirmed because it changes enforcement semantics.

## Acceptance Criteria

- No production file outside `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/claude.py` and `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/openai.py` imports provider SDK types.
- Every LLM execution path uses `ProviderRegistry` and `ProviderProtocol.start(...)`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/model_router.py` returns `ModelSpec`, supports `ci_fix`, and never silently cross-falls back to a different provider.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/providers/catalog.py` is the single source of model capability truth for runtime, API, CLI, and UI.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon.py`, `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_executor.py`, `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/daemon_review.py`, `/Users/mtarun/Desktop/SideHustles/claude-does/forge/core/followup.py`, and `/Users/mtarun/Desktop/SideHustles/claude-does/forge/learning/guard.py` consume only `ProviderEvent`.
- A forbidden operation such as `git push` or a write to a mounted read-only dir is blocked or aborted and then rolled back before the task is marked terminal.
- Paused tasks resume through `ResumeState` on the same provider/backend when valid, and fall back to a fresh attempt when invalid, with attempt history appended to `model_history`.
- `/Users/mtarun/Desktop/SideHustles/claude-does/forge/storage/db.py` persists `provider_model`, `backend`, `canonical_model_id`, `model_history`, `resume_state`, and `provider_config`.
- `/api/settings` returns provider metadata, `/api/providers` returns registry-derived provider/model capabilities, and task status returns task-level provider metadata plus pipeline-level `provider_config`.
- `forge run` accepts the new provider/stage flags and resolves them according to the documented precedence.
- `forge doctor` uses `registry.preflight_all()` and shows observed-health warnings when nightly conformance has degraded a model.
- pipeline execution preflight uses `registry.preflight_for_pipeline(resolved_models)`.
- `forge providers list` shows catalog tier, validated stages, and observed-health warnings.
- The web settings page allows provider/model selection for the required stages, shows degraded-model indicators from observed health, the dashboard shows provider health indicators, task detail shows provider/backend and escalation history, and completion summary shows the persisted routing snapshot.
- Unit, integration, and end-to-end tests pass for the Claude path, and the required real-provider conformance gates pass for Claude plus the OpenAI smoke test when OpenAI is enabled.
