"""Tests for the FastAPI app factory."""

from httpx import ASGITransport, AsyncClient


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
    assert data["version"] == "0.1.0"


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
    assert app.version == "0.1.0"
