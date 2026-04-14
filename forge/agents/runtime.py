"""Agent runtime. Manages agent execution lifecycle with error boundaries."""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime

from forge.agents.adapter import (
    AgentAdapter,
    AgentResult,
    _get_changed_files,
    build_agent_system_prompt,
)
from forge.agents.context_manager import (
    AgentContextManager,
    ContextPressure,
)
from forge.core.async_utils import safe_create_task
from forge.core.cost_registry import CostRegistry
from forge.learning.guard import GuardTriggered
from forge.providers.base import (
    AuditVerdict,
    CatalogEntry,
    EventKind,
    ExecutionMode,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    ProviderProtocol,
    ProviderResult,
    ResumeState,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.registry import ProviderRegistry
from forge.providers.restrictions import AGENT_TOOL_POLICY
from forge.providers.safety_auditor import SafetyAuditor

logger = logging.getLogger("forge.agents.runtime")

# ── Token Velocity Monitor ──────────────────────────────────────────────
# Inspired by Claude Code's diminishing-returns detection in tokenBudget.ts.
# Tracks output token delta per turn and flags when an agent is spinning
# (producing negligible output over consecutive turns).

_VELOCITY_WINDOW = 5  # consecutive low-output turns before flagging
_VELOCITY_MIN_TOKENS = 150  # minimum tokens per turn to not count as "low"


class TokenVelocityMonitor:
    """Detects agents that are spinning without producing meaningful output.

    Track cumulative output_tokens across provider events. When the delta
    (new tokens since last check) drops below _VELOCITY_MIN_TOKENS for
    _VELOCITY_WINDOW consecutive checks, ``is_stalled`` returns True.

    Usage:
        monitor = TokenVelocityMonitor()
        # ... wire monitor.record(event) into the on_event callback ...
        if monitor.is_stalled:
            # abort or escalate
    """

    def __init__(
        self,
        window: int = _VELOCITY_WINDOW,
        min_tokens: int = _VELOCITY_MIN_TOKENS,
    ) -> None:
        self._window = window
        self._min_tokens = min_tokens
        self._last_output_tokens: int = 0
        self._low_velocity_streak: int = 0
        self._total_checks: int = 0

    def record_tokens(self, output_tokens: int) -> None:
        """Record a token count snapshot (called per turn or per event batch)."""
        delta = output_tokens - self._last_output_tokens
        self._last_output_tokens = output_tokens
        self._total_checks += 1
        if self._total_checks <= 2:
            # Skip first two checks — agent is still warming up
            return
        if delta < self._min_tokens:
            self._low_velocity_streak += 1
        else:
            self._low_velocity_streak = 0

    @property
    def is_stalled(self) -> bool:
        return self._low_velocity_streak >= self._window

    @property
    def streak(self) -> int:
        return self._low_velocity_streak


class AgentRuntime:
    """Wraps an adapter with timeout handling and error boundaries."""

    def __init__(
        self,
        adapter: AgentAdapter | None = None,
        timeout_seconds: int = 600,
        *,
        registry: ProviderRegistry | None = None,
        cost_registry: CostRegistry | None = None,
    ) -> None:
        self._adapter = adapter
        self._timeout = timeout_seconds
        self._registry = registry
        self._cost_registry = cost_registry

    async def run_task(
        self,
        agent_id: str,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        allowed_dirs: list[str] | None = None,
        model: str | ModelSpec = "sonnet",
        on_message=None,
        project_context: str = "",
        conventions_json: str | None = None,
        conventions_md: str | None = None,
        completed_deps: list[dict] | None = None,
        contracts_block: str = "",
        lessons_block: str = "",
        resume: str | None = None,
        autonomy: str = "balanced",
        questions_remaining: int = 3,
        timeout_seconds: int | None = None,
        project_dir: str | None = None,
        agent_max_turns: int = 75,
        project_commands: dict[str, str] | None = None,
        reasoning_effort: str | None = None,
    ) -> AgentResult:
        if self._registry is not None:
            return await self._run_provider_task(
                agent_id=agent_id,
                task_prompt=task_prompt,
                worktree_path=worktree_path,
                allowed_files=allowed_files,
                allowed_dirs=allowed_dirs,
                model=model,
                on_message=on_message,
                project_context=project_context,
                conventions_json=conventions_json,
                conventions_md=conventions_md,
                completed_deps=completed_deps,
                contracts_block=contracts_block,
                lessons_block=lessons_block,
                resume=resume,
                autonomy=autonomy,
                questions_remaining=questions_remaining,
                timeout_seconds=timeout_seconds,
                project_dir=project_dir,
                agent_max_turns=agent_max_turns,
                project_commands=project_commands,
                reasoning_effort=reasoning_effort,
            )

        if self._adapter is None:
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Agent '{agent_id}' failed: no adapter or provider registry configured",
                error="No adapter or provider registry configured",
            )

        normalized_model = str(model) if isinstance(model, ModelSpec) else model
        effective_timeout = timeout_seconds if timeout_seconds is not None else self._timeout
        max_retries = 2  # 3 total attempts
        for attempt in range(max_retries + 1):
            try:
                return await self._adapter.run(
                    task_prompt=task_prompt,
                    worktree_path=worktree_path,
                    allowed_files=allowed_files,
                    timeout_seconds=effective_timeout,
                    allowed_dirs=allowed_dirs,
                    model=normalized_model,
                    on_message=on_message,
                    project_context=project_context,
                    conventions_json=conventions_json,
                    conventions_md=conventions_md,
                    completed_deps=completed_deps,
                    contracts_block=contracts_block,
                    lessons_block=lessons_block,
                    resume=resume,
                    autonomy=autonomy,
                    questions_remaining=questions_remaining,
                    project_dir=project_dir,
                    agent_max_turns=agent_max_turns,
                    project_commands=project_commands,
                )
            except TimeoutError:
                # Timeout means task is too large; don't retry
                return AgentResult(
                    success=False,
                    files_changed=[],
                    summary=f"Agent '{agent_id}' timed out after {effective_timeout}s",
                    error=f"Timeout after {effective_timeout}s",
                )
            except GuardTriggered:
                raise  # Must propagate to _stream_agent for lesson capture
            except Exception as e:
                from forge.core.error_classifier import classify_agent_error

                classified = classify_agent_error(str(e))
                if classified.retriable and attempt < max_retries:
                    backoff = 5 * (2**attempt) + random.uniform(0, 5)
                    logger.warning(
                        "Retriable error [%s] for agent '%s' (attempt %d/%d): %s — retrying in %.1fs",
                        classified.category,
                        agent_id,
                        attempt + 1,
                        max_retries + 1,
                        classified.message,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                return AgentResult(
                    success=False,
                    files_changed=[],
                    summary=f"Agent '{agent_id}' failed [{classified.category}]: {classified.message}",
                    error=str(e),
                )
        # Should not reach here, but satisfy type checker
        return AgentResult(
            success=False,
            files_changed=[],
            summary=f"Agent '{agent_id}' exhausted retries",
            error="Exhausted retries",
        )

    async def _run_provider_task(
        self,
        *,
        agent_id: str,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        allowed_dirs: list[str] | None = None,
        model: str | ModelSpec = "sonnet",
        on_message=None,
        project_context: str = "",
        conventions_json: str | None = None,
        conventions_md: str | None = None,
        completed_deps: list[dict] | None = None,
        contracts_block: str = "",
        lessons_block: str = "",
        resume: str | None = None,
        autonomy: str = "balanced",
        questions_remaining: int = 3,
        timeout_seconds: int | None = None,
        project_dir: str | None = None,
        agent_max_turns: int = 75,
        project_commands: dict[str, str] | None = None,
        reasoning_effort: str | None = None,
    ) -> AgentResult:
        effective_timeout = timeout_seconds if timeout_seconds is not None else self._timeout
        spec = model if isinstance(model, ModelSpec) else ModelSpec.parse(model)
        provider = self._registry.get_for_model(spec)
        catalog_entry = self._registry.get_catalog_entry(spec)
        system_prompt = build_agent_system_prompt(
            worktree_path=worktree_path,
            allowed_dirs=allowed_dirs or [],
            allowed_files=allowed_files,
            project_context=project_context,
            conventions_json=conventions_json,
            conventions_md=conventions_md,
            completed_deps=completed_deps,
            contracts_block=contracts_block,
            autonomy=autonomy,
            questions_remaining=questions_remaining,
            agent_max_turns=agent_max_turns,
            lessons_block=lessons_block,
            project_commands=project_commands,
        )

        resume_state = None
        if resume:
            now_iso = datetime.now(UTC).isoformat()
            resume_state = ResumeState(
                provider=spec.provider,
                backend=catalog_entry.backend,
                session_token=resume,
                created_at=now_iso,
                last_active_at=now_iso,
                turn_count=0,
                is_resumable=True,
            )

        def _on_event(event: ProviderEvent) -> None:
            if on_message is None:
                return
            try:
                result = on_message(event)
                if inspect.isawaitable(result):
                    safe_create_task(result, logger=logger, name=f"agent-event-{agent_id}")
            except Exception:
                logger.warning("Agent event callback failed for %s", agent_id, exc_info=True)

        result = await run_with_retry(
            provider=provider,
            catalog_entry=catalog_entry,
            prompt=task_prompt,
            system_prompt=system_prompt,
            execution_mode=ExecutionMode.CODING,
            tool_policy=AGENT_TOOL_POLICY,
            output_contract=OutputContract(format="freeform"),
            workspace=WorkspaceRoots(
                primary_cwd=worktree_path,
                read_only_dirs=allowed_dirs or [],
            ),
            max_turns=agent_max_turns,
            timeout_seconds=effective_timeout,
            on_event=_on_event if on_message is not None else None,
            resume_state=resume_state,
            cost_registry=self._cost_registry,
            reasoning_effort=reasoning_effort,
        )
        result.files_changed = await _get_changed_files(worktree_path)
        return result


async def run_with_retry(
    *,
    provider: ProviderProtocol,
    catalog_entry: CatalogEntry,
    prompt: str,
    system_prompt: str,
    execution_mode: ExecutionMode,
    tool_policy: ToolPolicy,
    output_contract: OutputContract,
    workspace: WorkspaceRoots,
    max_turns: int,
    timeout_seconds: int,
    on_event: Callable[[ProviderEvent], None] | None = None,
    max_retries: int = 2,
    resume_state: ResumeState | None = None,
    cost_registry: CostRegistry | None = None,
    reasoning_effort: str | None = None,
) -> AgentResult:
    """Provider-protocol-aware retry loop with safety auditor integration.

    Calls provider.start() in a retry loop with exponential backoff for
    transient errors. Wires a SafetyAuditor into the on_event callback
    that aborts execution on ABORT verdicts.

    Args:
        provider: The provider protocol implementation to use.
        catalog_entry: Model catalog entry describing capabilities.
        prompt: The user prompt to send.
        system_prompt: System prompt (agent instructions).
        execution_mode: CODING or INTELLIGENCE mode.
        tool_policy: Tool access policy (denylist/allowlist).
        output_contract: Expected output format.
        workspace: Workspace roots (primary_cwd + read_only_dirs).
        max_turns: Maximum conversation turns.
        timeout_seconds: Per-attempt timeout.
        on_event: Optional callback for streaming events to consumers.
        max_retries: Number of retries (total attempts = max_retries + 1).
        resume_state: Optional resume state for continuing a session.
        reasoning_effort: Optional per-stage reasoning effort override.

    Returns:
        AgentResult with provider metadata populated.
    """
    auditor = SafetyAuditor(policy=tool_policy, workspace=workspace)
    velocity_monitor = TokenVelocityMonitor()
    context_mgr = AgentContextManager(
        max_context_tokens=catalog_entry.max_context_tokens,
        agent_id=f"provider-{catalog_entry.provider}",
        task_id="run_with_retry",
    )
    safety_abort_reason: str | None = None

    for attempt in range(max_retries + 1):
        safety_abort_reason = None
        handle = None
        attempt_t0 = time.monotonic()

        # Build the event callback that chains:
        # safety auditor → context manager → velocity monitor → consumer
        def _make_on_event(
            abort_holder: list,
            vel_monitor: TokenVelocityMonitor,
            ctx_mgr: AgentContextManager,
        ):
            def _on_event(event: ProviderEvent) -> None:
                # Safety check
                verdict = auditor.check(event)
                if verdict == AuditVerdict.ABORT:
                    violation = auditor.violations[-1] if auditor.violations else None
                    reason = (
                        f"Safety abort: {violation.reason}"
                        if violation
                        else "Safety policy violation"
                    )
                    abort_holder.append(reason)

                # Context pressure tracking — record usage events
                if event.kind == EventKind.USAGE and event.input_tokens and event.output_tokens:
                    pressure = ctx_mgr.record_usage(event.input_tokens, event.output_tokens)
                    if pressure in (ContextPressure.CRITICAL, ContextPressure.EXCEEDED):
                        snapshot = ctx_mgr.snapshot()
                        logger.warning(
                            "Context pressure %s: %d/%d tokens (%.0f%%), ~%d turns remaining",
                            pressure.value,
                            snapshot.input_tokens,
                            snapshot.max_context_tokens,
                            snapshot.utilization_pct * 100,
                            snapshot.estimated_turns_remaining,
                        )

                # Track tool results for potential microcompaction
                if event.kind == EventKind.TOOL_RESULT and event.tool_call_id:
                    ctx_mgr.record_tool_result(
                        tool_call_id=event.tool_call_id,
                        tool_name=event.tool_name or "unknown",
                        result_text=event.tool_output,
                    )

                # Token velocity tracking — record output tokens from turn-complete events
                if hasattr(event, "output_tokens") and event.output_tokens:
                    vel_monitor.record_tokens(event.output_tokens)
                    if vel_monitor.is_stalled:
                        abort_holder.append(
                            f"Token velocity stall: agent produced <{vel_monitor._min_tokens} "
                            f"tokens/turn for {vel_monitor.streak} consecutive turns. "
                            "Auto-terminating to prevent cost waste."
                        )
                # Forward to consumer
                if on_event:
                    on_event(event)

            return _on_event

        abort_reasons: list[str] = []
        event_cb = _make_on_event(abort_reasons, velocity_monitor, context_mgr)

        try:
            handle = provider.start(
                prompt=prompt,
                system_prompt=system_prompt,
                catalog_entry=catalog_entry,
                execution_mode=execution_mode,
                tool_policy=tool_policy,
                output_contract=output_contract,
                workspace=workspace,
                max_turns=max_turns,
                reasoning_effort=reasoning_effort,
                resume_state=resume_state if attempt == 0 else None,
                on_event=event_cb,
            )

            # Wait for result with timeout
            result: ProviderResult = await asyncio.wait_for(
                handle.result(),
                timeout=timeout_seconds,
            )

            # Check if safety auditor triggered abort during execution
            if abort_reasons:
                safety_abort_reason = abort_reasons[0]
                return _build_result(
                    result,
                    catalog_entry,
                    success=False,
                    error=safety_abort_reason,
                    attempt=attempt,
                    cost_registry=cost_registry,
                    context_mgr=context_mgr,
                )

            return _build_result(
                result,
                catalog_entry,
                success=not result.is_error,
                error=result.text if result.is_error else None,
                attempt=attempt,
                cost_registry=cost_registry,
                context_mgr=context_mgr,
            )

        except TimeoutError:
            if handle and handle.is_running:
                await handle.abort()
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Provider timed out after {timeout_seconds}s",
                error=f"Timeout after {timeout_seconds}s",
                provider_model=str(catalog_entry.spec),
                backend=catalog_entry.backend,
                canonical_model_id=catalog_entry.canonical_id,
            )

        except GuardTriggered:
            raise

        except Exception as e:
            from forge.core.error_classifier import classify_agent_error

            elapsed = time.monotonic() - attempt_t0
            classified = classify_agent_error(str(e))
            if classified.retriable and attempt < max_retries:
                backoff = 5 * (2**attempt) + random.uniform(0, 5)
                logger.warning(
                    "Retriable error [%s] (attempt %d/%d, %.1fs): %s — retrying in %.1fs",
                    classified.category,
                    attempt + 1,
                    max_retries + 1,
                    elapsed,
                    classified.message,
                    backoff,
                )
                await asyncio.sleep(backoff)
                continue
            # Non-retriable or exhausted retries
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Provider failed [{classified.category}]: {classified.message}",
                error=str(e),
                provider_model=str(catalog_entry.spec),
                backend=catalog_entry.backend,
                canonical_model_id=catalog_entry.canonical_id,
            )

    # Should not reach here
    return AgentResult(
        success=False,
        files_changed=[],
        summary="Exhausted retries",
        error="Exhausted retries",
        provider_model=str(catalog_entry.spec),
        backend=catalog_entry.backend,
        canonical_model_id=catalog_entry.canonical_id,
    )


