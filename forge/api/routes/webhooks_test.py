"""Tests for the GitHub webhook endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from forge.api.routes.webhooks import (
    WEBHOOK_RATE_LIMIT_SECONDS,
    _build_task_description,
    _check_rate_limit,
    _extract_forge_command,
    _is_collaborator,
    _verify_signature,
    _webhook_rate_limit,
)

WEBHOOK_SECRET = "test-webhook-secret"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """Compute sha256=<hex> signature for *body* using *secret*."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _make_payload(
    *,
    action: str = "created",
    comment_body: str = "/forge add tests",
    author_association: str = "MEMBER",
    issue_number: int = 42,
    issue_title: str = "Add dark mode",
    issue_body: str = "We need dark mode.",
    repo_full_name: str = "owner/repo",
) -> dict:
    """Build a realistic ``issue_comment`` webhook payload."""
    return {
        "action": action,
        "issue": {
            "number": issue_number,
            "title": issue_title,
            "body": issue_body,
            "html_url": f"https://github.com/{repo_full_name}/issues/{issue_number}",
        },
        "comment": {
            "body": comment_body,
            "user": {"login": "contributor"},
            "author_association": author_association,
        },
        "repository": {
            "full_name": repo_full_name,
            "clone_url": f"https://github.com/{repo_full_name}.git",
        },
    }


@pytest.fixture
async def client():
    """AsyncClient with an in-memory DB and webhook secret configured."""
    from forge.api.app import create_app

    app = create_app(
        db_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret",
    )

    # Override webhook settings on app.state
    app.state.github_webhook_secret = WEBHOOK_SECRET
    app.state.github_allowed_repos = []  # allow all by default
    app.state.webhook_project_dir = "/tmp/test-project"

    await app.state.db.initialize()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await app.state.db.close()


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    """Reset the module-level rate-limit dict before every test."""
    _webhook_rate_limit.clear()
    yield
    _webhook_rate_limit.clear()


# ===================================================================
# Unit tests — pure helper functions
# ===================================================================


class TestVerifySignature:
    """Tests for _verify_signature()."""

    def test_valid_signature(self):
        body = b'{"hello": "world"}'
        sig = _sign(body)
        assert _verify_signature(body, sig, WEBHOOK_SECRET) is True

    def test_invalid_signature(self):
        body = b'{"hello": "world"}'
        assert _verify_signature(body, "sha256=badhex", WEBHOOK_SECRET) is False

    def test_missing_header(self):
        assert _verify_signature(b"body", "", WEBHOOK_SECRET) is False

    def test_no_sha256_prefix(self):
        assert _verify_signature(b"body", "md5=abc", WEBHOOK_SECRET) is False

    def test_wrong_secret(self):
        body = b'{"hello": "world"}'
        sig = _sign(body, "correct-secret")
        assert _verify_signature(body, sig, "wrong-secret") is False


class TestIsCollaborator:
    """Tests for _is_collaborator()."""

    @pytest.mark.parametrize("assoc", ["OWNER", "MEMBER", "COLLABORATOR"])
    def test_trusted_associations(self, assoc: str):
        payload = _make_payload(author_association=assoc)
        assert _is_collaborator(payload) is True

    @pytest.mark.parametrize("assoc", ["CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE", ""])
    def test_untrusted_associations(self, assoc: str):
        payload = _make_payload(author_association=assoc)
        assert _is_collaborator(payload) is False

    def test_case_insensitive(self):
        payload = _make_payload(author_association="owner")
        assert _is_collaborator(payload) is True


class TestExtractForgeCommand:
    """Tests for _extract_forge_command()."""

    def test_forge_with_instruction(self):
        assert _extract_forge_command("/forge add tests") == "add tests"

    def test_forge_alone(self):
        assert _extract_forge_command("/forge") == ""

    def test_forge_with_whitespace(self):
        assert _extract_forge_command("  /forge  extra text  ") == "extra text"

    def test_not_forge(self):
        assert _extract_forge_command("just a comment") is None

    def test_forge_in_middle(self):
        assert _extract_forge_command("please /forge this") is None


class TestBuildTaskDescription:
    """Tests for _build_task_description()."""

    def test_full_description(self):
        payload = _make_payload(
            issue_title="Add auth", issue_body="Need JWT auth.", comment_body="/forge also add tests",
        )
        result = _build_task_description(payload, "also add tests")
        assert "# Add auth" in result
        assert "Need JWT auth." in result
        assert "## Additional Instructions" in result
        assert "also add tests" in result

    def test_no_extra_instruction(self):
        payload = _make_payload(issue_title="Fix bug", issue_body="Crash on login")
        result = _build_task_description(payload, "")
        assert "# Fix bug" in result
        assert "Crash on login" in result
        assert "Additional Instructions" not in result

    def test_empty_issue_body(self):
        payload = _make_payload(issue_title="Quick fix", issue_body="")
        result = _build_task_description(payload, "do it fast")
        assert "# Quick fix" in result
        assert "do it fast" in result


