"""Tests for the diff endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an httpx AsyncClient backed by the app with in-memory DB."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-diff",
    )

    # Manually init since ASGITransport doesn't trigger lifespan
    await app.state.db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.db.close()


@pytest.fixture
async def client_with_app():
    """Like `client`, but also yields the app for direct DB access."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-for-diff",
    )

    await app.state.db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, app

    await app.state.db.close()


async def _register_and_get_token(
    client: AsyncClient,
    email: str = "diff-user@example.com",
    display_name: str = "Diff User",
) -> str:
    """Helper: register a user and return the access token."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "securepass",
            "display_name": display_name,
        },
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestDiffEndpoint:
    """Tests for GET /tasks/{pipeline_id}/diff."""

    async def test_diff_requires_auth(self, client):
        """GET /tasks/{id}/diff without auth should return 401."""
        resp = await client.get("/api/tasks/some-id/diff")
        assert resp.status_code == 401

    async def test_diff_returns_404_for_unknown_pipeline(self, client):
        """GET /tasks/{id}/diff for unknown pipeline should return 404."""
        token = await _register_and_get_token(client)
        resp = await client.get(
            "/api/tasks/nonexistent-id/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 404

    async def test_diff_returns_empty_for_pipeline_without_diff(self, client_with_app):
        """GET /tasks/{id}/diff for a pipeline with no diff returns empty string."""
        client, app = client_with_app
        db = app.state.db
        user_id, token = await _register_and_get_user_id_and_token(
            client, email="diff-empty@example.com"
        )

        # Create pipeline directly in DB (no background planning task)
        pipeline_id = "test-pipe-empty"
        await db.create_pipeline(
            id=pipeline_id,
            description="Test diff",
            project_dir="/tmp/project",
            user_id=user_id,
        )

        # Get diff
        resp = await client.get(
            f"/api/tasks/{pipeline_id}/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_id"] == pipeline_id
        assert data["diff"] == ""


class TestDiffIDOR:
    """IDOR protection: users cannot access other users' pipeline diffs."""

    async def test_diff_as_different_user_returns_404(self, client_with_app):
        """GET /tasks/{id}/diff for a pipeline owned by another user should return 404."""
        client, app = client_with_app
        db = app.state.db

        # Register user A and create a pipeline directly in DB
        user_id_a, token_a = await _register_and_get_user_id_and_token(
            client, email="diff-a@example.com"
        )
        pipeline_id = "test-pipe-idor"
        await db.create_pipeline(
            id=pipeline_id,
            description="User A diff task",
            project_dir="/proj",
            user_id=user_id_a,
        )

        # Register user B and try to access user A's diff
        token_b = await _register_and_get_token(client, email="diff-b@example.com")
        resp = await client.get(
            f"/api/tasks/{pipeline_id}/diff",
            headers=_auth_header(token_b),
        )
        assert resp.status_code == 404


async def _register_and_get_user_id_and_token(
    client: AsyncClient,
    email: str = "diff-db-user@example.com",
    display_name: str = "Diff DB User",
) -> tuple[str, str]:
    """Helper: register a user and return (user_id, access_token)."""
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "securepass",
            "display_name": display_name,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    return data["user"]["id"], data["access_token"]


class TestDiffFromDBEvents:
    """Tests for the DB-backed diff aggregation from merge_result events."""

    async def test_diff_aggregates_successful_merge_events(self, client_with_app):
        """Diff endpoint should combine diff text from successful merge_result events."""
        client, app = client_with_app
        db = app.state.db
        user_id, token = await _register_and_get_user_id_and_token(client)

        # Create pipeline directly in DB (no background planning task)
        pipeline_id = "test-pipe-agg"
        await db.create_pipeline(
            id=pipeline_id,
            description="merge diff test",
            project_dir="/tmp/proj",
            user_id=user_id,
        )

        # Insert merge_result events directly into DB
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id="t1",
            event_type="task:merge_result",
            payload={"success": True, "diff": "diff --git a/file1\n+added line"},
        )
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id="t2",
            event_type="task:merge_result",
            payload={"success": True, "diff": "diff --git a/file2\n+another line"},
        )

        resp = await client.get(
            f"/api/tasks/{pipeline_id}/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "diff --git a/file1" in data["diff"]
        assert "diff --git a/file2" in data["diff"]

    async def test_diff_ignores_failed_merge_events(self, client_with_app):
        """Diff endpoint should skip events where success is False."""
        client, app = client_with_app
        db = app.state.db
        user_id, token = await _register_and_get_user_id_and_token(
            client, email="diff-fail@example.com"
        )

        pipeline_id = "test-pipe-fail"
        await db.create_pipeline(
            id=pipeline_id,
            description="failed merge test",
            project_dir="/tmp/proj",
            user_id=user_id,
        )

        # One successful, one failed
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id="t1",
            event_type="task:merge_result",
            payload={"success": True, "diff": "good-diff"},
        )
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id="t2",
            event_type="task:merge_result",
            payload={"success": False, "diff": "bad-diff"},
        )

        resp = await client.get(
            f"/api/tasks/{pipeline_id}/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "good-diff" in data["diff"]
        assert "bad-diff" not in data["diff"]

    async def test_diff_ignores_non_merge_events(self, client_with_app):
        """Diff endpoint should only look at task:merge_result events."""
        client, app = client_with_app
        db = app.state.db
        user_id, token = await _register_and_get_user_id_and_token(
            client, email="diff-nonmerge@example.com"
        )

        pipeline_id = "test-pipe-nonmerge"
        await db.create_pipeline(
            id=pipeline_id,
            description="non-merge test",
            project_dir="/tmp/proj",
            user_id=user_id,
        )

        # Add a non-merge event with a diff field
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id="t1",
            event_type="task:state_changed",
            payload={"diff": "should-not-appear", "success": True},
        )

        resp = await client.get(
            f"/api/tasks/{pipeline_id}/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        assert resp.json()["diff"] == ""

    async def test_pipeline_diff_includes_repo_id_prefix(self, client_with_app):
        """Diff sections should be prefixed with '# repo: <repo_id>\\n'.

        When merge events have a repo_id in their payload, each diff section
        should start with '# repo: <repo_id>'. Events without a repo_id should
        default to '# repo: default'.
        """
        client, app = client_with_app
        db = app.state.db
        user_id, token = await _register_and_get_user_id_and_token(
            client, email="diff-repo-id@example.com"
        )

        pipeline_id = "test-pipe-repo-id"
        await db.create_pipeline(
            id=pipeline_id,
            description="repo_id prefix test",
            project_dir="/tmp/proj",
            user_id=user_id,
        )

        # Event with explicit repo_id
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id="t1",
            event_type="task:merge_result",
            payload={
                "success": True,
                "diff": "diff --git a/backend/main.py\n+new line",
                "repo_id": "backend",
            },
        )

        # Event without repo_id — should default to "default"
        await db.log_event(
            pipeline_id=pipeline_id,
            task_id="t2",
            event_type="task:merge_result",
            payload={
                "success": True,
                "diff": "diff --git a/frontend/app.ts\n+another line",
            },
        )

        resp = await client.get(
            f"/api/tasks/{pipeline_id}/diff",
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        diff_text = data["diff"]

        # Each section must be prefixed with the repo header
        assert "# repo: backend\n" in diff_text
        assert "# repo: default\n" in diff_text

        # The actual diff content must still be present
        assert "diff --git a/backend/main.py" in diff_text
        assert "diff --git a/frontend/app.ts" in diff_text

        # The backend section must come before the default section
        assert diff_text.index("# repo: backend") < diff_text.index("# repo: default")