def _build_result(
    result: ProviderResult,
    catalog_entry: CatalogEntry,
    *,
    success: bool,
    error: str | None,
    attempt: int,
    cost_registry: CostRegistry | None,
    context_mgr: AgentContextManager | None = None,
) -> AgentResult:
    """Convert ProviderResult to AgentResult with provider metadata."""
    if cost_registry is not None:
        from forge.core.cost_registry import resolve_cost

        cost_usd = resolve_cost(result, catalog_entry.spec, cost_registry)
    else:
        cost_usd = result.provider_reported_cost_usd or 0.0

    model_history_entry = {
        "attempt": attempt,
        "model": str(catalog_entry.spec),
        "backend": catalog_entry.backend,
        "result": "success" if success else "error",
        "cost_usd": cost_usd,
    }

    # Attach context pressure snapshot for daemon telemetry
    if context_mgr is not None:
        snapshot = context_mgr.snapshot()
        model_history_entry["context_pressure"] = snapshot.pressure.value
        model_history_entry["context_utilization_pct"] = round(snapshot.utilization_pct, 3)
        model_history_entry["estimated_turns_remaining"] = snapshot.estimated_turns_remaining

    return AgentResult(
        success=success,
        files_changed=[],  # Caller populates from git diff
        summary=result.text if success else (error or result.text),
        cost_usd=cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        error=error,
        session_id=result.resume_state.session_token if result.resume_state else None,
        resume_state=result.resume_state,
        provider_model=str(catalog_entry.spec),
        backend=catalog_entry.backend,
        canonical_model_id=result.model_canonical_id,
        model_history_entry=model_history_entry,
    )