class TestCheckRateLimit:
    """Tests for _check_rate_limit()."""

    def test_first_request_allowed(self):
        assert _check_rate_limit("owner/repo", 1) is True

    def test_second_request_within_window_blocked(self):
        assert _check_rate_limit("owner/repo", 2) is True
        assert _check_rate_limit("owner/repo", 2) is False

    def test_different_issues_independent(self):
        assert _check_rate_limit("owner/repo", 10) is True
        assert _check_rate_limit("owner/repo", 11) is True

    def test_different_repos_independent(self):
        assert _check_rate_limit("owner/repo-a", 1) is True
        assert _check_rate_limit("owner/repo-b", 1) is True

    def test_allowed_after_window_expires(self):
        key = "owner/repo#99"
        _webhook_rate_limit[key] = time.time() - WEBHOOK_RATE_LIMIT_SECONDS - 1
        assert _check_rate_limit("owner/repo", 99) is True


# ===================================================================
# Integration tests — endpoint
# ===================================================================


class TestWebhookEndpoint:
    """Tests for POST /api/webhooks/github."""

    async def _post_webhook(
        self,
        client: AsyncClient,
        payload: dict | None = None,
        *,
        event_type: str = "issue_comment",
        secret: str = WEBHOOK_SECRET,
    ):
        """Send a webhook request with proper signature."""
        if payload is None:
            payload = _make_payload()
        body = json.dumps(payload).encode()
        sig = _sign(body, secret)
        return await client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": event_type,
            },
        )

    # ── Signature ──────────────────────────────────────────────────

    async def test_invalid_signature_returns_401(self, client):
        body = json.dumps(_make_payload()).encode()
        resp = await client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=bad",
                "X-GitHub-Event": "issue_comment",
            },
        )
        assert resp.status_code == 401

    async def test_missing_signature_returns_401(self, client):
        body = json.dumps(_make_payload()).encode()
        resp = await client.post(
            "/api/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "issue_comment",
            },
        )
        assert resp.status_code == 401

    # ── Event filtering ────────────────────────────────────────────

    async def test_ping_returns_200(self, client):
        resp = await self._post_webhook(client, _make_payload(), event_type="ping")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pong"

    async def test_non_issue_comment_returns_200(self, client):
        resp = await self._post_webhook(client, _make_payload(), event_type="push")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"

    async def test_action_not_created_returns_200(self, client):
        payload = _make_payload(action="edited")
        resp = await self._post_webhook(client, payload)
        assert resp.status_code == 200
        assert "Only 'created'" in resp.json()["reason"]

    # ── Command parsing ────────────────────────────────────────────

    async def test_non_forge_comment_returns_200(self, client):
        payload = _make_payload(comment_body="just a regular comment")
        resp = await self._post_webhook(client, payload)
        assert resp.status_code == 200
        assert "does not start with /forge" in resp.json()["reason"]

    # ── Collaborator check ─────────────────────────────────────────

    async def test_non_collaborator_returns_403(self, client):
        payload = _make_payload(author_association="NONE")
        resp = await self._post_webhook(client, payload)
        assert resp.status_code == 403

    # ── Repo allow-list ────────────────────────────────────────────

    async def test_repo_not_in_allowlist_returns_403(self, client):
        # Set an explicit allow list
        client._transport.app.state.github_allowed_repos = ["allowed/repo"]
        payload = _make_payload(repo_full_name="other/repo")
        resp = await self._post_webhook(client, payload)
        assert resp.status_code == 403
        assert "not in the allowed list" in resp.json()["detail"]

    async def test_empty_allowlist_allows_all(self, client):
        client._transport.app.state.github_allowed_repos = []
        payload = _make_payload(repo_full_name="any/repo")
        resp = await self._post_webhook(client, payload)
        # Should proceed past allow-list check (202 or other downstream status)
        assert resp.status_code != 403

    # ── Rate limit ─────────────────────────────────────────────────

    async def test_rate_limit_returns_429(self, client):
        payload = _make_payload(issue_number=100, repo_full_name="owner/rl-repo")
        resp1 = await self._post_webhook(client, payload)
        assert resp1.status_code == 202

        resp2 = await self._post_webhook(client, payload)
        assert resp2.status_code == 429

    # ── Success path ───────────────────────────────────────────────

    async def test_success_creates_pipeline_and_returns_202(self, client):
        payload = _make_payload(issue_number=50, repo_full_name="owner/success-repo")
        resp = await self._post_webhook(client, payload)
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert "pipeline_id" in data
        assert data["issue_number"] == 50

    async def test_success_stores_pipeline_in_db(self, client):
        payload = _make_payload(
            issue_number=60,
            issue_title="Store test",
            repo_full_name="owner/db-repo",
        )
        resp = await self._post_webhook(client, payload)
        assert resp.status_code == 202
        pipeline_id = resp.json()["pipeline_id"]

        db = client._transport.app.state.db
        pipeline = await db.get_pipeline(pipeline_id)
        assert pipeline is not None
        assert pipeline.github_issue_number == 60
        assert "github.com" in pipeline.github_issue_url
        assert "Store test" in pipeline.description


