"""Tests for User and AuditLog SQLAlchemy models."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


async def test_create_user_in_memory_db():
    """UserRow can be created and queried in an in-memory SQLite database."""
    from forge.storage.db import Base, UserRow

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = UserRow(
            email="test@example.com",
            password_hash="hashed-pw",
            display_name="Test User",
        )
        session.add(user)
        await session.commit()

        result = await session.execute(select(UserRow).where(UserRow.email == "test@example.com"))
        fetched = result.scalar_one()

    assert fetched.email == "test@example.com"
    assert fetched.display_name == "Test User"
    assert fetched.password_hash == "hashed-pw"
    assert isinstance(fetched.id, str)
    assert fetched.created_at is not None

    await engine.dispose()


async def test_user_email_uniqueness():
    """Duplicate emails should raise IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    from forge.storage.db import Base, UserRow

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        u1 = UserRow(email="dup@example.com", password_hash="h1", display_name="User1")
        u2 = UserRow(email="dup@example.com", password_hash="h2", display_name="User2")
        session.add(u1)
        await session.commit()

        session.add(u2)
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()


async def test_create_audit_log():
    """AuditLogRow can be created and linked to a user."""
    from forge.storage.db import AuditLogRow, Base, UserRow

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = UserRow(email="audit@example.com", password_hash="h", display_name="Auditor")
        session.add(user)
        await session.commit()

        log = AuditLogRow(
            user_id=user.id,
            action="login",
            ip_address="127.0.0.1",
            metadata_json='{"browser": "test"}',
        )
        session.add(log)
        await session.commit()

        result = await session.execute(select(AuditLogRow).where(AuditLogRow.user_id == user.id))
        fetched = result.scalar_one()

    assert fetched.action == "login"
    assert fetched.ip_address == "127.0.0.1"
    assert fetched.metadata_json == '{"browser": "test"}'
    assert fetched.timestamp is not None

    await engine.dispose()


async def test_user_id_is_uuid():
    """UserRow.id should default to a valid UUID string."""
    from forge.storage.db import Base, UserRow

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = UserRow(email="uuid@example.com", password_hash="h", display_name="UUID")
        session.add(user)
        await session.commit()

    # Should be a valid UUID
    uuid.UUID(user.id)

    await engine.dispose()
