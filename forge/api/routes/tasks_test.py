"""Integration tests for task REST endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

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

    async def test_cancel_returns_cancelled_task_ids(self, client_with_app):
        """POST /tasks/{id}/cancel should return list of cancelled task IDs."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="cancel-ids@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Cancel test", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id="ct1", title="T1", description="D", files=[], depends_on=[],
                complexity="low", state="in_progress", pipeline_id=pid,
            ))
            session.add(TaskRow(
                id="ct2", title="T2", description="D", files=[], depends_on=[],
                complexity="low", state="todo", pipeline_id=pid,
            ))
            session.add(TaskRow(
                id="ct3", title="T3", description="D", files=[], depends_on=[],
                complexity="low", state="done", pipeline_id=pid,
            ))
            await session.commit()

        resp = await client.post(f"/api/tasks/{pid}/cancel", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["pipeline_id"] == pid
        # ct1 and ct2 should be cancelled, ct3 (done) should not
        assert set(data["tasks_cancelled"]) == {"ct1", "ct2"}

    async def test_cancel_already_cancelled_returns_already(self, client_with_app):
        """POST /tasks/{id}/cancel on already-cancelled pipeline returns already_cancelled."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="cancel-again@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Already cancelled", project_dir="/proj",
                status="cancelled", user_id=user_id,
            ))
            await session.commit()

        resp = await client.post(f"/api/tasks/{pid}/cancel", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "already_cancelled"


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

    async def test_stats_total_spend_null_when_no_cost_events(self, client_with_app):
        """total_spend_usd should be None when no cost events exist."""
        client, _app = client_with_app
        token = await _register_and_get_token(client, email="stats-no-cost@example.com")

        resp = await client.get("/api/tasks/stats", headers=_auth_header(token))

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_spend_usd"] is None

    async def test_stats_total_spend_aggregated_from_cost_events(self, client_with_app):
        """total_spend_usd should sum cost_usd from task:cost_update events."""
        import uuid

        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="stats-cost@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        # Create two pipelines owned by this user.
        pid_a = str(uuid.uuid4())
        pid_b = str(uuid.uuid4())

        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid_a, description="Cost A", project_dir="/proj/a",
                status="complete", user_id=user_id,
            ))
            session.add(PipelineRow(
                id=pid_b, description="Cost B", project_dir="/proj/b",
                status="complete", user_id=user_id,
            ))
            await session.commit()

        # Log cost events across both pipelines.
        await db.log_event(
            pipeline_id=pid_a, task_id="t1",
            event_type="task:cost_update", payload={"cost_usd": 0.05},
        )
        await db.log_event(
            pipeline_id=pid_a, task_id="t2",
            event_type="task:cost_update", payload={"cost_usd": 0.10},
        )
        await db.log_event(
            pipeline_id=pid_b, task_id="t3",
            event_type="task:cost_update", payload={"cost_usd": 0.25},
        )
        # Non-cost event should be ignored.
        await db.log_event(
            pipeline_id=pid_a, task_id="t1",
            event_type="task:agent_output", payload={"line": "hello"},
        )

        resp = await client.get("/api/tasks/stats", headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_spend_usd"] == 0.4


# ── Image upload tests ────────────────────────────────────────────


class TestCreateTaskWithImages:
    """Tests for image upload support in POST /tasks."""

    async def test_create_task_with_images_appends_note(self, client_with_app):
        """Images should append a note to the stored description."""

        client, app = client_with_app
        token = await _register_and_get_token(client, email="img@example.com")
        headers = _auth_header(token)

        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Build feature X",
                "project_path": "/some/path",
                "images": ["data:image/png;base64,abc123", "data:image/jpeg;base64,def456"],
            },
            headers=headers,
        )
        assert resp.status_code == 201
        pipeline_id = resp.json()["pipeline_id"]

        # Verify description in DB includes the image note.
        db = app.state.db
        pipeline = await db.get_pipeline(pipeline_id)
        assert "[2 image(s) attached]" in pipeline.description

        # Verify images stored in app.state.
        assert pipeline_id in app.state.pipeline_images
        assert len(app.state.pipeline_images[pipeline_id]) == 2

    async def test_create_task_without_images_no_note(self, client_with_app):
        """Without images, description should remain unchanged."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="noimg@example.com")
        headers = _auth_header(token)

        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Simple task",
                "project_path": "/some/path",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        pipeline_id = resp.json()["pipeline_id"]

        db = app.state.db
        pipeline = await db.get_pipeline(pipeline_id)
        assert pipeline.description == "Simple task"
        assert "image(s) attached" not in pipeline.description

    async def test_create_task_images_default_empty(self, client):
        """CreateTaskRequest.images should default to empty list."""
        token = await _register_and_get_token(client, email="default-img@example.com")
        resp = await client.post(
            "/api/tasks",
            json={
                "description": "No images field",
                "project_path": "/path",
            },
            headers=_auth_header(token),
        )
        assert resp.status_code == 201


# ── Restart endpoint tests ────────────────────────────────────────


class TestRestartEndpoint:
    """Tests for POST /tasks/{pipeline_id}/restart."""

    async def test_restart_requires_auth(self, client):
        """POST /tasks/{id}/restart without auth should return 401."""
        resp = await client.post("/api/tasks/some-id/restart")
        assert resp.status_code == 401

    async def test_restart_nonexistent_returns_404(self, client):
        """POST /tasks/{id}/restart for unknown pipeline should return 404."""
        token = await _register_and_get_token(client, email="restart-404@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent/restart",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_restart_resets_pipeline(self, client_with_app):
        """POST /tasks/{id}/restart should reset pipeline to pending and clear state."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="restart-ok@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Restart me", project_dir="/proj",
                status="error", user_id=user_id,
                task_graph_json='{"tasks": []}',
            ))
            session.add(TaskRow(
                id="rt1", title="T1", description="D", files=[], depends_on=[],
                complexity="low", state="error", pipeline_id=pid,
            ))
            await session.commit()

        # Log an event so we can verify it gets deleted
        await db.log_event(pipeline_id=pid, task_id="rt1", event_type="agent_output", payload={"line": "hi"})

        resp = await client.post(f"/api/tasks/{pid}/restart", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting"
        assert data["pipeline_id"] == pid
        assert data["tasks_reset"] == 1
        assert data["events_deleted"] == 1

        # Pipeline should be in planning state (restart sets to pending, then planning)
        pipeline = await db.get_pipeline(pid)
        assert pipeline.status == "planning"
        assert pipeline.task_graph_json is None

    async def test_restart_idor_protection(self, client_with_app):
        """POST /tasks/{id}/restart should 404 for another user's pipeline."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app

        # Create user A and their pipeline
        token_a = await _register_and_get_token(client, email="restart-a@example.com")
        payload_a = decode_token(token_a, secret="test-secret-for-stats")
        user_id_a = payload_a["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="User A pipeline", project_dir="/proj",
                status="error", user_id=user_id_a,
            ))
            await session.commit()

        # User B tries to restart user A's pipeline
        token_b = await _register_and_get_token(client, email="restart-b@example.com")
        resp = await client.post(
            f"/api/tasks/{pid}/restart",
            headers=_auth_header(token_b),
        )
        assert resp.status_code == 404


# ── Branch name passthrough tests ────────────────────────────────────


class TestBranchNamePassthrough:
    """Tests for branch_name support in task creation."""

    async def test_create_task_with_branch_name(self, client_with_app):
        """POST /tasks with branch_name should store it in the pipeline."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="branch@example.com")
        headers = _auth_header(token)

        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Feature with branch",
                "project_path": "/proj",
                "branch_name": "feat/my-feature",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        pipeline_id = resp.json()["pipeline_id"]

        db = app.state.db
        pipeline = await db.get_pipeline(pipeline_id)
        assert pipeline.branch_name == "feat/my-feature"

    async def test_create_task_without_branch_name(self, client_with_app):
        """POST /tasks without branch_name should default to None."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="no-branch@example.com")
        headers = _auth_header(token)

        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Feature without branch",
                "project_path": "/proj",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        pipeline_id = resp.json()["pipeline_id"]

        db = app.state.db
        pipeline = await db.get_pipeline(pipeline_id)
        assert pipeline.branch_name is None


# ── PR title generation tests ────────────────────────────────────────


class TestSanitizePrTitle:
    """Tests for _sanitize_pr_title heuristic fallback."""

    def test_simple_description(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        result = _sanitize_pr_title("Fix the login button")
        assert result == "fix the login button"

    def test_strips_trailing_punctuation(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        result = _sanitize_pr_title("Can we fix the copy button alignment??")
        assert result == "can we fix the copy button alignment"

    def test_takes_first_sentence(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        result = _sanitize_pr_title(
            "We need to fix some bugs. The lines changed by each agent are wrong."
        )
        assert result == "we need to fix some bugs"

    def test_stops_at_newline(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        result = _sanitize_pr_title(
            "Fix copy button alignment\nAlso fix the commit message formatting"
        )
        assert result == "fix copy button alignment"

    def test_strips_numbered_list(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        result = _sanitize_pr_title(
            "We need to fix some bugs: 1. The lines changed by each agent are wrong"
        )
        assert result == "we need to fix some bugs"

    def test_strips_bullet_markers(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        result = _sanitize_pr_title("- Fix button alignment and design")
        assert result == "fix button alignment and design"

    def test_truncates_long_description(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        long_desc = "Implement the full user authentication system with OAuth2 support and refresh tokens and session management"
        result = _sanitize_pr_title(long_desc)
        assert len(result) <= 50

    def test_empty_description_fallback(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        result = _sanitize_pr_title("")
        assert isinstance(result, str)

    def test_asterisk_bullet_stripped(self):
        from forge.api.routes.tasks import _sanitize_pr_title

        result = _sanitize_pr_title("* Refactor auth module")
        assert result == "refactor auth module"


class TestGeneratePrTitle:
    """Tests for _generate_pr_title with LLM and fallback."""

    async def test_returns_llm_title_on_success(self):
        """When sdk_query returns a valid title, use it."""
        from forge.api.routes.tasks import _generate_pr_title

        mock_result = MagicMock()
        mock_result.result = "fix: copy button alignment and commit formatting"

        with patch("forge.core.sdk_helpers.sdk_query", new_callable=AsyncMock, return_value=mock_result):
            title = await _generate_pr_title(
                "Fix copy button and commit messages",
                "- Fix copy button\n- Fix commit formatting",
            )
        assert title == "fix: copy button alignment and commit formatting"

    async def test_strips_forge_prefix_from_llm(self):
        """If LLM includes 'forge:' prefix, strip it."""
        from forge.api.routes.tasks import _generate_pr_title

        mock_result = MagicMock()
        mock_result.result = "forge: fix button alignment"

        with patch("forge.core.sdk_helpers.sdk_query", new_callable=AsyncMock, return_value=mock_result):
            title = await _generate_pr_title("Fix button", "- Fix button")
        assert title == "fix button alignment"

    async def test_strips_quotes_from_llm(self):
        """If LLM wraps title in quotes, strip them."""
        from forge.api.routes.tasks import _generate_pr_title

        mock_result = MagicMock()
        mock_result.result = '"fix: improve error handling"'

        with patch("forge.core.sdk_helpers.sdk_query", new_callable=AsyncMock, return_value=mock_result):
            title = await _generate_pr_title("Improve error handling", "")
        assert title == "fix: improve error handling"

    async def test_falls_back_on_sdk_exception(self):
        """When sdk_query raises, fall back to heuristic."""
        from forge.api.routes.tasks import _generate_pr_title

        with patch("forge.core.sdk_helpers.sdk_query", new_callable=AsyncMock, side_effect=RuntimeError("SDK down")):
            title = await _generate_pr_title("Fix the login button", "")
        assert title == "fix the login button"

    async def test_falls_back_on_empty_result(self):
        """When sdk_query returns None, fall back to heuristic."""
        from forge.api.routes.tasks import _generate_pr_title

        with patch("forge.core.sdk_helpers.sdk_query", new_callable=AsyncMock, return_value=None):
            title = await _generate_pr_title("Fix the login button", "")
        assert title == "fix the login button"

    async def test_falls_back_on_empty_result_text(self):
        """When result.result is empty string, fall back to heuristic."""
        from forge.api.routes.tasks import _generate_pr_title

        mock_result = MagicMock()
        mock_result.result = ""

        with patch("forge.core.sdk_helpers.sdk_query", new_callable=AsyncMock, return_value=mock_result):
            title = await _generate_pr_title("Fix the login button", "")
        assert title == "fix the login button"


# ── Execute with edited task graph tests ─────────────────────────────


class TestExecuteWithEditedGraph:
    """Tests for POST /tasks/{pipeline_id}/execute with edited task graph."""

    async def test_execute_requires_auth(self, client):
        """POST /tasks/{id}/execute without auth should return 401."""
        resp = await client.post("/api/tasks/some-id/execute")
        assert resp.status_code == 401

    async def test_execute_nonexistent_returns_404(self, client):
        """POST /tasks/{id}/execute for unknown pipeline should return 404."""
        token = await _register_and_get_token(client, email="exec@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent/execute",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_execute_with_invalid_graph_returns_422(self, client_with_app):
        """POST /tasks/{id}/execute with cyclic deps should return 422."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="exec-invalid@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db

        # Create pipeline in DB
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Test exec", project_dir="/proj",
                status="planned", user_id=user_id,
            ))
            await session.commit()

        # Store a pending graph (mocked daemon)
        mock_daemon = MagicMock()
        from forge.core.models import TaskDefinition, TaskGraph, Complexity
        original_graph = TaskGraph(tasks=[
            TaskDefinition(id="t1", title="T1", description="D1", files=["f.py"], complexity=Complexity.LOW),
        ])
        app.state.pending_graphs[pid] = (original_graph, mock_daemon)

        # Submit edited graph with cyclic dependencies
        resp = await client.post(
            f"/api/tasks/{pid}/execute",
            json={
                "tasks": [
                    {"id": "t1", "title": "T1", "description": "D1", "files": ["a.py"],
                     "depends_on": ["t2"], "complexity": "low"},
                    {"id": "t2", "title": "T2", "description": "D2", "files": ["b.py"],
                     "depends_on": ["t1"], "complexity": "medium"},
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 422
        assert "Invalid task graph" in resp.json()["detail"]

    async def test_execute_with_valid_edited_graph(self, client_with_app):
        """POST /tasks/{id}/execute with valid edited tasks should return 202."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="exec-valid@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db

        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Test exec valid", project_dir="/proj",
                status="planned", user_id=user_id,
            ))
            await session.commit()

        # Store a pending graph with mocked daemon
        mock_daemon = MagicMock()
        mock_daemon.execute = AsyncMock()
        from forge.core.models import TaskDefinition, TaskGraph, Complexity
        original_graph = TaskGraph(tasks=[
            TaskDefinition(id="t1", title="T1", description="D1", files=["f.py"], complexity=Complexity.LOW),
        ])
        app.state.pending_graphs[pid] = (original_graph, mock_daemon)

        resp = await client.post(
            f"/api/tasks/{pid}/execute",
            json={
                "tasks": [
                    {"id": "t1", "title": "Task One", "description": "Do A", "files": ["a.py"],
                     "depends_on": [], "complexity": "low"},
                    {"id": "t2", "title": "Task Two", "description": "Do B", "files": ["b.py"],
                     "depends_on": ["t1"], "complexity": "high"},
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "executing"
        assert data["pipeline_id"] == pid

    async def test_execute_without_edited_graph_uses_original(self, client_with_app):
        """POST /tasks/{id}/execute without tasks field uses original plan."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="exec-orig@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db

        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Test no edit", project_dir="/proj",
                status="planned", user_id=user_id,
            ))
            await session.commit()

        mock_daemon = MagicMock()
        mock_daemon.execute = AsyncMock()
        from forge.core.models import TaskDefinition, TaskGraph, Complexity
        original_graph = TaskGraph(tasks=[
            TaskDefinition(id="t1", title="T1", description="D1", files=["f.py"], complexity=Complexity.LOW),
        ])
        app.state.pending_graphs[pid] = (original_graph, mock_daemon)

        resp = await client.post(
            f"/api/tasks/{pid}/execute",
            headers=headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "executing"


# ── Task diff endpoint tests ─────────────────────────────────────────


class TestGetTaskDiff:
    """Tests for GET /tasks/{pipeline_id}/tasks/{task_id}/diff."""

    async def test_diff_requires_auth(self, client):
        """GET /tasks/{pid}/tasks/{tid}/diff without auth returns 401."""
        resp = await client.get("/api/tasks/some-pipe/tasks/some-task/diff")
        assert resp.status_code == 401

    async def test_diff_nonexistent_pipeline_returns_404(self, client):
        """GET diff for unknown pipeline returns 404."""
        token = await _register_and_get_token(client, email="diff-404@example.com")
        resp = await client.get(
            "/api/tasks/nonexistent/tasks/some-task/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_diff_wrong_task_state_returns_409(self, client_with_app):
        """GET diff for a task not in awaiting_approval returns 409."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="diff-409@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Diff test", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="in_progress",
                pipeline_id=pid,
            ))
            await session.commit()

        resp = await client.get(
            f"/api/tasks/{pid}/tasks/{tid}/diff",
            headers=headers,
        )
        assert resp.status_code == 409

    async def test_diff_missing_worktree_returns_410(self, client_with_app):
        """GET diff when worktree doesn't exist returns 410."""
        import json
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="diff-410@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Diff worktree", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="awaiting_approval",
                pipeline_id=pid,
                approval_context=json.dumps({
                    "worktree_path": "/nonexistent/path/worktree",
                    "pipeline_branch": "forge/pipeline-abc",
                }),
            ))
            await session.commit()

        resp = await client.get(
            f"/api/tasks/{pid}/tasks/{tid}/diff",
            headers=headers,
        )
        assert resp.status_code == 410

    async def test_diff_returns_diff_and_stats(self, client_with_app):
        """GET diff with valid worktree returns diff text and stats."""
        import json
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="diff-ok@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db

        # Use /tmp as a real existing directory
        import tempfile
        worktree_dir = tempfile.mkdtemp()

        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Diff OK", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="awaiting_approval",
                pipeline_id=pid,
                approval_context=json.dumps({
                    "worktree_path": worktree_dir,
                    "pipeline_branch": "main",
                }),
            ))
            await session.commit()

        # Mock _get_diff_vs_main to return a sample diff
        mock_diff = "diff --git a/foo.py b/foo.py\n+added line\n-removed line\n"
        with patch("forge.api.routes.tasks._get_diff_vs_main", return_value=mock_diff):
            resp = await client.get(
                f"/api/tasks/{pid}/tasks/{tid}/diff",
                headers=headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == tid
        assert "diff" in data
        assert data["stats"]["files_changed"] == 1
        assert data["stats"]["lines_added"] == 1
        assert data["stats"]["lines_removed"] == 1

        # Cleanup
        import shutil
        shutil.rmtree(worktree_dir, ignore_errors=True)


# ── Task approve endpoint tests ──────────────────────────────────────


class TestApproveTask:
    """Tests for POST /tasks/{pipeline_id}/tasks/{task_id}/approve."""

    async def test_approve_requires_auth(self, client):
        """POST approve without auth should return 401."""
        resp = await client.post("/api/tasks/some-pipe/tasks/some-task/approve")
        assert resp.status_code == 401

    async def test_approve_nonexistent_pipeline_returns_404(self, client):
        """POST approve for unknown pipeline returns 404."""
        token = await _register_and_get_token(client, email="approve-404@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent/tasks/some-task/approve",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_approve_wrong_state_returns_409(self, client_with_app):
        """POST approve for a task not in awaiting_approval returns 409."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="approve-409@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Approve test", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="done",
                pipeline_id=pid,
            ))
            await session.commit()

        resp = await client.post(
            f"/api/tasks/{pid}/tasks/{tid}/approve",
            headers=headers,
        )
        assert resp.status_code == 409

    async def test_approve_success_returns_202(self, client_with_app):
        """POST approve for task in awaiting_approval returns 202 and merging."""
        import json
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="approve-ok@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Approve OK", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="awaiting_approval",
                pipeline_id=pid,
                approval_context=json.dumps({
                    "worktree_path": "/tmp/wt",
                    "pipeline_branch": "forge/pipeline-abc",
                }),
            ))
            await session.commit()

        # Set up ws_manager mock
        app.state.ws_manager = AsyncMock()

        resp = await client.post(
            f"/api/tasks/{pid}/tasks/{tid}/approve",
            headers=headers,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "merging"
        assert data["task_id"] == tid

        # Verify task state was updated
        task = await db.get_task(tid)
        assert task.state == "merging"


# ── Task reject endpoint tests ───────────────────────────────────────


class TestRejectTask:
    """Tests for POST /tasks/{pipeline_id}/tasks/{task_id}/reject."""

    async def test_reject_requires_auth(self, client):
        """POST reject without auth should return 401."""
        resp = await client.post(
            "/api/tasks/some-pipe/tasks/some-task/reject",
            json={},
        )
        assert resp.status_code == 401

    async def test_reject_nonexistent_pipeline_returns_404(self, client):
        """POST reject for unknown pipeline returns 404."""
        token = await _register_and_get_token(client, email="reject-404@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent/tasks/some-task/reject",
            json={},
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_reject_wrong_state_returns_409(self, client_with_app):
        """POST reject for a task not in awaiting_approval returns 409."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="reject-409@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Reject test", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="in_progress",
                pipeline_id=pid,
            ))
            await session.commit()

        resp = await client.post(
            f"/api/tasks/{pid}/tasks/{tid}/reject",
            json={"reason": "Bad code"},
            headers=headers,
        )
        assert resp.status_code == 409

    async def test_reject_success_resets_task(self, client_with_app):
        """POST reject for awaiting_approval task resets to todo."""
        import json
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="reject-ok@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Reject OK", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="awaiting_approval",
                pipeline_id=pid,
                approval_context=json.dumps({
                    "worktree_path": "/tmp/wt",
                    "pipeline_branch": "forge/pipeline-abc",
                }),
            ))
            await session.commit()

        # Set up ws_manager mock
        app.state.ws_manager = AsyncMock()

        resp = await client.post(
            f"/api/tasks/{pid}/tasks/{tid}/reject",
            json={"reason": "Token expiry not handled"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "retrying"
        assert data["task_id"] == tid

        # Verify task state was reset
        task = await db.get_task(tid)
        assert task.state == "todo"
        assert task.retry_count == 1
        assert task.review_feedback == "Token expiry not handled"

    async def test_reject_without_reason_uses_default(self, client_with_app):
        """POST reject without a reason uses 'Rejected by user' default."""
        import json
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="reject-noreason@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Reject no reason", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="awaiting_approval",
                pipeline_id=pid,
                approval_context=json.dumps({
                    "worktree_path": "/tmp/wt",
                    "pipeline_branch": "forge/pipeline-abc",
                }),
            ))
            await session.commit()

        app.state.ws_manager = AsyncMock()

        resp = await client.post(
            f"/api/tasks/{pid}/tasks/{tid}/reject",
            json={},
            headers=headers,
        )
        assert resp.status_code == 200

        task = await db.get_task(tid)
        assert task.review_feedback == "Rejected by user"


# ── Pause endpoint tests ─────────────────────────────────────────────


class TestPausePipeline:
    """Tests for POST /tasks/{pipeline_id}/pause."""

    async def test_pause_requires_auth(self, client):
        """POST /tasks/{id}/pause without auth should return 401."""
        resp = await client.post("/api/tasks/some-id/pause")
        assert resp.status_code == 401

    async def test_pause_nonexistent_returns_404(self, client):
        """POST /tasks/{id}/pause for unknown pipeline should return 404."""
        token = await _register_and_get_token(client, email="pause-404@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent/pause",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_pause_non_running_returns_409(self, client_with_app):
        """POST /tasks/{id}/pause on a completed pipeline returns 409."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="pause-409@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Pause test", project_dir="/proj",
                status="complete", user_id=user_id,
            ))
            await session.commit()

        resp = await client.post(f"/api/tasks/{pid}/pause", headers=headers)
        assert resp.status_code == 409

    async def test_pause_executing_pipeline(self, client_with_app):
        """POST /tasks/{id}/pause on executing pipeline returns paused."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="pause-ok@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Pause exec", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            await session.commit()

        app.state.ws_manager = AsyncMock()

        resp = await client.post(f"/api/tasks/{pid}/pause", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "paused"

        # Verify DB state
        pipeline = await db.get_pipeline(pid)
        assert pipeline.status == "paused"
        assert pipeline.paused is True

    async def test_pause_planned_pipeline(self, client_with_app):
        """POST /tasks/{id}/pause on planned pipeline also works."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="pause-plan@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Pause planned", project_dir="/proj",
                status="planned", user_id=user_id,
            ))
            await session.commit()

        app.state.ws_manager = AsyncMock()

        resp = await client.post(f"/api/tasks/{pid}/pause", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"


# ── Resume from paused endpoint tests ────────────────────────────────


class TestResumePausedPipeline:
    """Tests for POST /tasks/{pipeline_id}/resume with paused state."""

    async def test_resume_paused_pipeline(self, client_with_app):
        """POST /tasks/{id}/resume on paused pipeline returns executing."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="resume-paused@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Resume paused", project_dir="/proj",
                status="paused", user_id=user_id,
                paused=True,
            ))
            await session.commit()

        app.state.ws_manager = AsyncMock()

        resp = await client.post(f"/api/tasks/{pid}/resume", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "executing"

        # Verify DB state
        pipeline = await db.get_pipeline(pid)
        assert pipeline.status == "executing"
        assert pipeline.paused is False

    async def test_pause_then_resume_roundtrip(self, client_with_app):
        """Pause then resume a pipeline end-to-end."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app
        token = await _register_and_get_token(client, email="roundtrip@example.com")
        headers = _auth_header(token)

        payload = decode_token(token, secret="test-secret-for-stats")
        user_id = payload["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="Roundtrip", project_dir="/proj",
                status="executing", user_id=user_id,
            ))
            await session.commit()

        app.state.ws_manager = AsyncMock()

        # Pause
        resp = await client.post(f"/api/tasks/{pid}/pause", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

        # Verify paused
        pipeline = await db.get_pipeline(pid)
        assert pipeline.paused is True

        # Resume
        resp = await client.post(f"/api/tasks/{pid}/resume", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "executing"

        # Verify resumed
        pipeline = await db.get_pipeline(pid)
        assert pipeline.paused is False
        assert pipeline.status == "executing"


# ── Require approval passthrough tests ───────────────────────────────


class TestRequireApprovalPassthrough:
    """Tests for require_approval in POST /tasks."""

    async def test_create_task_with_require_approval_true(self, client_with_app):
        """POST /tasks with require_approval=true should store it on the pipeline."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="approval-true@example.com")
        headers = _auth_header(token)

        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Feature with approval",
                "project_path": "/proj",
                "require_approval": True,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        pipeline_id = resp.json()["pipeline_id"]

        db = app.state.db
        pipeline = await db.get_pipeline(pipeline_id)
        assert pipeline.require_approval is True

    async def test_create_task_without_require_approval(self, client_with_app):
        """POST /tasks without require_approval defaults to False."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="approval-default@example.com")
        headers = _auth_header(token)

        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Feature without approval",
                "project_path": "/proj",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        pipeline_id = resp.json()["pipeline_id"]

        db = app.state.db
        pipeline = await db.get_pipeline(pipeline_id)
        assert pipeline.require_approval is False

    async def test_create_task_with_require_approval_false(self, client_with_app):
        """POST /tasks with require_approval=false explicitly."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="approval-false@example.com")
        headers = _auth_header(token)

        resp = await client.post(
            "/api/tasks",
            json={
                "description": "Feature no approval",
                "project_path": "/proj",
                "require_approval": False,
            },
            headers=headers,
        )
        assert resp.status_code == 201
        pipeline_id = resp.json()["pipeline_id"]

        db = app.state.db
        pipeline = await db.get_pipeline(pipeline_id)
        assert pipeline.require_approval is False


# ── Diff stats parsing tests ─────────────────────────────────────────


class TestParseDiffStats:
    """Tests for _parse_diff_stats helper."""

    def test_parse_empty_diff(self):
        from forge.api.routes.tasks import _parse_diff_stats
        stats = _parse_diff_stats("")
        assert stats == {"files_changed": 0, "lines_added": 0, "lines_removed": 0}

    def test_parse_single_file_diff(self):
        from forge.api.routes.tasks import _parse_diff_stats
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,5 @@\n"
            " unchanged\n"
            "+added line 1\n"
            "+added line 2\n"
            "-removed line\n"
        )
        stats = _parse_diff_stats(diff)
        assert stats["files_changed"] == 1
        assert stats["lines_added"] == 2
        assert stats["lines_removed"] == 1

    def test_parse_multi_file_diff(self):
        from forge.api.routes.tasks import _parse_diff_stats
        diff = (
            "diff --git a/a.py b/a.py\n"
            "+line\n"
            "diff --git a/b.py b/b.py\n"
            "-old\n"
            "+new\n"
        )
        stats = _parse_diff_stats(diff)
        assert stats["files_changed"] == 2
        assert stats["lines_added"] == 2
        assert stats["lines_removed"] == 1


# ── IDOR tests for new endpoints ─────────────────────────────────────


class TestNewEndpointIDOR:
    """IDOR protection for approval, pause, and diff endpoints."""

    async def test_diff_idor_protection(self, client_with_app):
        """GET diff for another user's pipeline returns 404."""
        import json
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app

        # User A creates pipeline
        token_a = await _register_and_get_token(client, email="idor-diff-a@example.com")
        payload_a = decode_token(token_a, secret="test-secret-for-stats")
        user_id_a = payload_a["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="IDOR diff", project_dir="/proj",
                status="executing", user_id=user_id_a,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="awaiting_approval",
                pipeline_id=pid,
                approval_context=json.dumps({"worktree_path": "/tmp/wt", "pipeline_branch": "main"}),
            ))
            await session.commit()

        # User B tries to access
        token_b = await _register_and_get_token(client, email="idor-diff-b@example.com")
        resp = await client.get(
            f"/api/tasks/{pid}/tasks/{tid}/diff",
            headers=_auth_header(token_b),
        )
        assert resp.status_code == 404

    async def test_approve_idor_protection(self, client_with_app):
        """POST approve for another user's pipeline returns 404."""
        import json
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow, TaskRow

        client, app = client_with_app

        token_a = await _register_and_get_token(client, email="idor-approve-a@example.com")
        payload_a = decode_token(token_a, secret="test-secret-for-stats")
        user_id_a = payload_a["sub"]

        pid = str(uuid.uuid4())
        tid = f"{pid[:8]}-task-1"
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="IDOR approve", project_dir="/proj",
                status="executing", user_id=user_id_a,
            ))
            session.add(TaskRow(
                id=tid, title="T1", description="D", files=["a.py"],
                depends_on=[], complexity="low", state="awaiting_approval",
                pipeline_id=pid,
            ))
            await session.commit()

        token_b = await _register_and_get_token(client, email="idor-approve-b@example.com")
        resp = await client.post(
            f"/api/tasks/{pid}/tasks/{tid}/approve",
            headers=_auth_header(token_b),
        )
        assert resp.status_code == 404

    async def test_pause_idor_protection(self, client_with_app):
        """POST pause for another user's pipeline returns 404."""
        import uuid
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        client, app = client_with_app

        token_a = await _register_and_get_token(client, email="idor-pause-a@example.com")
        payload_a = decode_token(token_a, secret="test-secret-for-stats")
        user_id_a = payload_a["sub"]

        pid = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(PipelineRow(
                id=pid, description="IDOR pause", project_dir="/proj",
                status="executing", user_id=user_id_a,
            ))
            await session.commit()

        token_b = await _register_and_get_token(client, email="idor-pause-b@example.com")
        resp = await client.post(
            f"/api/tasks/{pid}/pause",
            headers=_auth_header(token_b),
        )
        assert resp.status_code == 404
