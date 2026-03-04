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
