"""Integration tests for follow-up REST endpoints."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from forge.core.followup import FollowUpExecution, FollowUpQuestion, FollowUpStatus


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-followup",
    )

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
        jwt_secret="test-secret-followup",
    )

    await app.state.db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, app

    await app.state.db.close()


async def _register_and_get_token(
    client: AsyncClient,
    email: str = "followup-user@example.com",
) -> str:
    """Helper: register a user and return the access token."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "securepass",
            "display_name": "Followup User",
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_complete_pipeline(app, user_id: str) -> str:
    """Create a complete pipeline directly in the DB with task graph."""
    from forge.storage.db import PipelineRow

    pipeline_id = str(uuid.uuid4())
    task_graph = {
        "tasks": [
            {
                "id": f"{pipeline_id[:8]}-task-1",
                "title": "Auth Module",
                "description": "Implement JWT authentication",
                "files": ["auth.py", "jwt.py"],
                "depends_on": [],
                "complexity": "medium",
            },
            {
                "id": f"{pipeline_id[:8]}-task-2",
                "title": "Test Suite",
                "description": "Write unit tests",
                "files": ["test_auth.py"],
                "depends_on": [f"{pipeline_id[:8]}-task-1"],
                "complexity": "low",
            },
        ],
    }

    db = app.state.db
    async with db._session_factory() as session:
        session.add(
            PipelineRow(
                id=pipeline_id,
                description="Build auth system",
                project_dir="/tmp/test-project",
                status="complete",
                user_id=user_id,
                task_graph_json=json.dumps(task_graph),
            )
        )
        await session.commit()

    return pipeline_id


# ── Authentication tests ──────────────────────────────────────────────


class TestFollowUpAuth:
    """Follow-up endpoints require valid JWT auth."""

    async def test_submit_followup_without_auth_returns_401(self, client):
        """POST /tasks/{id}/followup without auth should return 401."""
        resp = await client.post(
            "/api/tasks/some-id/followup",
            json={"questions": [{"text": "How?"}]},
        )
        assert resp.status_code == 401

    async def test_get_followup_status_without_auth_returns_401(self, client):
        """GET /tasks/{id}/followup/{id} without auth should return 401."""
        resp = await client.get("/api/tasks/some-id/followup/some-followup-id")
        assert resp.status_code == 401

    async def test_invalid_token_returns_401(self, client):
        """Invalid token should return 401."""
        resp = await client.post(
            "/api/tasks/some-id/followup",
            json={"questions": [{"text": "How?"}]},
            headers={"Authorization": "Bearer bad.token.here"},
        )
        assert resp.status_code == 401


# ── Validation tests ──────────────────────────────────────────────────


