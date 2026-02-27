"""Tests for the AuthService (register and login)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_session():
    """Provide an async session backed by in-memory SQLite."""
    from forge.api.models.user import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def jwt_secret():
    return "test-jwt-secret"


async def test_register_creates_user(db_session, jwt_secret):
    """register() should create a user and return tokens."""
    from forge.api.services.auth_service import AuthService

    svc = AuthService(db_session, jwt_secret=jwt_secret)
    result = await svc.register(
        email="new@example.com",
        password="strongpass123",
        display_name="New User",
    )

    assert "access_token" in result
    assert "refresh_token" in result
    assert result["user"]["email"] == "new@example.com"
    assert result["user"]["display_name"] == "New User"
    assert "id" in result["user"]
    # Password hash should NOT be in the response
    assert "password_hash" not in result["user"]


async def test_register_duplicate_email_raises(db_session, jwt_secret):
    """register() with duplicate email should raise ValueError."""
    from forge.api.services.auth_service import AuthService

    svc = AuthService(db_session, jwt_secret=jwt_secret)
    await svc.register(
        email="dup@example.com",
        password="pass1",
        display_name="First",
    )

    with pytest.raises(ValueError, match="already registered"):
        await svc.register(
            email="dup@example.com",
            password="pass2",
            display_name="Second",
        )


async def test_login_correct_password(db_session, jwt_secret):
    """login() with correct credentials should return tokens."""
    from forge.api.services.auth_service import AuthService

    svc = AuthService(db_session, jwt_secret=jwt_secret)
    await svc.register(
        email="login@example.com",
        password="mypassword",
        display_name="Login User",
    )

    result = await svc.login(email="login@example.com", password="mypassword")

    assert "access_token" in result
    assert "refresh_token" in result
    assert result["user"]["email"] == "login@example.com"


async def test_login_wrong_password_raises(db_session, jwt_secret):
    """login() with wrong password should raise ValueError."""
    from forge.api.services.auth_service import AuthService

    svc = AuthService(db_session, jwt_secret=jwt_secret)
    await svc.register(
        email="wrong@example.com",
        password="correct",
        display_name="Wrong PW",
    )

    with pytest.raises(ValueError, match="Invalid"):
        await svc.login(email="wrong@example.com", password="incorrect")


async def test_login_nonexistent_user_raises(db_session, jwt_secret):
    """login() with unknown email should raise ValueError."""
    from forge.api.services.auth_service import AuthService

    svc = AuthService(db_session, jwt_secret=jwt_secret)

    with pytest.raises(ValueError, match="Invalid"):
        await svc.login(email="ghost@example.com", password="anything")


async def test_password_is_hashed(db_session, jwt_secret):
    """The stored password_hash should not equal the plaintext password."""
    from sqlalchemy import select

    from forge.api.models.user import UserRow
    from forge.api.services.auth_service import AuthService

    svc = AuthService(db_session, jwt_secret=jwt_secret)
    await svc.register(
        email="hash@example.com",
        password="plaintext",
        display_name="Hash Check",
    )

    result = await db_session.execute(
        select(UserRow).where(UserRow.email == "hash@example.com")
    )
    user = result.scalar_one()
    assert user.password_hash != "plaintext"
    assert len(user.password_hash) > 20  # bcrypt hashes are long
