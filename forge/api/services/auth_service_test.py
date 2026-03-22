"""Tests for auth methods on the unified Database class."""

import pytest

from forge.storage.db import Database


@pytest.fixture
async def db():
    """Provide a Database backed by in-memory SQLite."""
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    yield database
    await database.close()


async def test_create_user(db: Database):
    """create_user() should create a user and return the UserRow."""
    user = await db.create_user(
        email="new@example.com",
        password="strongpass123",
        display_name="New User",
    )

    assert user.email == "new@example.com"
    assert user.display_name == "New User"
    assert user.id is not None
    # Password hash should NOT be the plaintext
    assert user.password_hash != "strongpass123"


async def test_create_user_duplicate_email_raises(db: Database):
    """create_user() with duplicate email should raise ValueError."""
    await db.create_user(
        email="dup@example.com",
        password="pass1pass",
        display_name="First",
    )

    with pytest.raises(ValueError, match="already registered"):
        await db.create_user(
            email="dup@example.com",
            password="pass2pass",
            display_name="Second",
        )


async def test_get_user_by_email(db: Database):
    """get_user_by_email() should return the user."""
    await db.create_user(
        email="login@example.com",
        password="mypassword1",
        display_name="Login User",
    )

    user = await db.get_user_by_email("login@example.com")
    assert user is not None
    assert user.email == "login@example.com"


async def test_get_user_by_email_not_found(db: Database):
    """get_user_by_email() with unknown email should return None."""
    user = await db.get_user_by_email("ghost@example.com")
    assert user is None


async def test_verify_password_correct(db: Database):
    """verify_password() with correct password should return True."""
    user = await db.create_user(
        email="verify@example.com",
        password="correcthorse",
        display_name="Verify User",
    )
    assert Database.verify_password("correcthorse", user.password_hash) is True


async def test_verify_password_wrong(db: Database):
    """verify_password() with wrong password should return False."""
    user = await db.create_user(
        email="wrong@example.com",
        password="correct123",
        display_name="Wrong PW",
    )
    assert Database.verify_password("incorrect", user.password_hash) is False


async def test_password_is_hashed(db: Database):
    """The stored password_hash should not equal the plaintext password."""
    user = await db.create_user(
        email="hash@example.com",
        password="plaintext",
        display_name="Hash Check",
    )
    assert user.password_hash != "plaintext"
    assert len(user.password_hash) > 20  # bcrypt hashes are long
