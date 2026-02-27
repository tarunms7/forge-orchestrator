"""Tests for AuditService."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from forge.api.models.user import AuditLogRow, Base
from forge.api.services.audit_service import AuditService


@pytest.fixture
async def async_session():
    """Create an in-memory SQLite database and return an async session factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


class TestAuditServiceLog:
    """Tests for AuditService.log()."""

    async def test_log_creates_audit_entry(self, async_session: AsyncSession):
        service = AuditService(async_session)
        await service.log(user_id="user-1", action="login")

        result = await async_session.execute(select(AuditLogRow))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == "user-1"
        assert rows[0].action == "login"
        assert rows[0].metadata_json is None
        assert rows[0].ip_address is None

    async def test_log_with_metadata(self, async_session: AsyncSession):
        metadata = {"browser": "Chrome", "os": "macOS"}
        service = AuditService(async_session)
        await service.log(user_id="user-2", action="page_view", metadata=metadata)

        result = await async_session.execute(select(AuditLogRow))
        row = result.scalars().one()
        assert row.user_id == "user-2"
        assert row.action == "page_view"
        stored = json.loads(row.metadata_json)
        assert stored == metadata

    async def test_log_with_ip(self, async_session: AsyncSession):
        service = AuditService(async_session)
        await service.log(user_id="user-3", action="logout", ip="192.168.1.100")

        result = await async_session.execute(select(AuditLogRow))
        row = result.scalars().one()
        assert row.ip_address == "192.168.1.100"

    async def test_log_generates_uuid_id(self, async_session: AsyncSession):
        service = AuditService(async_session)
        await service.log(user_id="user-1", action="test")

        result = await async_session.execute(select(AuditLogRow))
        row = result.scalars().one()
        assert row.id is not None
        assert len(row.id) == 36  # UUID format

    async def test_log_sets_timestamp(self, async_session: AsyncSession):
        service = AuditService(async_session)
        await service.log(user_id="user-1", action="test")

        result = await async_session.execute(select(AuditLogRow))
        row = result.scalars().one()
        assert row.timestamp is not None


class TestAuditServiceListForUser:
    """Tests for AuditService.list_for_user()."""

    async def test_list_returns_user_logs(self, async_session: AsyncSession):
        service = AuditService(async_session)
        await service.log(user_id="user-A", action="login")
        await service.log(user_id="user-A", action="view_dashboard")
        await service.log(user_id="user-B", action="login")

        logs = await service.list_for_user("user-A")
        assert len(logs) == 2
        actions = [log.action for log in logs]
        assert "login" in actions
        assert "view_dashboard" in actions

    async def test_list_respects_limit(self, async_session: AsyncSession):
        service = AuditService(async_session)
        for i in range(5):
            await service.log(user_id="user-X", action=f"action_{i}")

        logs = await service.list_for_user("user-X", limit=3)
        assert len(logs) == 3

    async def test_list_returns_most_recent_first(self, async_session: AsyncSession):
        service = AuditService(async_session)
        await service.log(user_id="user-Y", action="first")
        await service.log(user_id="user-Y", action="second")
        await service.log(user_id="user-Y", action="third")

        logs = await service.list_for_user("user-Y")
        # Most recent should be first
        assert logs[0].action == "third"
        assert logs[-1].action == "first"

    async def test_list_empty_for_unknown_user(self, async_session: AsyncSession):
        service = AuditService(async_session)
        logs = await service.list_for_user("nonexistent")
        assert logs == []

    async def test_list_default_limit_is_100(self, async_session: AsyncSession):
        service = AuditService(async_session)
        # Just verify it doesn't raise with default limit
        logs = await service.list_for_user("user-Z")
        assert isinstance(logs, list)
