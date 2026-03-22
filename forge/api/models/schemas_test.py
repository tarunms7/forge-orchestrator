"""Tests for multi-repo schema fields in forge/api/models/schemas.py."""

from forge.api.models.schemas import (
    CreateTaskRequest,
    PipelineResponse,
    RepoEntry,
    TaskListItem,
    TaskStatusResponse,
)


def test_create_task_request_with_repos() -> None:
    """CreateTaskRequest accepts optional repos list of RepoEntry dicts."""
    req = CreateTaskRequest(
        description="Add feature",
        project_path="/home/user/project",
        repos=[
            RepoEntry(id="backend", path="/home/user/project/backend", base_branch="main"),
            RepoEntry(id="frontend", path="/home/user/project/frontend"),
        ],
    )
    assert req.repos is not None
    assert len(req.repos) == 2
    assert req.repos[0].id == "backend"
    assert req.repos[0].path == "/home/user/project/backend"
    assert req.repos[0].base_branch == "main"
    assert req.repos[1].id == "frontend"
    assert req.repos[1].base_branch is None


def test_create_task_request_without_repos() -> None:
    """CreateTaskRequest.repos defaults to None for backward compat."""
    req = CreateTaskRequest(
        description="Fix bug",
        project_path="/home/user/project",
    )
    assert req.repos is None


def test_task_status_response_includes_repo_id() -> None:
    """TaskStatusResponse has repo_id defaulting to 'default'."""
    resp = TaskStatusResponse(pipeline_id="abc-123", phase="running")
    assert resp.repo_id == "default"


def test_task_status_response_repo_id_can_be_set() -> None:
    """TaskStatusResponse.repo_id can be set to a custom value."""
    resp = TaskStatusResponse(pipeline_id="abc-123", phase="running", repo_id="backend")
    assert resp.repo_id == "backend"


def test_task_list_item_includes_repo_id() -> None:
    """TaskListItem has repo_id defaulting to 'default'."""
    item = TaskListItem(
        pipeline_id="abc-123",
        description="Fix bug",
        project_path="/home/user/project",
        phase="running",
    )
    assert item.repo_id == "default"


def test_task_list_item_repo_id_can_be_set() -> None:
    """TaskListItem.repo_id can be set to a custom value."""
    item = TaskListItem(
        pipeline_id="abc-123",
        description="Fix bug",
        project_path="/home/user/project",
        phase="running",
        repo_id="frontend",
    )
    assert item.repo_id == "frontend"


def test_pipeline_response_includes_repos() -> None:
    """PipelineResponse accepts optional repos list."""
    resp = PipelineResponse(
        pipeline_id="abc-123",
        repos=[
            {"id": "backend", "path": "/home/user/project/backend", "base_branch": "main"},
            {"id": "frontend", "path": "/home/user/project/frontend", "base_branch": None},
        ],
    )
    assert resp.repos is not None
    assert len(resp.repos) == 2
    assert resp.repos[0]["id"] == "backend"
    assert resp.repos[1]["id"] == "frontend"


def test_pipeline_response_without_repos() -> None:
    """PipelineResponse.repos defaults to None for single-repo pipelines."""
    resp = PipelineResponse(pipeline_id="abc-123")
    assert resp.repos is None


def test_repo_entry_base_branch_optional() -> None:
    """RepoEntry.base_branch is optional and defaults to None."""
    entry = RepoEntry(id="backend", path="/home/user/project/backend")
    assert entry.id == "backend"
    assert entry.path == "/home/user/project/backend"
    assert entry.base_branch is None
