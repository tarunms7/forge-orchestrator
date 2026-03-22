"""Tests for the history endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    try:
        from forge.api.app import create_app

        app = create_app(
            db_url="sqlite+aiosqlite:///:memory:",
            jwt_secret="test-secret-for-history",
        )
    except Exception:
        pytest.skip("FastAPI app unavailable (pydantic version mismatch)")

    # Manually init since ASGITransport doesn't trigger lifespan
    await app.state.db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.db.close()


async def _register_and_get_token(client: AsyncClient) -> str:
    """Helper: register a user and return the access token."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "history-user@example.com",
            "password": "securepass",
            "display_name": "History User",
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _get_user_id(client: AsyncClient, email: str) -> str:
    """Look up a user by email in the DB and return their id."""
    from forge.storage.db import UserRow

    app = client._transport.app
    db = app.state.db
    async with db._session_factory() as session:
        result = await session.execute(
            select(UserRow).where(UserRow.email == email)
        )
        return result.scalar_one().id


async def _create_pipeline_in_db(
    client: AsyncClient,
    *,
    user_id: str,
    description: str = "Test pipeline",
    created_at: str,
    completed_at: str | None = None,
    status: str = "complete",
) -> str:
    """Insert a PipelineRow directly into the DB with specific timestamps.

    Bypasses the POST /api/tasks endpoint (and its background planner) so
    tests can control created_at / completed_at precisely without race
    conditions from the background planning task setting completed_at upon
    failure.  Returns the new pipeline_id.
    """
    from forge.storage.db import PipelineRow

    pipeline_id = str(uuid.uuid4())
    app = client._transport.app
    db = app.state.db
    async with db._session_factory() as session:
        row = PipelineRow(
            id=pipeline_id,
            description=description,
            project_dir="/tmp/proj",
            status=status,
            user_id=user_id,
            created_at=created_at,
            completed_at=completed_at,
        )
        session.add(row)
        await session.commit()
    return pipeline_id


