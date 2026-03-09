"""Tests for single-user (auth-disabled) mode."""

from httpx import ASGITransport, AsyncClient


async def test_protected_endpoint_works_without_auth_when_auth_disabled(monkeypatch):
    """When no jwt_secret is provided, auth is auto-disabled and protected endpoints work."""
    monkeypatch.delenv("FORGE_JWT_SECRET", raising=False)
    monkeypatch.delenv("FORGE_AUTH_DISABLED", raising=False)

    from forge.api.app import create_app

    app = create_app()
    # No jwt_secret → auto-generated → auth_disabled=True
    assert app.state.auth_disabled is True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/templates")

    assert resp.status_code == 200


async def test_health_works_without_auth_when_auth_disabled(monkeypatch):
    """Health endpoint should work regardless of auth mode."""
    monkeypatch.delenv("FORGE_JWT_SECRET", raising=False)
    monkeypatch.delenv("FORGE_AUTH_DISABLED", raising=False)

    from forge.api.app import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_protected_endpoint_requires_auth_when_jwt_secret_set(monkeypatch):
    """When jwt_secret is explicitly provided, auth is required."""
    monkeypatch.delenv("FORGE_AUTH_DISABLED", raising=False)

    from forge.api.app import create_app

    app = create_app(jwt_secret="test-secret-key")
    assert app.state.auth_disabled is False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/templates")

    assert resp.status_code == 401


async def test_single_user_returns_local_user_id(monkeypatch):
    """In single-user mode, get_current_user should return 'local'."""
    monkeypatch.delenv("FORGE_JWT_SECRET", raising=False)
    monkeypatch.delenv("FORGE_AUTH_DISABLED", raising=False)

    from forge.api.app import create_app

    app = create_app()
    assert app.state.auth_disabled is True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/templates")

    # Templates endpoint returns builtin templates for the 'local' user
    assert resp.status_code == 200
    data = resp.json()
    assert "builtin" in data


async def test_explicit_auth_disabled_env(monkeypatch):
    """FORGE_AUTH_DISABLED=true should enable single-user mode even with jwt_secret."""
    monkeypatch.setenv("FORGE_AUTH_DISABLED", "true")

    from forge.api.app import create_app

    app = create_app(jwt_secret="real-secret")
    assert app.state.auth_disabled is True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/templates")

    assert resp.status_code == 200


async def test_explicit_auth_disabled_false_keeps_auth_enabled(monkeypatch):
    """FORGE_AUTH_DISABLED=false + no FORGE_JWT_SECRET should keep auth enabled."""
    monkeypatch.delenv("FORGE_JWT_SECRET", raising=False)
    monkeypatch.setenv("FORGE_AUTH_DISABLED", "false")

    from forge.api.app import create_app

    app = create_app()
    # Even though secret was auto-generated, explicit false keeps auth on
    assert app.state.auth_disabled is False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/templates")

    assert resp.status_code == 401
