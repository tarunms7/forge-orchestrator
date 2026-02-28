"""Integration tests for task REST endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-tasks",
    )

    # Manually init since ASGITransport doesn't trigger lifespan
    await app.state.db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.db.close()


async def _register_and_get_token(
    client: AsyncClient,
    email: str = "tasks-user@example.com",
    display_name: str = "Tasks User",
) -> str:
    """Helper: register a user and return the access token."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "securepass",
            "display_name": display_name,
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Authentication tests ─────────────────────────────────────────────


class TestTaskAuth:
    """Task endpoints require valid JWT auth."""

    async def test_create_task_without_auth_returns_401(self, client):
        """POST /tasks without Authorization header should return 401."""
        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Build feature X",
                "project_path": "/some/path",
            },
        )
        assert resp.status_code == 401

    async def test_get_tasks_without_auth_returns_401(self, client):
        """GET /tasks without Authorization header should return 401."""
        resp = await client.get("/api/tasks")
        assert resp.status_code == 401

    async def test_get_task_status_without_auth_returns_401(self, client):
        """GET /tasks/{id} without Authorization header should return 401."""
        resp = await client.get("/api/tasks/some-pipeline-id")
        assert resp.status_code == 401

    async def test_invalid_token_returns_401(self, client):
        """Requests with an invalid/expired token should return 401."""
        resp = await client.get(
            "/api/tasks",
            headers={"Authorization": "Bearer bad.token.here"},
        )
        assert resp.status_code == 401


# ── CRUD tests ───────────────────────────────────────────────────────


class TestCreateTask:
    """Tests for POST /tasks."""

    async def test_create_task_returns_pipeline_id(self, client):
        """POST /tasks should return a pipeline_id."""
        token = await _register_and_get_token(client)
        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Implement dark mode",
                "project_path": "/home/user/project",
            },
            headers=_auth_header(token),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "pipeline_id" in data
        assert len(data["pipeline_id"]) > 0

    async def test_create_task_with_extra_dirs(self, client):
        """POST /tasks with extra_dirs should succeed."""
        token = await _register_and_get_token(client)
        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Refactor auth module",
                "project_path": "/home/user/project",
                "extra_dirs": ["/home/user/shared-lib"],
            },
            headers=_auth_header(token),
        )
        assert resp.status_code == 201


class TestGetTaskStatus:
    """Tests for GET /tasks/{pipeline_id}."""

    async def test_get_task_status(self, client):
        """GET /tasks/{pipeline_id} should return task details."""
        token = await _register_and_get_token(client)

        # Create a task first
        create_resp = await client.post(
            "/api/tasks",
            json={
                "description": "Add tests",
                "project_path": "/tmp/project",
            },
            headers=_auth_header(token),
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        # Get status
        resp = await client.get(
            f"/api/tasks/{pipeline_id}",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_id"] == pipeline_id
        assert "phase" in data
        assert "tasks" in data

    async def test_get_nonexistent_task_returns_404(self, client):
        """GET /tasks/{id} for unknown id should return 404."""
        token = await _register_and_get_token(client)
        resp = await client.get(
            "/api/tasks/nonexistent-id",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404


class TestListTasks:
    """Tests for GET /tasks."""

    async def test_list_tasks_empty(self, client):
        """GET /tasks should return empty list when no tasks exist."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/tasks", headers=_auth_header(token))
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_tasks_returns_created(self, client):
        """GET /tasks should return all tasks for the authenticated user."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        # Create two tasks
        await client.post(
            "/api/tasks",
            json={"description": "Task A", "project_path": "/p1"},
            headers=headers,
        )
        await client.post(
            "/api/tasks",
            json={"description": "Task B", "project_path": "/p2"},
            headers=headers,
        )

        resp = await client.get("/api/tasks", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        descriptions = {t["description"] for t in data}
        assert descriptions == {"Task A", "Task B"}


# ── IDOR tests ──────────────────────────────────────────────────────


class TestTaskIDOR:
    """IDOR protection: users cannot access other users' pipelines."""

    async def test_get_task_as_different_user_returns_404(self, client):
        """GET /tasks/{id} for a pipeline owned by another user should return 404."""
        # Register user A and create a task
        token_a = await _register_and_get_token(client, email="usera@example.com")
        create_resp = await client.post(
            "/api/tasks",
            json={"description": "User A task", "project_path": "/proj"},
            headers=_auth_header(token_a),
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        # Register user B and try to access user A's task
        token_b = await _register_and_get_token(client, email="userb@example.com")
        resp = await client.get(
            f"/api/tasks/{pipeline_id}",
            headers=_auth_header(token_b),
        )
        assert resp.status_code == 404
