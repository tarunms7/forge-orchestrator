"""Tests for GET /api/providers endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-providers",
    )

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
            "email": "providers-user@example.com",
            "password": "securepass",
            "display_name": "Providers User",
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestListProviders:
    """Tests for GET /api/providers."""

    async def test_requires_auth(self, client):
        """GET /api/providers without auth should return 401."""
        resp = await client.get("/api/providers")
        assert resp.status_code == 401

    async def test_returns_providers_list(self, client):
        """GET /api/providers should return list of providers with models."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/providers", headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.json()

        assert "providers" in data
        assert "observed_health" in data
        assert isinstance(data["providers"], list)
        assert len(data["providers"]) >= 1

        # Check response shape of first provider
        provider = data["providers"][0]
        assert "name" in provider
        assert "models" in provider
        assert isinstance(provider["models"], list)

    async def test_model_entry_shape(self, client):
        """Each model entry should have the expected fields."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/providers", headers=_auth_header(token))
        data = resp.json()

        # Find a model entry
        model = data["providers"][0]["models"][0]
        assert "alias" in model
        assert "canonical_id" in model
        assert "backend" in model
        assert "tier" in model
        assert "capabilities" in model
        assert "validated_stages" in model

        # Check capabilities shape
        caps = model["capabilities"]
        assert "can_use_tools" in caps
        assert "can_stream" in caps
        assert "can_run_shell" in caps
        assert "can_edit_files" in caps
        assert "max_context_tokens" in caps
        assert "supports_structured_output" in caps
        assert "supports_reasoning" in caps

    async def test_observed_health_is_list(self, client):
        """observed_health should be a list (possibly empty)."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/providers", headers=_auth_header(token))
        data = resp.json()
        assert isinstance(data["observed_health"], list)


class TestProvidersWithMockRegistry:
    """Tests for GET /api/providers with a mock registry on app.state."""

    async def test_uses_registry_when_available(self, client):
        """When app.state.registry is set, providers should come from it."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/providers", headers=_auth_header(token))
        data = resp.json()

        # The app factory now always wires a registry with ClaudeProvider
        provider_names = [p["name"] for p in data["providers"]]
        assert "claude" in provider_names

        # Claude provider should have sonnet, opus, haiku
        claude = next(p for p in data["providers"] if p["name"] == "claude")
        aliases = [m["alias"] for m in claude["models"]]
        assert "sonnet" in aliases
        assert "opus" in aliases
