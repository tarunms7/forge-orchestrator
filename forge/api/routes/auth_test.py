"""Integration tests for auth REST endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app
    from forge.api.models.user import Base

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-routes",
    )

    # Manually create tables since ASGITransport doesn't trigger lifespan
    async with app.state.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.async_engine.dispose()


async def test_register_success(client):
    """POST /auth/register with valid data should return 201 with access token and refresh cookie."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "new@example.com",
            "password": "securepass123",
            "display_name": "New User",
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" not in data  # refresh_token is now a cookie
    assert data["user"]["email"] == "new@example.com"
    assert data["user"]["display_name"] == "New User"

    # Refresh token should be in an httpOnly cookie
    cookies = resp.cookies
    assert "refresh_token" in cookies


async def test_register_duplicate_email(client):
    """POST /auth/register with duplicate email should return 409."""
    payload = {
        "email": "dup@example.com",
        "password": "password123",
        "display_name": "First",
    }
    resp1 = await client.post("/api/auth/register", json=payload)
    assert resp1.status_code == 201

    resp2 = await client.post("/api/auth/register", json=payload)
    assert resp2.status_code == 409
    assert "already registered" in resp2.json()["detail"].lower()


async def test_register_missing_fields(client):
    """POST /auth/register with missing fields should return 422."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": "missing@example.com"},
    )
    assert resp.status_code == 422


async def test_register_short_password_rejected(client):
    """POST /auth/register with password shorter than 8 chars should return 422."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "short@example.com",
            "password": "short",
            "display_name": "Short PW User",
        },
    )
    assert resp.status_code == 422


async def test_login_success(client):
    """POST /auth/login with valid credentials should return 200 with tokens."""
    # First register
    await client.post(
        "/api/auth/register",
        json={
            "email": "login@example.com",
            "password": "mypassword1",
            "display_name": "Login User",
        },
    )

    # Then login
    resp = await client.post(
        "/api/auth/login",
        json={"email": "login@example.com", "password": "mypassword1"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" not in data  # refresh_token is now a cookie
    assert data["user"]["email"] == "login@example.com"

    # Refresh token should be in an httpOnly cookie
    cookies = resp.cookies
    assert "refresh_token" in cookies


async def test_login_wrong_password(client):
    """POST /auth/login with wrong password should return 401."""
    await client.post(
        "/api/auth/register",
        json={
            "email": "wrong@example.com",
            "password": "correctpass1",
            "display_name": "Wrong PW",
        },
    )

    resp = await client.post(
        "/api/auth/login",
        json={"email": "wrong@example.com", "password": "incorrect1"},
    )

    assert resp.status_code == 401
    assert "invalid" in resp.json()["detail"].lower()


async def test_login_nonexistent_user(client):
    """POST /auth/login with unknown email should return 401."""
    resp = await client.post(
        "/api/auth/login",
        json={"email": "ghost@example.com", "password": "anything1"},
    )

    assert resp.status_code == 401


# ── Refresh endpoint tests ──────────────────────────────────────────


async def test_refresh_returns_new_access_token(client):
    """POST /auth/refresh with valid refresh cookie should return new access token."""
    # Register to get a refresh cookie
    reg_resp = await client.post(
        "/api/auth/register",
        json={
            "email": "refresh@example.com",
            "password": "securepass123",
            "display_name": "Refresh User",
        },
    )
    assert reg_resp.status_code == 201

    # Extract refresh token from the Set-Cookie header and set it manually
    # (httpx won't send Secure cookies over http:// in tests)
    refresh_token = reg_resp.cookies["refresh_token"]
    client.cookies.set("refresh_token", refresh_token)

    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data


async def test_refresh_without_cookie_returns_401(client):
    """POST /auth/refresh without refresh cookie should return 401."""
    # Use a fresh client with no cookies
    from forge.api.app import create_app
    from forge.api.models.user import Base

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-routes",
    )
    async with app.state.async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as fresh_client:
        resp = await fresh_client.post("/api/auth/refresh")

    assert resp.status_code == 401
    assert "no refresh token" in resp.json()["detail"].lower()

    await app.state.async_engine.dispose()


async def test_refresh_with_invalid_cookie_returns_401(client):
    """POST /auth/refresh with invalid token in cookie should return 401."""
    # Manually set an invalid refresh cookie
    client.cookies.set("refresh_token", "invalid.token.here", domain="test")
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401


async def test_refresh_cookie_path_is_root(client):
    """Refresh token cookie path must be '/' so it's sent to /api/auth/refresh."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "cookie-path@example.com",
            "password": "securepass123",
            "display_name": "Cookie Test",
        },
    )
    assert resp.status_code == 201
    # Check the Set-Cookie header for path
    set_cookie = resp.headers.get("set-cookie", "")
    assert "path=/" in set_cookie.lower().replace(" ", "")
    # Ensure it's not path=/auth
    assert "path=/auth" not in set_cookie.lower().replace(" ", "")
