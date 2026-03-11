"""Agent runtime. Manages agent execution lifecycle with error boundaries."""

from forge.agents.adapter import AgentAdapter, AgentResult


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
        resume: str | None = None,
        autonomy: str = "balanced",
        questions_remaining: int = 3,
    ) -> AgentResult:
        try:
            return await self._adapter.run(
                task_prompt=task_prompt,
                worktree_path=worktree_path,
                allowed_files=allowed_files,
                timeout_seconds=self._timeout,
                allowed_dirs=allowed_dirs,
                model=model,
                on_message=on_message,
                project_context=project_context,
                conventions_json=conventions_json,
                conventions_md=conventions_md,
                completed_deps=completed_deps,
                contracts_block=contracts_block,
                resume=resume,
                autonomy=autonomy,
                questions_remaining=questions_remaining,
            )
        except TimeoutError:
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Agent '{agent_id}' timed out after {self._timeout}s",
                error=f"Timeout after {self._timeout}s",
            )
        except Exception as e:
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Agent '{agent_id}' failed: {e}",
                error=str(e),
            )
