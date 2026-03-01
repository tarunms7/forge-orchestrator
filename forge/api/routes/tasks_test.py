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


@pytest.fixture
async def client_with_app():
    """Like `client`, but also yields the app for direct DB access."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-stats",
    )

    await app.state.db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, app

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


# ── Event enrichment tests ────────────────────────────────────────


class TestGetTaskStatusWithEvents:
    """Tests for GET /tasks/{pipeline_id} event enrichment."""

    async def test_get_task_status_includes_timeline(self, client):
        """GET /tasks/{id} should include timeline field."""
        token = await _register_and_get_token(client, email="timeline@example.com")
        headers = _auth_header(token)

        create_resp = await client.post(
            "/api/tasks",
            json={"description": "Events test", "project_path": "/tmp/proj"},
            headers=headers,
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        resp = await client.get(f"/api/tasks/{pipeline_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "timeline" in data
        assert isinstance(data["timeline"], list)


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


# ── Resume endpoint tests ────────────────────────────────────────


class TestResumeEndpoint:
    """Tests for POST /tasks/{pipeline_id}/resume."""

    async def test_resume_requires_auth(self, client):
        """POST /tasks/{id}/resume without auth should return 401."""
        resp = await client.post("/api/tasks/some-id/resume")
        assert resp.status_code == 401

    async def test_resume_nonexistent_returns_404(self, client):
        """POST /tasks/{id}/resume for unknown pipeline should return 404."""
        token = await _register_and_get_token(client, email="resume@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent/resume",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404


# ── Cancel endpoint tests ────────────────────────────────────────


class TestCancelEndpoint:
    """Tests for POST /tasks/{pipeline_id}/cancel."""

    async def test_cancel_requires_auth(self, client):
        """POST /tasks/{id}/cancel without auth should return 401."""
        resp = await client.post("/api/tasks/some-id/cancel")
        assert resp.status_code == 401

    async def test_cancel_nonexistent_returns_404(self, client):
        """POST /tasks/{id}/cancel for unknown pipeline should return 404."""
        token = await _register_and_get_token(client, email="cancel@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent/cancel",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404


# ── Retry task endpoint tests ────────────────────────────────────


class TestRetryTaskEndpoint:
    """Tests for POST /tasks/{pipeline_id}/{task_id}/retry."""

    async def test_retry_requires_auth(self, client):
        """POST /tasks/{pipe}/{task}/retry without auth should return 401."""
        resp = await client.post("/api/tasks/some-pipe/some-task/retry")
        assert resp.status_code == 401

    async def test_retry_nonexistent_pipeline_returns_404(self, client):
        """POST /tasks/{pipe}/{task}/retry for unknown pipeline should return 404."""
        token = await _register_and_get_token(client, email="retry@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent/bad-task/retry",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404


# ── Stats endpoint tests ─────────────────────────────────────────────


class TestStats:
    """Tests for GET /api/tasks/stats, focusing on avg_duration_secs."""

    async def test_stats_no_pipelines_avg_duration_is_none(self, client_with_app):
        """With no pipelines, avg_duration_secs should be None."""
        client, _app = client_with_app
        token = await _register_and_get_token(client, email="stats-empty@example.com")

        resp = await client.get("/api/tasks/stats", headers=_auth_header(token))

        assert resp.status_code == 200
        data = resp.json()
        assert data["avg_duration_secs"] is None

    async def test_stats_avg_duration_computed_from_complete_pipelines(self, client_with_app):
        """avg_duration_secs should be the mean of complete pipeline durations.

        Pipeline A: 60 s  |  Pipeline B: 120 s  ->  average = 90.0 s
        """
        import uuid
        from datetime import datetime, timedelta, timezone

        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="stats-dur@example.com")
        headers = _auth_header(token)

        # Decode the JWT to obtain the authenticated user's ID so we can
        # insert pipeline rows directly in the DB (bypasses daemon planning,
        # which would race with our status updates).
        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        # Build deterministic timestamps: A = 60 s, B = 120 s -> avg = 90 s.
        base = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        created_a = base.isoformat()
        completed_a = (base + timedelta(seconds=60)).isoformat()

        base_b = base + timedelta(hours=1)
        created_b = base_b.isoformat()
        completed_b = (base_b + timedelta(seconds=120)).isoformat()

        # Insert complete pipeline rows directly, owned by the registered user.
        pipeline_id_a = str(uuid.uuid4())
        pipeline_id_b = str(uuid.uuid4())

        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pipeline_id_a,
                description="Pipeline A",
                project_dir="/proj/a",
                status="complete",
                user_id=user_id,
                created_at=created_a,
                completed_at=completed_a,
            ))
            session.add(PipelineRow(
                id=pipeline_id_b,
                description="Pipeline B",
                project_dir="/proj/b",
                status="complete",
                user_id=user_id,
                created_at=created_b,
                completed_at=completed_b,
            ))
            await session.commit()

        resp = await client.get("/api/tasks/stats", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["completed"] == 2
        assert data["avg_duration_secs"] == 90.0