class TestFollowUpValidation:
    """Input validation for follow-up requests."""

    async def test_empty_questions_returns_422(self, client):
        """Empty questions list should return 422."""
        token = await _register_and_get_token(client, email="val1@example.com")
        resp = await client.post(
            "/api/tasks/some-id/followup",
            json={"questions": []},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422

    async def test_question_without_text_returns_422(self, client):
        """Question with empty text should return 422."""
        token = await _register_and_get_token(client, email="val2@example.com")
        resp = await client.post(
            "/api/tasks/some-id/followup",
            json={"questions": [{"text": ""}]},
            headers=_auth_header(token),
        )
        assert resp.status_code == 422


# ── Pipeline state checks ────────────────────────────────────────────


class TestFollowUpPipelineChecks:
    """Follow-up endpoint pipeline state validations."""

    async def test_nonexistent_pipeline_returns_404(self, client):
        """Follow-up on nonexistent pipeline returns 404."""
        token = await _register_and_get_token(client, email="notfound@example.com")
        resp = await client.post(
            "/api/tasks/nonexistent-id/followup",
            json={"questions": [{"text": "Fix the bug"}]},
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_incomplete_pipeline_returns_400(self, client_with_app):
        """Follow-up on a non-complete pipeline returns 400."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="incomplete@example.com")

        # Create a pipeline in "planning" state
        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        payload = decode_token(token, secret="test-secret-followup")
        user_id = payload["sub"]

        pipeline_id = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(
                PipelineRow(
                    id=pipeline_id,
                    description="In progress",
                    project_dir="/tmp/proj",
                    status="executing",
                    user_id=user_id,
                )
            )
            await session.commit()

        resp = await client.post(
            f"/api/tasks/{pipeline_id}/followup",
            json={"questions": [{"text": "What about X?"}]},
            headers=_auth_header(token),
        )
        assert resp.status_code == 400
        assert "executing" in resp.json()["detail"]

    async def test_pipeline_without_tasks_returns_400(self, client_with_app):
        """Follow-up on pipeline with no task graph returns 400."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="notasks@example.com")

        from forge.api.security.jwt import decode_token
        from forge.storage.db import PipelineRow

        payload = decode_token(token, secret="test-secret-followup")
        user_id = payload["sub"]

        pipeline_id = str(uuid.uuid4())
        db = app.state.db
        async with db._session_factory() as session:
            session.add(
                PipelineRow(
                    id=pipeline_id,
                    description="No tasks",
                    project_dir="/tmp/proj",
                    status="complete",
                    user_id=user_id,
                    task_graph_json=None,
                )
            )
            await session.commit()

        resp = await client.post(
            f"/api/tasks/{pipeline_id}/followup",
            json={"questions": [{"text": "Fix it"}]},
            headers=_auth_header(token),
        )
        assert resp.status_code == 400
        assert "no tasks" in resp.json()["detail"].lower()


# ── Submission tests ──────────────────────────────────────────────────


class TestSubmitFollowUp:
    """Tests for POST /tasks/{pipeline_id}/followup."""

    @patch("forge.api.routes.followup.classify_questions")
    @patch("forge.api.routes.followup.execute_followups")
    async def test_submit_followup_returns_202(self, mock_execute, mock_classify, client_with_app):
        """Valid follow-up submission returns 202 with followup_id."""
        mock_classify.return_value = {0: "task-1"}
        mock_execute.return_value = MagicMock()

        client, app = client_with_app
        token = await _register_and_get_token(client, email="submit@example.com")

        from forge.api.security.jwt import decode_token

        payload = decode_token(token, secret="test-secret-followup")
        user_id = payload["sub"]

        pipeline_id = await _create_complete_pipeline(app, user_id)

        resp = await client.post(
            f"/api/tasks/{pipeline_id}/followup",
            json={
                "questions": [
                    {"text": "Can you add input validation?"},
                    {"text": "The login page needs a loading state", "context": "UX feedback"},
                ],
            },
            headers=_auth_header(token),
        )

        assert resp.status_code == 202
        data = resp.json()
        assert "followup_id" in data
        assert data["pipeline_id"] == pipeline_id
        assert data["status"] == "pending"
        assert "2 follow-up" in data["message"]


# ── IDOR tests ────────────────────────────────────────────────────────


class TestFollowUpIDOR:
    """IDOR protection: users cannot submit follow-ups on others' pipelines."""

    async def test_submit_followup_on_other_users_pipeline_returns_404(self, client_with_app):
        """User B cannot submit follow-ups on User A's pipeline."""
        client, app = client_with_app

        # Register User A and create a completed pipeline
        token_a = await _register_and_get_token(client, email="usera-fu@example.com")
        from forge.api.security.jwt import decode_token

        payload_a = decode_token(token_a, secret="test-secret-followup")
        user_a_id = payload_a["sub"]
        pipeline_id = await _create_complete_pipeline(app, user_a_id)

        # Register User B and try to submit follow-up
        token_b = await _register_and_get_token(client, email="userb-fu@example.com")

        resp = await client.post(
            f"/api/tasks/{pipeline_id}/followup",
            json={"questions": [{"text": "What about X?"}]},
            headers=_auth_header(token_b),
        )
        assert resp.status_code == 404


# ── Get Follow-Up Status tests ────────────────────────────────────────


class TestGetFollowUpStatus:
    """Tests for GET /tasks/{pipeline_id}/followup/{followup_id}."""

    async def test_nonexistent_followup_returns_404(self, client_with_app):
        """Unknown followup_id returns 404."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="getfu@example.com")

        from forge.api.security.jwt import decode_token

        payload = decode_token(token, secret="test-secret-followup")
        user_id = payload["sub"]
        pipeline_id = await _create_complete_pipeline(app, user_id)

        resp = await client.get(
            f"/api/tasks/{pipeline_id}/followup/nonexistent-id",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_get_existing_followup_status(self, client_with_app):
        """Should return follow-up details from the in-memory store."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="status@example.com")

        from forge.api.security.jwt import decode_token

        payload = decode_token(token, secret="test-secret-followup")
        user_id = payload["sub"]
        pipeline_id = await _create_complete_pipeline(app, user_id)

        # Manually inject a follow-up execution into the store
        followup_id = str(uuid.uuid4())
        followup = FollowUpExecution(
            id=followup_id,
            pipeline_id=pipeline_id,
            status=FollowUpStatus.COMPLETE,
            questions=[
                FollowUpQuestion(text="Fix the bug"),
                FollowUpQuestion(text="Add tests", context="Coverage is low"),
            ],
            classification={0: "task-1", 1: "task-2"},
        )
        if not hasattr(app.state, "followup_store"):
            app.state.followup_store = {}
        app.state.followup_store[followup_id] = followup

        resp = await client.get(
            f"/api/tasks/{pipeline_id}/followup/{followup_id}",
            headers=_auth_header(token),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["followup_id"] == followup_id
        assert data["pipeline_id"] == pipeline_id
        assert data["status"] == "complete"
        assert len(data["questions"]) == 2
        assert data["questions"][0]["text"] == "Fix the bug"
        assert data["questions"][1]["context"] == "Coverage is low"
        assert data["classification"]["0"] == "task-1"
        assert data["classification"]["1"] == "task-2"

    async def test_get_followup_wrong_pipeline_returns_404(self, client_with_app):
        """Follow-up stored under pipeline A, queried under pipeline B -> 404."""
        client, app = client_with_app
        token = await _register_and_get_token(client, email="wrongpipe@example.com")

        from forge.api.security.jwt import decode_token

        payload = decode_token(token, secret="test-secret-followup")
        user_id = payload["sub"]
        pipeline_id = await _create_complete_pipeline(app, user_id)

        # Inject follow-up for a DIFFERENT pipeline
        followup_id = str(uuid.uuid4())
        followup = FollowUpExecution(
            id=followup_id,
            pipeline_id="other-pipeline",
            status=FollowUpStatus.PENDING,
            questions=[FollowUpQuestion(text="Q")],
        )
        if not hasattr(app.state, "followup_store"):
            app.state.followup_store = {}
        app.state.followup_store[followup_id] = followup

        resp = await client.get(
            f"/api/tasks/{pipeline_id}/followup/{followup_id}",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404
