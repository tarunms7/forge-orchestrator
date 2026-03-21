"""Agent runtime. Manages agent execution lifecycle with error boundaries."""

import asyncio
import logging

from forge.agents.adapter import AgentAdapter, AgentResult
from forge.learning.guard import GuardTriggered

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
                transient_keywords = ["rate_limit", "rate limit", "overloaded", "529", "500", "502", "503", "connection", "reset"]
                is_transient = any(kw in err_str for kw in transient_keywords)
                if is_transient and attempt < max_retries:
                    backoff = 5 * (2 ** attempt)
                    logger.warning(
                        "Transient error for agent '%s' (attempt %d/%d): %s — retrying in %ds",
                        agent_id, attempt + 1, max_retries + 1, e, backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                return AgentResult(
                    success=False,
                    files_changed=[],
                    summary=f"Agent '{agent_id}' failed: {e}",
                    error=str(e),
                )
