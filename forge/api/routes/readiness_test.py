"""Tests for GET /api/readiness endpoint."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from forge.providers.readiness import (
    ProviderReadinessEntry,
    ReadinessReport,
    StageRoutingEntry,
)


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-readiness",
    )

    await app.state.db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.db.close()


async def _register_and_get_token(client: AsyncClient) -> str:
    """Helper: register a user and return the access token."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "readiness-user@example.com",
            "password": "securepass",
            "display_name": "Readiness User",
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mock_report() -> ReadinessReport:
    """Build a mock ReadinessReport for testing."""
    return ReadinessReport(
        providers=[
            ProviderReadinessEntry(
                ui_key="claude",
                provider_key="claude",
                display_name="Claude",
                installed=True,
                connected=True,
                auth_source="claude.ai",
                status="Connected",
                detail="user@example.com",
                blocking_issues=[],
            ),
        ],
        routing=[
            StageRoutingEntry(
                stage="planner",
                label="Planner",
                provider="claude",
                model="opus",
                spec="claude:opus",
                backend="claude-code-sdk",
                reasoning_effort="high",
                warnings=[],
            ),
            StageRoutingEntry(
                stage="agent_low",
                label="Agent Low",
                provider="claude",
                model="sonnet",
                spec="claude:sonnet",
                backend="claude-code-sdk",
                reasoning_effort=None,
                warnings=[],
            ),
        ],
        blocking_issues=[],
        warnings=[],
        ready=True,
    )


class TestGetReadiness:
    """Tests for GET /api/readiness."""

    async def test_requires_auth(self, client):
        """GET /api/readiness without auth should return 401."""
        resp = await client.get("/api/readiness")
        assert resp.status_code == 401

    async def test_returns_readiness_report(self, client):
        """GET /api/readiness should return a valid readiness report."""
        token = await _register_and_get_token(client)

        mock_report = _mock_report()
        with patch(
            "forge.api.routes.readiness.build_readiness_report",
            return_value=mock_report,
        ):
            resp = await client.get("/api/readiness", headers=_auth_header(token))

        assert resp.status_code == 200
        data = resp.json()

        # Verify top-level shape
        assert "providers" in data
        assert "routing" in data
        assert "blocking_issues" in data
        assert "warnings" in data
        assert "ready" in data

        assert data["ready"] is True
        assert data["blocking_issues"] == []
        assert data["warnings"] == []

    async def test_provider_entry_shape(self, client):
        """Each provider entry should have the expected fields."""
        token = await _register_and_get_token(client)

        mock_report = _mock_report()
        with patch(
            "forge.api.routes.readiness.build_readiness_report",
            return_value=mock_report,
        ):
            resp = await client.get("/api/readiness", headers=_auth_header(token))

        data = resp.json()
        provider = data["providers"][0]

        assert provider["ui_key"] == "claude"
        assert provider["provider_key"] == "claude"
        assert provider["display_name"] == "Claude"
        assert provider["installed"] is True
        assert provider["connected"] is True
        assert provider["auth_source"] == "claude.ai"
        assert provider["status"] == "Connected"
        assert provider["detail"] == "user@example.com"
        assert provider["blocking_issues"] == []

    async def test_routing_entry_shape(self, client):
        """Each routing entry should have the expected fields."""
        token = await _register_and_get_token(client)

        mock_report = _mock_report()
        with patch(
            "forge.api.routes.readiness.build_readiness_report",
            return_value=mock_report,
        ):
            resp = await client.get("/api/readiness", headers=_auth_header(token))

        data = resp.json()
        assert len(data["routing"]) == 2

        entry = data["routing"][0]
        assert entry["stage"] == "planner"
        assert entry["label"] == "Planner"
        assert entry["provider"] == "claude"
        assert entry["model"] == "opus"
        assert entry["spec"] == "claude:opus"
        assert entry["backend"] == "claude-code-sdk"
        assert entry["reasoning_effort"] == "high"
        assert entry["warnings"] == []

    async def test_blocking_issues_surfaced(self, client):
        """Blocking issues should appear in the response and ready=False."""
        token = await _register_and_get_token(client)

        report = ReadinessReport(
            providers=[
                ProviderReadinessEntry(
                    ui_key="codex",
                    provider_key="openai",
                    display_name="Codex",
                    installed=True,
                    connected=False,
                    auth_source=None,
                    status="Needs login",
                    detail="Run `codex login`",
                    blocking_issues=["Provider 'openai' is not connected"],
                ),
            ],
            routing=[],
            blocking_issues=["Provider 'openai' is not connected"],
            warnings=[],
            ready=False,
        )

        with patch(
            "forge.api.routes.readiness.build_readiness_report",
            return_value=report,
        ):
            resp = await client.get("/api/readiness", headers=_auth_header(token))

        data = resp.json()
        assert data["ready"] is False
        assert len(data["blocking_issues"]) == 1
        assert "openai" in data["blocking_issues"][0]

    async def test_warnings_surfaced(self, client):
        """Non-blocking warnings should appear in the response."""
        token = await _register_and_get_token(client)

        report = ReadinessReport(
            providers=[],
            routing=[
                StageRoutingEntry(
                    stage="reviewer",
                    label="Reviewer",
                    provider="claude",
                    model="opus",
                    spec="claude:opus",
                    backend="claude-code-sdk",
                    warnings=["opus is expensive for reviewer stage"],
                ),
            ],
            blocking_issues=[],
            warnings=["opus is expensive for reviewer stage"],
            ready=True,
        )

        with patch(
            "forge.api.routes.readiness.build_readiness_report",
            return_value=report,
        ):
            resp = await client.get("/api/readiness", headers=_auth_header(token))

        data = resp.json()
        assert data["ready"] is True
        assert len(data["warnings"]) == 1
        assert "expensive" in data["warnings"][0]
