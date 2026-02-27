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


async def _register_and_get_token(
    client: AsyncClient,
    email: str = "diff-user@example.com",
    display_name: str = "Diff User",
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


class TestDiffEndpoint:
    """Tests for GET /tasks/{pipeline_id}/diff."""

    async def test_diff_requires_auth(self, client):
        """GET /tasks/{id}/diff without auth should return 401."""
        resp = await client.get("/api/tasks/some-id/diff")
        assert resp.status_code == 401

    async def test_diff_returns_404_for_unknown_pipeline(self, client):
        """GET /tasks/{id}/diff for unknown pipeline should return 404."""
        token = await _register_and_get_token(client)
        resp = await client.get(
            "/api/tasks/nonexistent-id/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_diff_returns_empty_for_pipeline_without_diff(self, client):
        """GET /tasks/{id}/diff for a pipeline with no diff returns empty string."""
        token = await _register_and_get_token(client)

        # Create a task first
        create_resp = await client.post(
            "/api/tasks",
            json={
                "description": "Test diff",
                "project_path": "/tmp/project",
            },
            headers=_auth_header(token),
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        # Get diff
        resp = await client.get(
            f"/api/tasks/{pipeline_id}/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_id"] == pipeline_id
        assert data["diff"] == ""


class TestDiffIDOR:
    """IDOR protection: users cannot access other users' pipeline diffs."""

    async def test_diff_as_different_user_returns_404(self, client):
        """GET /tasks/{id}/diff for a pipeline owned by another user should return 404."""
        # Register user A and create a task
        token_a = await _register_and_get_token(client, email="diff-a@example.com")
        create_resp = await client.post(
            "/api/tasks",
            json={"description": "User A diff task", "project_path": "/proj"},
            headers=_auth_header(token_a),
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        # Register user B and try to access user A's diff
        token_b = await _register_and_get_token(client, email="diff-b@example.com")
        resp = await client.get(
            f"/api/tasks/{pipeline_id}/diff",
            headers=_auth_header(token_b),
        )
        assert resp.status_code == 404
