"""Pydantic request/response schemas for the Forge REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateTaskRequest(BaseModel):
    """Request body for creating a new pipeline task."""

    description: str
    project_path: str
    extra_dirs: list[str] = Field(default_factory=list)


class PipelineResponse(BaseModel):
    """Response returned when a pipeline is created."""

    pipeline_id: str


class TaskStatusResponse(BaseModel):
    """Response for pipeline status queries."""

    pipeline_id: str
    phase: str
    tasks: list[dict] = Field(default_factory=list)


class TaskListItem(BaseModel):
    """Item in the task list response."""

    pipeline_id: str
    description: str
    project_path: str
    phase: str
