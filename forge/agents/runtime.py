"""Agent runtime. Manages agent execution lifecycle with error boundaries."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable

from forge.agents.adapter import AgentAdapter, AgentResult
from forge.learning.guard import GuardTriggered
from forge.providers.base import (
    AuditVerdict,
    CatalogEntry,
    ExecutionMode,
    OutputContract,
    ProviderEvent,
    ProviderProtocol,
    ProviderResult,
    ResumeState,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.safety_auditor import SafetyAuditor

logger = logging.getLogger("forge.agents.runtime")


class AgentRuntime:
    """Wraps an adapter with timeout handling and error boundaries."""

    def __init__(self, adapter: AgentAdapter, timeout_seconds: int) -> None:
        self._adapter = adapter
        self._timeout = timeout_seconds

    async def run_task(
        self,
        agent_id: str,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        allowed_dirs: list[str] | None = None,
        model: str = "sonnet",
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
    ) -> AgentResult:
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
                err_str = str(e).lower()
                transient_keywords = [
                    "rate_limit",
                    "rate limit",
                    "overloaded",
                    "529",
                    "500",
                    "502",
                    "503",
                    "connection",
                    "reset",
                ]
                is_transient = any(kw in err_str for kw in transient_keywords)
                if is_transient and attempt < max_retries:
                    backoff = 5 * (2**attempt) + random.uniform(0, 5)
                    logger.warning(
                        "Transient error for agent '%s' (attempt %d/%d): %s — retrying in %.1fs",
                        agent_id,
                        attempt + 1,
                        max_retries + 1,
                        e,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                return AgentResult(
                    success=False,
                    files_changed=[],
                    summary=f"Agent '{agent_id}' failed: {e}",
                    error=str(e),
                )
        # Should not reach here, but satisfy type checker
        return AgentResult(
            success=False,
            files_changed=[],
            summary=f"Agent '{agent_id}' exhausted retries",
            error="Exhausted retries",
        )


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

    Returns:
        AgentResult with provider metadata populated.
    """
    auditor = SafetyAuditor(policy=tool_policy, workspace=workspace)
    safety_abort_reason: str | None = None

    for attempt in range(max_retries + 1):
        safety_abort_reason = None
        handle = None
        attempt_t0 = time.monotonic()

        # Build the event callback that chains safety auditor + consumer
        def _make_on_event(abort_holder: list):
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
                # Forward to consumer
                if on_event:
                    on_event(event)

            return _on_event

        abort_reasons: list[str] = []
        event_cb = _make_on_event(abort_reasons)

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
                )

            return _build_result(
                result,
                catalog_entry,
                success=not result.is_error,
                error=result.text if result.is_error else None,
                attempt=attempt,
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
            elapsed = time.monotonic() - attempt_t0
            err_str = str(e).lower()
            transient_keywords = [
                "rate_limit",
                "rate limit",
                "overloaded",
                "529",
                "500",
                "502",
                "503",
                "connection",
                "reset",
            ]
            is_transient = any(kw in err_str for kw in transient_keywords)
            if is_transient and attempt < max_retries:
                backoff = 5 * (2**attempt) + random.uniform(0, 5)
                logger.warning(
                    "Transient error (attempt %d/%d, %.1fs): %s — retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    elapsed,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                continue
            # Non-transient or exhausted retries
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Provider failed: {e}",
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
) -> AgentResult:
    """Convert ProviderResult to AgentResult with provider metadata."""
    model_history_entry = {
        "attempt": attempt,
        "model": str(catalog_entry.spec),
        "backend": catalog_entry.backend,
        "result": "success" if success else "error",
        "cost_usd": result.provider_reported_cost_usd or 0.0,
    }

    return AgentResult(
        success=success,
        files_changed=[],  # Caller populates from git diff
        summary=result.text if success else (error or result.text),
        cost_usd=result.provider_reported_cost_usd or 0.0,
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
