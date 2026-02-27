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
    """POST /auth/register with valid data should return 201 with tokens."""
    resp = await client.post(
        "/auth/register",
        json={
            "email": "new@example.com",
            "password": "securepass123",
            "display_name": "New User",
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "new@example.com"
    assert data["user"]["display_name"] == "New User"


async def test_register_duplicate_email(client):
    """POST /auth/register with duplicate email should return 409."""
    payload = {
        "email": "dup@example.com",
        "password": "pass1",
        "display_name": "First",
    }
    resp1 = await client.post("/auth/register", json=payload)
    assert resp1.status_code == 201

    resp2 = await client.post("/auth/register", json=payload)
    assert resp2.status_code == 409
    assert "already registered" in resp2.json()["detail"].lower()


async def test_register_missing_fields(client):
    """POST /auth/register with missing fields should return 422."""
    resp = await client.post(
        "/auth/register",
        json={"email": "missing@example.com"},
    )
    assert resp.status_code == 422


async def test_login_success(client):
    """POST /auth/login with valid credentials should return 200 with tokens."""
    # First register
    await client.post(
        "/auth/register",
        json={
            "email": "login@example.com",
            "password": "mypassword",
            "display_name": "Login User",
        },
    )

    # Then login
    resp = await client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "mypassword"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["email"] == "login@example.com"


async def test_login_wrong_password(client):
    """POST /auth/login with wrong password should return 401."""
    await client.post(
        "/auth/register",
        json={
            "email": "wrong@example.com",
            "password": "correct",
            "display_name": "Wrong PW",
        },
    )

    resp = await client.post(
        "/auth/login",
        json={"email": "wrong@example.com", "password": "incorrect"},
    )

    assert resp.status_code == 401
    assert "invalid" in resp.json()["detail"].lower()


async def test_login_nonexistent_user(client):
    """POST /auth/login with unknown email should return 401."""
    resp = await client.post(
        "/auth/login",
        json={"email": "ghost@example.com", "password": "anything"},
    )

    assert resp.status_code == 401
