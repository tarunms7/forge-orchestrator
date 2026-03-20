"""Pydantic models for Forge. All data flows through typed schemas."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field, field_validator

_REPO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskState(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    MERGING = "merging"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"
    BLOCKED = "blocked"


class AgentState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"


@dataclass(frozen=True)
class RepoConfig:
    """Immutable configuration for a single repository in a workspace."""

    id: str  # unique identifier (e.g., 'backend', 'frontend')
    path: str  # absolute path to the repo root
    base_branch: str  # default branch for this repo (e.g., 'main', 'develop')


class TaskDefinition(BaseModel):
    """A task as defined by the planner. Immutable spec."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str
    files: list[str]
    depends_on: list[str] = Field(default_factory=list)
    complexity: Complexity = Complexity.MEDIUM
    # Integration hints from planner (optional for backward compat)
    integration_hints: list[dict] | None = None
    repo: str = "default"  # which repo this task operates in

    @field_validator("repo")
    @classmethod
    def repo_id_valid(cls, v: str) -> str:
        if not v or not _REPO_ID_RE.match(v):
            raise ValueError(
                f"repo must be non-empty and match ^[a-z0-9][a-z0-9-]*$, got {v!r}"
            )
        return v

    @field_validator("files")
    @classmethod
    def files_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Task must declare at least one file")
        return v


class TaskGraph(BaseModel):
    """The planner's output: a validated set of tasks with dependencies."""

    tasks: list[TaskDefinition] = Field(min_length=1)
    conventions: dict | None = None
    # Cross-task integration hints (collected from all tasks)
    integration_hints: list[dict] | None = None


class TaskRecord(BaseModel):
    """Runtime state of a task. Mutable projection."""

    id: str
    title: str
    description: str
    files: list[str]
    depends_on: list[str]
    complexity: Complexity
    state: TaskState = TaskState.TODO
    assigned_agent: str | None = None
    retry_count: int = 0
    branch_name: str | None = None
    worktree_path: str | None = None
    repo: str = "default"

    @classmethod
    def from_definition(cls, defn: TaskDefinition) -> "TaskRecord":
        return cls(
            id=defn.id,
            title=defn.title,
            description=defn.description,
            files=list(defn.files),
            depends_on=list(defn.depends_on),
            complexity=defn.complexity,
            repo=defn.repo,
        )


class AgentRecord(BaseModel):
    """Runtime state of an agent."""

    id: str
    state: AgentState = AgentState.IDLE
    current_task: str | None = None


def row_to_record(row) -> TaskRecord:
    return TaskRecord(
        id=row.id, title=row.title, description=row.description,
        files=row.files, depends_on=row.depends_on, complexity=row.complexity,
        state=TaskState(row.state),
        assigned_agent=row.assigned_agent,
        retry_count=row.retry_count,
        repo=getattr(row, 'repo_id', None) or 'default',
    )


def row_to_agent(row) -> AgentRecord:
    return AgentRecord(
        id=row.id,
        state=AgentState(row.state),
        current_task=row.current_task,
    )
