"""Tests for the diff endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app
    from forge.api.models.user import Base

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-diff",
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
        "/auth/register",
        json={
            "email": "diff-user@example.com",
            "password": "securepass",
            "display_name": "Diff User",
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestDiffEndpoint:
    """Tests for GET /tasks/{pipeline_id}/diff."""

    async def test_diff_requires_auth(self, client):
        """GET /tasks/{id}/diff without auth should return 401."""
        resp = await client.get("/tasks/some-id/diff")
        assert resp.status_code == 401

    async def test_diff_returns_404_for_unknown_pipeline(self, client):
        """GET /tasks/{id}/diff for unknown pipeline should return 404."""
        token = await _register_and_get_token(client)
        resp = await client.get(
            "/tasks/nonexistent-id/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_diff_returns_empty_for_pipeline_without_diff(self, client):
        """GET /tasks/{id}/diff for a pipeline with no diff returns empty string."""
        token = await _register_and_get_token(client)

        # Create a task first
        create_resp = await client.post(
            "/tasks",
            json={
                "description": "Test diff",
                "project_path": "/tmp/project",
            },
            headers=_auth_header(token),
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        # Get diff
        resp = await client.get(
            f"/tasks/{pipeline_id}/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_id"] == pipeline_id
        assert data["diff"] == ""
