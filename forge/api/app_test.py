"""Tests for the FastAPI app factory."""

from importlib.metadata import version as pkg_version

from httpx import ASGITransport, AsyncClient

try:
    _expected_version = pkg_version("forge-orchestrator")
except Exception:
    _expected_version = "0.1.0"  # fallback for editable installs


async def test_health_endpoint_returns_ok():
    """GET /health should return status ok and version."""
    from forge.api.app import create_app

    app = create_app(jwt_secret="test-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == _expected_version


async def test_cors_allows_localhost_3000():
    """CORS should allow requests from localhost:3000."""
    from forge.api.app import create_app

    app = create_app(jwt_secret="test-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert resp.status_code == 200
    assert "http://localhost:3000" in resp.headers.get("access-control-allow-origin", "")


async def test_app_factory_with_db_url():
    """create_app with db_url should set up unified Database on app.state."""
    from forge.api.app import create_app

    app = create_app(db_url="sqlite+aiosqlite:///:memory:", jwt_secret="test-secret")
    assert hasattr(app.state, "db")
    assert app.state.db is not None
    # Backward compat alias
    assert app.state.forge_db is app.state.db


async def test_app_metadata():
    """App should have correct title and version."""
    from forge.api.app import create_app

    app = create_app(jwt_secret="test-secret")
    assert app.title == "Forge"
    assert app.version == _expected_version


async def test_app_registry_includes_openai_when_enabled(monkeypatch):
    """App registry should expose OpenAI when FORGE_OPENAI_ENABLED is set."""
    from forge.api.app import create_app

    monkeypatch.setenv("FORGE_OPENAI_ENABLED", "true")
    app = create_app(jwt_secret="test-secret")

    provider_names = {provider.name for provider in app.state.registry.all_providers()}
    assert "claude" in provider_names
    assert "openai" in provider_names
