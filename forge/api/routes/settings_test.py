"""Tests for the settings endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app
    from forge.api.models.user import Base

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-settings",
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
            "email": "settings-user@example.com",
            "password": "securepass",
            "display_name": "Settings User",
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestGetSettings:
    """Tests for GET /settings."""

    async def test_settings_requires_auth(self, client):
        """GET /settings without auth should return 401."""
        resp = await client.get("/api/settings")
        assert resp.status_code == 401

    async def test_get_default_settings(self, client):
        """GET /settings should return default settings for new user."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/settings", headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_agents"] == 4
        assert data["timeout"] == 300
        assert data["browser_notifications"] is False
        assert data["webhook_url"] == ""
        assert data["default_execution_target"] == "local"


class TestUpdateSettings:
    """Tests for PUT /settings."""

    async def test_update_requires_auth(self, client):
        """PUT /settings without auth should return 401."""
        resp = await client.put("/api/settings", json={"max_agents": 8})
        assert resp.status_code == 401

    async def test_update_partial_settings(self, client):
        """PUT /settings should update only provided fields."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        resp = await client.put(
            "/api/settings",
            json={"max_agents": 8, "browser_notifications": True},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_agents"] == 8
        assert data["browser_notifications"] is True
        # Unchanged fields remain at defaults
        assert data["timeout"] == 300
        assert data["webhook_url"] == ""

    async def test_update_webhook_url(self, client):
        """PUT /settings should update the webhook URL."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        resp = await client.put(
            "/api/settings",
            json={"webhook_url": "https://hooks.slack.com/services/test"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["webhook_url"] == "https://hooks.slack.com/services/test"

    async def test_settings_persist_across_requests(self, client):
        """Settings should persist across multiple requests."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        # Update
        await client.put(
            "/api/settings",
            json={"max_agents": 12},
            headers=headers,
        )

        # Verify
        resp = await client.get("/api/settings", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["max_agents"] == 12

    async def test_validation_rejects_invalid_max_agents(self, client):
        """PUT /settings should reject max_agents out of range."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        resp = await client.put(
            "/api/settings",
            json={"max_agents": 0},
            headers=headers,
        )
        assert resp.status_code == 422
