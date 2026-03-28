"""Pydantic request/response schemas for the Forge REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RepoEntry(BaseModel):
    """A repository entry in a multi-repo workspace."""

    id: str
    path: str
    base_branch: str | None = None


class CreateTaskRequest(BaseModel):
    """Request body for creating a new pipeline task."""

    description: str
    project_path: str
    extra_dirs: list[str] = Field(default_factory=list)
    model_strategy: str = "auto"
    images: list[str] = Field(
        default_factory=list,
        description="Base64-encoded image data URIs (e.g. data:image/png;base64,...)",
    )
    branch_name: str | None = None
    build_cmd: str | None = Field(
        default=None, description="Shell command to verify the build after agent work"
    )
    test_cmd: str | None = Field(
        default=None, description="Shell command to run tests after agent work"
    )
    budget_limit_usd: float = Field(
        default=0.0, description="Maximum USD budget for this pipeline. 0 means unlimited."
    )
    require_approval: bool | None = None
    template_id: str | None = Field(
        default=None, description="Pipeline template ID (built-in or user-created)"
    )
    quality_preset: str | None = Field(
        default=None, description="Quality preset: fast, balanced, or thorough"
    )
    repos: list[RepoEntry] | None = Field(
        default=None,
        description="List of repositories for multi-repo workspaces. None for single-repo backward compat.",
    )


class RestartPipelineRequest(BaseModel):
    """Optional request body for restarting a pipeline."""

    clean_worktrees: bool = True


class EditedTaskDefinition(BaseModel):
    """A single task as edited by the user."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str
    files: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    complexity: Literal["low", "medium", "high"] = "medium"


class ExecuteRequest(BaseModel):
    """Optional: edited task graph to execute instead of the planned one."""

    tasks: list[EditedTaskDefinition] | None = None


class RejectRequest(BaseModel):
    """Request body for rejecting a task awaiting approval."""

    reason: str | None = None


class CIFixRequest(BaseModel):
    """Request body for manually triggering CI auto-fix."""

    max_retries: int = Field(default=3, ge=1, le=10, description="Max fix attempts")
    budget_usd: float = Field(default=0.0, ge=0, description="Budget for fix agents (0=unlimited)")


class PipelineResponse(BaseModel):
    """Response returned when a pipeline is created."""

    pipeline_id: str
    repos: list[dict] | None = Field(
        default=None,
        description="List of repo dicts included in this pipeline. None for single-repo pipelines.",
    )


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
    github_issue_url: str | None = None
    github_issue_number: int | None = None
    repo_id: str = Field(
        default="default", description="Repository identifier for this status context."
    )
    ci_fix_status: str | None = None
    ci_fix_attempt: int = 0
    ci_fix_max_retries: int = 3
    ci_fix_cost_usd: float = 0.0


class TaskListItem(BaseModel):
    """Item in the task list response."""

    pipeline_id: str
    description: str
    project_path: str
    phase: str
    repo_id: str = Field(
        default="default", description="Repository identifier. 'default' for single-repo pipelines."
    )


# ── Template schemas ──────────────────────────────────────────────────


class ReviewConfigSchema(BaseModel):
    """Review pipeline overrides for a template."""

    skip_l2: bool = False
    extra_review_pass: bool = False
    custom_review_focus: str = ""


class CreateUserTemplateRequest(BaseModel):
    """Request body for creating a user-owned pipeline template."""

    name: str
    description: str
    icon: str
    model_strategy: str = "auto"
    planner_prompt_modifier: str = ""
    agent_prompt_modifier: str = ""
    review_config: ReviewConfigSchema = Field(default_factory=ReviewConfigSchema)
    build_cmd: str | None = None
    test_cmd: str | None = None
    max_tasks: int | None = None
    default_complexity: Literal["low", "medium", "high"] | None = None


class UpdateUserTemplateRequest(BaseModel):
    """Request body for updating a user-owned pipeline template. All fields optional."""

    name: str | None = None
    description: str | None = None
    icon: str | None = None
    model_strategy: str | None = None
    planner_prompt_modifier: str | None = None
    agent_prompt_modifier: str | None = None
    review_config: ReviewConfigSchema | None = None
    build_cmd: str | None = None
    test_cmd: str | None = None
    max_tasks: int | None = None
    default_complexity: Literal["low", "medium", "high"] | None = None


class TemplateResponse(BaseModel):
    """Response model for a single pipeline template."""

    id: str
    name: str
    description: str
    icon: str
    model_strategy: str = "auto"
    planner_prompt_modifier: str = ""
    agent_prompt_modifier: str = ""
    review_config: ReviewConfigSchema = Field(default_factory=ReviewConfigSchema)
    build_cmd: str | None = None
    test_cmd: str | None = None
    max_tasks: int | None = None
    default_complexity: str | None = None
    is_builtin: bool = True
    user_id: str | None = None
    created_at: datetime | None = None


class TemplateListResponse(BaseModel):
    """Response model for listing all templates."""

    builtin: list[TemplateResponse]
    user: list[TemplateResponse]


# ── Contract schemas ─────────────────────────────────────────────────


class FieldSpecResponse(BaseModel):
    """A single field specification in a contract."""

    name: str
    type: str
    required: bool
    description: str


class ApiContractResponse(BaseModel):
    """A single API endpoint contract."""

    id: str
    method: str
    path: str
    description: str
    request_body: list[FieldSpecResponse] | None
    response_body: list[FieldSpecResponse]
    response_example: str
    auth_required: bool
    producer_task_id: str
    consumer_task_ids: list[str]


class TypeContractResponse(BaseModel):
    """A shared data structure contract."""

    name: str
    description: str
    field_specs: list[FieldSpecResponse]
    used_by_tasks: list[str]


class ContractSetResponse(BaseModel):
    """Response model for GET /api/tasks/{pipeline_id}/contracts."""

    api_contracts: list[ApiContractResponse]
    type_contracts: list[TypeContractResponse]
