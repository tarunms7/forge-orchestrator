"""Tests for the history endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app
    from forge.api.models.user import Base

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-history",
    )

    async with app.state.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.async_engine.dispose()


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
