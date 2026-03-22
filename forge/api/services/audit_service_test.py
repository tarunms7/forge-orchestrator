"""Tests for audit log methods on the unified Database class."""

import json

import pytest

from forge.storage.db import Database


@pytest.fixture
async def db():
    """Create an in-memory Database."""
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    yield database
    await database.close()


class TestLogAudit:
    """Tests for Database.log_audit()."""

    async def test_log_creates_audit_entry(self, db: Database):
        await db.log_audit(user_id="user-1", action="login")

        logs = await db.list_audit_logs("user-1")
        assert len(logs) == 1
        assert logs[0].user_id == "user-1"
        assert logs[0].action == "login"
        assert logs[0].metadata_json is None
        assert logs[0].ip_address is None

    async def test_log_with_metadata(self, db: Database):
        metadata = {"browser": "Chrome", "os": "macOS"}
        await db.log_audit(user_id="user-2", action="page_view", metadata=metadata)

        logs = await db.list_audit_logs("user-2")
        assert len(logs) == 1
        row = logs[0]
        assert row.user_id == "user-2"
        assert row.action == "page_view"
        stored = json.loads(row.metadata_json)
        assert stored == metadata

    async def test_log_with_ip(self, db: Database):
        await db.log_audit(user_id="user-3", action="logout", ip="192.168.1.100")

        logs = await db.list_audit_logs("user-3")
        assert len(logs) == 1
        assert logs[0].ip_address == "192.168.1.100"

    async def test_log_generates_uuid_id(self, db: Database):
        await db.log_audit(user_id="user-1", action="test")

        logs = await db.list_audit_logs("user-1")
        assert len(logs) == 1
        assert logs[0].id is not None
        assert len(logs[0].id) == 36  # UUID format

    async def test_log_sets_timestamp(self, db: Database):
        await db.log_audit(user_id="user-1", action="test")

        logs = await db.list_audit_logs("user-1")
        assert len(logs) == 1
        assert logs[0].timestamp is not None


class TestListAuditLogs:
    """Tests for Database.list_audit_logs()."""

    async def test_list_returns_user_logs(self, db: Database):
        await db.log_audit(user_id="user-A", action="login")
        await db.log_audit(user_id="user-A", action="view_dashboard")
        await db.log_audit(user_id="user-B", action="login")

        logs = await db.list_audit_logs("user-A")
        assert len(logs) == 2
        actions = [log.action for log in logs]
        assert "login" in actions
        assert "view_dashboard" in actions

    async def test_list_respects_limit(self, db: Database):
        for i in range(5):
            await db.log_audit(user_id="user-X", action=f"action_{i}")

        logs = await db.list_audit_logs("user-X", limit=3)
        assert len(logs) == 3

    async def test_list_returns_most_recent_first(self, db: Database):
        await db.log_audit(user_id="user-Y", action="first")
        await db.log_audit(user_id="user-Y", action="second")
        await db.log_audit(user_id="user-Y", action="third")

        logs = await db.list_audit_logs("user-Y")
        # Most recent should be first
        assert logs[0].action == "third"
        assert logs[-1].action == "first"

    async def test_list_empty_for_unknown_user(self, db: Database):
        logs = await db.list_audit_logs("nonexistent")
        assert logs == []

    async def test_list_default_limit_is_100(self, db: Database):
        # Just verify it doesn't raise with default limit
        logs = await db.list_audit_logs("user-Z")
        assert isinstance(logs, list)