# ===================================================================
# Tests for _post_issue_comment
# ===================================================================


class TestPostIssueComment:
    """Tests for _post_issue_comment() subprocess call."""

    async def test_calls_gh_cli(self):
        from forge.api.routes.webhooks import _post_issue_comment

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        with patch("forge.api.routes.webhooks.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await _post_issue_comment("owner/repo", 42, "Hello!", "/tmp/proj")

            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert args[0] == "gh"
            assert args[1] == "issue"
            assert args[2] == "comment"
            assert args[3] == "42"
            assert "--repo" in args
            assert "owner/repo" in args
            assert "--body" in args
            assert "Hello!" in args

    async def test_logs_warning_on_failure(self):
        from forge.api.routes.webhooks import _post_issue_comment

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"auth required")
        mock_proc.returncode = 1

        with (
            patch("forge.api.routes.webhooks.asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("forge.api.routes.webhooks.logger") as mock_logger,
        ):
            await _post_issue_comment("owner/repo", 42, "Hello!", "/tmp/proj")
            mock_logger.warning.assert_called_once()


# ===================================================================
# Tests for _run_webhook_pipeline
# ===================================================================


class TestRunWebhookPipeline:
    """Tests for _run_webhook_pipeline() background task."""

    async def test_success_flow_posts_comments(self):
        from forge.api.routes.webhooks import _run_webhook_pipeline

        # Mock daemon
        mock_daemon = AsyncMock()
        mock_graph = MagicMock()
        mock_task = MagicMock()
        mock_task.title = "Test task"
        mock_task.complexity = MagicMock()
        mock_task.complexity.value = "medium"
        mock_graph.tasks = [mock_task]
        mock_daemon.plan.return_value = mock_graph
        mock_daemon.execute.return_value = None

        mock_emitter = MagicMock()
        daemon_factory = MagicMock(return_value=(mock_daemon, mock_emitter))

        # Mock DB
        mock_db = AsyncMock()
        mock_db.update_pipeline_status = AsyncMock()

        with (
            patch("forge.api.routes.webhooks._post_issue_comment", new_callable=AsyncMock) as mock_comment,
            patch("forge.api.routes.tasks._auto_create_pr", new_callable=AsyncMock, return_value="https://github.com/owner/repo/pull/99"),
        ):
            await _run_webhook_pipeline(
                forge_db=mock_db,
                daemon_factory=daemon_factory,
                pipeline_id="test-pipeline-id",
                project_dir="/tmp/proj",
                task_description="Test task",
                issue_url="https://github.com/owner/repo/issues/42",
                issue_number=42,
                repo_full_name="owner/repo",
            )

            # Should have posted 3 comments: start, plan ready, complete
            assert mock_comment.call_count == 3
            calls = [c[0] for c in mock_comment.call_args_list]

            # Comment 1: pipeline started
            assert "Forge pipeline started" in calls[0][2]
            # Comment 2: plan ready
            assert "Plan ready: 1 tasks" in calls[1][2]
            # Comment 3: complete with PR
            assert "Pipeline complete!" in calls[2][2]

    async def test_failure_posts_error_comment(self):
        from forge.api.routes.webhooks import _run_webhook_pipeline

        # Daemon that fails during planning
        mock_daemon = AsyncMock()
        mock_daemon.plan.side_effect = RuntimeError("SDK error")
        mock_emitter = MagicMock()
        daemon_factory = MagicMock(return_value=(mock_daemon, mock_emitter))

        mock_db = AsyncMock()

        with patch("forge.api.routes.webhooks._post_issue_comment", new_callable=AsyncMock) as mock_comment:
            await _run_webhook_pipeline(
                forge_db=mock_db,
                daemon_factory=daemon_factory,
                pipeline_id="fail-pipeline-id",
                project_dir="/tmp/proj",
                task_description="Fail task",
                issue_url="https://github.com/owner/repo/issues/1",
                issue_number=1,
                repo_full_name="owner/repo",
            )

            # Should have posted 2 comments: start + error
            assert mock_comment.call_count == 2
            error_comment = mock_comment.call_args_list[1][0][2]
            assert "Pipeline failed" in error_comment
            assert "RuntimeError" in error_comment
            assert "SDK error" not in error_comment

            # DB should be marked as error
            mock_db.update_pipeline_status.assert_called_with("fail-pipeline-id", "error")

    async def test_pr_failure_still_posts_complete(self):
        from forge.api.routes.webhooks import _run_webhook_pipeline

        mock_daemon = AsyncMock()
        mock_graph = MagicMock()
        mock_graph.tasks = []
        mock_daemon.plan.return_value = mock_graph

        daemon_factory = MagicMock(return_value=(mock_daemon, MagicMock()))
        mock_db = AsyncMock()

        with (
            patch("forge.api.routes.webhooks._post_issue_comment", new_callable=AsyncMock) as mock_comment,
            patch("forge.api.routes.tasks._auto_create_pr", new_callable=AsyncMock, side_effect=RuntimeError("push failed")),
        ):
            await _run_webhook_pipeline(
                forge_db=mock_db,
                daemon_factory=daemon_factory,
                pipeline_id="pr-fail-pipeline",
                project_dir="/tmp/proj",
                task_description="PR fail task",
                issue_url="https://github.com/owner/repo/issues/5",
                issue_number=5,
                repo_full_name="owner/repo",
            )

            # Still posts completion comment (with PR error note)
            last_comment = mock_comment.call_args_list[-1][0][2]
            assert "Pipeline complete!" in last_comment
            assert "PR creation failed" in last_comment


# ===================================================================
# App registration test
# ===================================================================


class TestWebhookRouterRegistered:
    """Verify the webhook router is wired into the app."""

    async def test_webhook_endpoint_exists(self, client):
        """Webhook endpoint should not return 404 (it's registered)."""
        # An unsigned request should get 401 (signature check), not 404
        resp = await client.post(
            "/api/webhooks/github",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code != 404

    async def test_app_state_has_webhook_secret(self, client):
        """App state should have github_webhook_secret set."""
        app = client._transport.app
        assert hasattr(app.state, "github_webhook_secret")
        assert app.state.github_webhook_secret == WEBHOOK_SECRET


# ===================================================================
# History endpoint integration tests (github issue fields)
# ===================================================================


class TestHistoryGithubIssueFields:
    """Verify github_issue_url/number appear in history responses."""

    async def _setup_user_and_pipeline(self, client: AsyncClient):
        """Register a user, create a pipeline with issue fields, return (token, pipeline_id)."""
        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "webhook-hist@example.com",
                "password": "pass1234",
                "display_name": "Webhook User",
            },
        )
        assert resp.status_code == 201
        token = resp.json()["access_token"]

        # Look up user_id
        from sqlalchemy import select

        from forge.storage.db import UserRow

        app = client._transport.app
        db = app.state.db
        async with db._session_factory() as session:
            result = await session.execute(
                select(UserRow).where(UserRow.email == "webhook-hist@example.com")
            )
            user_id = result.scalar_one().id

        # Insert pipeline with github issue fields
        import uuid

        from forge.storage.db import PipelineRow

        pipeline_id = str(uuid.uuid4())
        async with db._session_factory() as session:
            row = PipelineRow(
                id=pipeline_id,
                description="Webhook pipeline",
                project_dir="/tmp/proj",
                status="complete",
                user_id=user_id,
                created_at="2026-01-01T00:00:00+00:00",
                github_issue_url="https://github.com/owner/repo/issues/42",
                github_issue_number=42,
            )
            session.add(row)
            await session.commit()

        return token, pipeline_id

    async def test_list_history_includes_issue_fields(self, client):
        token, _ = await self._setup_user_and_pipeline(client)
        resp = await client.get(
            "/api/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["github_issue_url"] == "https://github.com/owner/repo/issues/42"
        assert data[0]["github_issue_number"] == 42

    async def test_detail_history_includes_issue_fields(self, client):
        token, pipeline_id = await self._setup_user_and_pipeline(client)
        resp = await client.get(
            f"/api/history/{pipeline_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["github_issue_url"] == "https://github.com/owner/repo/issues/42"
        assert data["github_issue_number"] == 42

    async def test_non_webhook_pipeline_has_null_issue_fields(self, client):
        """Pipelines created without webhook should have null issue fields."""
        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "normal-user@example.com",
                "password": "pass1234",
                "display_name": "Normal User",
            },
        )
        token = resp.json()["access_token"]

        # Create a normal pipeline via API
        create_resp = await client.post(
            "/api/tasks",
            json={"description": "Normal pipeline", "project_path": "/tmp"},
            headers={"Authorization": f"Bearer {token}"},
        )
        pipeline_id = create_resp.json()["pipeline_id"]

        resp = await client.get(
            f"/api/history/{pipeline_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["github_issue_url"] is None
        assert data["github_issue_number"] is None
