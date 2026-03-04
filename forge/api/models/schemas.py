"""Pydantic request/response schemas for the Forge REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateTaskRequest(BaseModel):
    """Request body for creating a new pipeline task."""

    description: str
    project_path: str
    extra_dirs: list[str] = Field(default_factory=list)
    model_strategy: str = "auto"
    images: list[str] = Field(default_factory=list, description="Base64-encoded image data URIs (e.g. data:image/png;base64,...)")
    branch_name: str | None = None
    build_cmd: str | None = Field(default=None, description="Shell command to verify the build after agent work")
    test_cmd: str | None = Field(default=None, description="Shell command to run tests after agent work")
    budget_limit_usd: float = Field(default=0.0, description="Maximum USD budget for this pipeline. 0 means unlimited.")


class RestartPipelineRequest(BaseModel):
    """Optional request body for restarting a pipeline."""

    clean_worktrees: bool = True


class ExecuteRequest(BaseModel):
    """Optional: edited task graph to execute instead of the planned one."""

    tasks: list[dict] | None = None  # if provided, overrides planned graph


class PipelineResponse(BaseModel):
    """Response returned when a pipeline is created."""

    pipeline_id: str


class TaskStatusResponse(BaseModel):
    """Response for pipeline status queries."""

    pipeline_id: str
    phase: str
    tasks: list[dict] = Field(default_factory=list)
    timeline: list[dict] = Field(default_factory=list)
    pr_url: str | None = None
    planner_output: list[str] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    planner_cost_usd: float = 0.0
    budget_limit_usd: float = 0.0
    estimated_cost_usd: float = 0.0


class TaskListItem(BaseModel):
    """Item in the task list response."""

    pipeline_id: str
    description: str
    project_path: str
    phase: str