class TestListHistory:
    """Tests for GET /history."""

    async def test_history_requires_auth(self, client):
        """GET /history without auth should return 401."""
        resp = await client.get("/api/history")
        assert resp.status_code == 401

    async def test_history_returns_empty_list(self, client):
        """GET /history should return empty list when no pipelines exist."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/history", headers=_auth_header(token))
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_history_returns_created_pipelines(self, client):
        """GET /history should list pipelines the user has created."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        # Create two tasks
        await client.post(
            "/api/tasks",
            json={"description": "History Task A", "project_path": "/p1"},
            headers=headers,
        )
        await client.post(
            "/api/tasks",
            json={"description": "History Task B", "project_path": "/p2"},
            headers=headers,
        )

        resp = await client.get("/api/history", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        descriptions = {item["description"] for item in data}
        assert descriptions == {"History Task A", "History Task B"}
        # Each entry should have expected keys
        for item in data:
            assert "pipeline_id" in item
            assert "phase" in item
            assert "task_count" in item
            assert "project_path" in item

    async def test_history_list_duration_in_items(self, client):
        """GET /history should include a correctly computed duration in each list item.

        Steps:
        1. Register a user and insert a pipeline into the DB with
           created_at='2026-01-01T00:00:00+00:00' and
           completed_at='2026-01-01T00:01:30+00:00'.
           (Direct DB insertion is used to avoid a race with the background
           planner, which sets completed_at on failure.)
        2. Call GET /api/history.
        3. Assert duration == 90 for the returned item.
        """
        token = await _register_and_get_token(client)
        headers = _auth_header(token)
        user_id = await _get_user_id(client, "history-user@example.com")

        # Insert a pipeline directly in the DB with a known 90-second run
        await _create_pipeline_in_db(
            client,
            user_id=user_id,
            description="Duration list task",
            created_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:01:30+00:00",
        )

        resp = await client.get("/api/history", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        item = data[0]
        assert "duration" in item
        assert item["duration"] == 90


class TestHistoryDetail:
    """Tests for GET /history/{pipeline_id}."""

    async def test_history_detail_requires_auth(self, client):
        """GET /history/{id} without auth should return 401."""
        resp = await client.get("/api/history/some-id")
        assert resp.status_code == 401

    async def test_history_detail_returns_404_for_unknown(self, client):
        """GET /history/{id} for unknown pipeline should return 404."""
        token = await _register_and_get_token(client)
        resp = await client.get(
            "/api/history/nonexistent-id",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_history_detail_returns_pipeline(self, client):
        """GET /history/{id} should return full pipeline detail."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        create_resp = await client.post(
            "/api/tasks",
            json={"description": "Detail task", "project_path": "/tmp/proj"},
            headers=headers,
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        resp = await client.get(
            f"/api/history/{pipeline_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_id"] == pipeline_id
        assert data["description"] == "Detail task"
        assert data["project_path"] == "/tmp/proj"
        assert "phase" in data
        assert "tasks" in data

    async def test_history_detail_duration_computed(self, client):
        """duration is correctly computed when created_at and completed_at are both set.

        Steps:
        1. Register a user and insert a pipeline into the DB with
           created_at='2026-01-01T00:00:00+00:00' and
           completed_at='2026-01-01T00:01:30+00:00'.
           (Direct DB insertion is used to avoid a race with the background
           planner, which sets completed_at on failure via update_pipeline_status.)
        2. Call GET /api/history/{pipeline_id}.
        3. Assert duration == 90 seconds.
        """
        token = await _register_and_get_token(client)
        headers = _auth_header(token)
        user_id = await _get_user_id(client, "history-user@example.com")

        # Insert a completed pipeline with a precise 90-second run
        pipeline_id = await _create_pipeline_in_db(
            client,
            user_id=user_id,
            description="Duration task",
            created_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:01:30+00:00",
        )

        resp = await client.get(f"/api/history/{pipeline_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["duration"] == 90

    async def test_history_detail_duration_none_when_not_completed(self, client):
        """duration is None when completed_at is not set (newly created pipeline).

        Steps:
        1. Register a user and insert a pipeline into the DB with no completed_at.
           (Direct DB insertion is used to avoid the background planner setting
           completed_at when planning fails in the test environment.)
        2. Call GET /api/history/{pipeline_id}.
        3. Assert duration is None.
        """
        token = await _register_and_get_token(client)
        headers = _auth_header(token)
        user_id = await _get_user_id(client, "history-user@example.com")

        # Insert a pipeline that has never been completed (no completed_at)
        pipeline_id = await _create_pipeline_in_db(
            client,
            user_id=user_id,
            description="Incomplete task",
            created_at="2026-01-01T00:00:00+00:00",
            completed_at=None,
            status="planning",
        )

        resp = await client.get(f"/api/history/{pipeline_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["duration"] is None


class TestListHistoryProjectPath:
    """Tests for project_path field in history list response.

    Uses httpx.AsyncClient against the real FastAPI app to verify that
    project_path is populated correctly in /history responses.
    """

    async def test_project_path_populated_in_list(self, client):
        """GET /history items include project_path from the pipeline's project_dir."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)
        user_id = await _get_user_id(client, "history-user@example.com")

        await _create_pipeline_in_db(
            client,
            user_id=user_id,
            description="Project path test",
            created_at="2026-01-01T00:00:00+00:00",
        )

        resp = await client.get("/api/history", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "project_path" in data[0]
        assert data[0]["project_path"] == "/tmp/proj"

    async def test_project_path_present_in_empty_history(self, client):
        """GET /history returns empty list when no pipelines exist."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/history", headers=_auth_header(token))
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_multiple_pipelines_each_have_project_path(self, client):
        """Each item in GET /history includes the project_path key."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)
        user_id = await _get_user_id(client, "history-user@example.com")

        for i in range(2):
            await _create_pipeline_in_db(
                client,
                user_id=user_id,
                description=f"Pipeline {i}",
                created_at=f"2026-01-0{i+1}T00:00:00+00:00",
            )

        resp = await client.get("/api/history", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        for item in data:
            assert "project_path" in item
            assert item["project_path"] == "/tmp/proj"
