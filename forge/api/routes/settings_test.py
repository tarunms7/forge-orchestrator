"""Tests for the settings endpoints (DB-persisted)."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-settings",
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
        assert data["max_agents"] == 2
        assert data["timeout"] == 600
        assert data["max_retries"] == 5
        assert data["model_strategy"] == "auto"
        assert data["planner_model"] == "opus"
        assert data["agent_model_low"] == "sonnet"
        assert data["agent_model_medium"] == "opus"
        assert data["agent_model_high"] == "opus"
        assert data["reviewer_model"] == "sonnet"


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
            json={"max_agents": 8, "model_strategy": "fast"},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_agents"] == 8
        assert data["model_strategy"] == "fast"
        # Unchanged fields remain at defaults
        assert data["timeout"] == 600
        assert data["planner_model"] == "opus"

    async def test_update_model_routing(self, client):
        """PUT /settings should update model routing fields."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        resp = await client.put(
            "/api/settings",
            json={
                "planner_model": "sonnet",
                "agent_model_low": "haiku",
                "reviewer_model": "opus",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planner_model"] == "sonnet"
        assert data["agent_model_low"] == "haiku"
        assert data["reviewer_model"] == "opus"
        # Unchanged model fields keep defaults
        assert data["agent_model_medium"] == "opus"
        assert data["agent_model_high"] == "opus"

    async def test_settings_persist_across_requests(self, client):
        """Settings should persist across multiple requests (DB-backed)."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        # Update
        await client.put(
            "/api/settings",
            json={"max_agents": 12, "planner_model": "haiku"},
            headers=headers,
        )

        # Verify
        resp = await client.get("/api/settings", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_agents"] == 12
        assert data["planner_model"] == "haiku"

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

    async def test_validation_rejects_invalid_timeout(self, client):
        """PUT /settings should reject timeout out of range."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        resp = await client.put(
            "/api/settings",
            json={"timeout": 10},
            headers=headers,
        )
        assert resp.status_code == 422

    async def test_multiple_updates_merge(self, client):
        """Multiple PUTs should merge, not overwrite previous settings."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        # First update
        await client.put(
            "/api/settings",
            json={"max_agents": 8},
            headers=headers,
        )

        # Second update (different field)
        await client.put(
            "/api/settings",
            json={"model_strategy": "quality"},
            headers=headers,
        )

        # Verify both persisted
        resp = await client.get("/api/settings", headers=headers)
        data = resp.json()
        assert data["max_agents"] == 8
        assert data["model_strategy"] == "quality"

    async def test_update_provider_model_format(self, client):
        """PUT /settings should accept provider:model format for model fields."""
        token = await _register_and_get_token(client)
        headers = _auth_header(token)

        resp = await client.put(
            "/api/settings",
            json={
                "planner_model": "claude:opus",
                "agent_model_low": "claude:sonnet",
                "contract_builder_model": "claude:opus",
                "ci_fix_model": "claude:sonnet",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["planner_model"] == "claude:opus"
        assert data["agent_model_low"] == "claude:sonnet"
        assert data["contract_builder_model"] == "claude:opus"
        assert data["ci_fix_model"] == "claude:sonnet"


class TestSettingsProviderFields:
    """Tests for provider-aware fields in GET /api/settings."""

    async def test_get_includes_openai_enabled(self, client):
        """GET /settings should include openai_enabled field."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/settings", headers=_auth_header(token))
        assert resp.status_code == 200
        data = resp.json()
        assert "openai_enabled" in data
        assert isinstance(data["openai_enabled"], bool)

    async def test_get_includes_available_providers(self, client):
        """GET /settings should include available_providers list."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/settings", headers=_auth_header(token))
        data = resp.json()
        assert "available_providers" in data
        assert isinstance(data["available_providers"], list)
        assert "claude" in data["available_providers"]

    async def test_get_includes_catalog(self, client):
        """GET /settings should include catalog list."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/settings", headers=_auth_header(token))
        data = resp.json()
        assert "catalog" in data
        assert isinstance(data["catalog"], list)
        assert len(data["catalog"]) >= 1

        # Check catalog entry shape
        entry = data["catalog"][0]
        assert "alias" in entry
        assert "canonical_id" in entry
        assert "backend" in entry
        assert "tier" in entry
        assert "capabilities" in entry
        assert "validated_stages" in entry

    async def test_get_includes_contract_builder_and_ci_fix(self, client):
        """GET /settings should include contract_builder_model and ci_fix_model."""
        token = await _register_and_get_token(client)
        resp = await client.get("/api/settings", headers=_auth_header(token))
        data = resp.json()
        assert "contract_builder_model" in data
        assert "ci_fix_model" in data
