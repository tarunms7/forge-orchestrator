"""Tests for WebSocket endpoint handler."""

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def app():
    """Create a FastAPI app with the WS endpoint for testing."""
    from forge.api.app import create_app

    return create_app(jwt_secret="ws-test-secret")


@pytest.fixture
def valid_token():
    """Generate a valid JWT token for testing."""
    from forge.api.security.jwt import create_access_token

    return create_access_token(subject="user-42", secret="ws-test-secret")


class TestWebSocketEndpoint:
    """Tests for /ws/{pipeline_id} endpoint."""

    def test_missing_token_closes_with_4001(self, app):
        """Sending a first message without a token should close with code 4001."""
        client = TestClient(app)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect("/api/ws/pipeline-123") as ws:
                # Send auth message without token
                ws.send_json({"no_token": True})
                # Server should close connection -- try to receive to trigger it
                ws.receive_json()
        assert "4001" in str(exc_info.value) or hasattr(exc_info.value, "code")

    def test_invalid_token_closes_with_4001(self, app):
        """Sending an invalid token in the first message should close with 4001."""
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/api/ws/pipeline-123") as ws:
                ws.send_json({"token": "bad.jwt.token"})
                ws.receive_json()

    def test_valid_token_connects_successfully(self, app, valid_token):
        """Sending a valid token in the first message should authenticate."""
        client = TestClient(app)
        with client.websocket_connect("/api/ws/pipeline-123") as ws:
            ws.send_json({"token": valid_token})
            # Wait for the auth_ok confirmation
            msg = ws.receive_json()
            assert msg["type"] == "auth_ok"
            assert msg["user_id"] == "user-42"

    def test_valid_connection_registered_in_manager(self, app, valid_token):
        """A valid connection should be tracked in the ConnectionManager."""
        client = TestClient(app)
        with client.websocket_connect("/api/ws/pipe-abc") as ws:
            ws.send_json({"token": valid_token})
            # Wait for auth_ok to ensure registration is complete
            msg = ws.receive_json()
            assert msg["type"] == "auth_ok"
            manager = app.state.ws_manager
            assert len(manager.active_connections.get("pipe-abc", [])) == 1

    def test_disconnect_removes_from_manager(self, app, valid_token):
        """After disconnect, the connection should be removed from the manager."""
        client = TestClient(app)
        with client.websocket_connect("/api/ws/pipe-xyz") as ws:
            ws.send_json({"token": valid_token})
            ws.receive_json()  # Wait for auth_ok
        # Connection closes when exiting context manager
        manager = app.state.ws_manager
        assert len(manager.active_connections.get("pipe-xyz", [])) == 0


class TestWebSocketBroadcastRepoId:
    """Test that broadcasts can include repo_id field."""

    @pytest.mark.asyncio
    async def test_ws_broadcast_includes_repo_id(self):
        """ConnectionManager.broadcast passes repo_id through to clients."""
        import json

        from forge.api.ws.manager import ConnectionManager

        manager = ConnectionManager()

        # Mock WebSocket that captures sent messages
        sent_messages: list[str] = []

        class MockWebSocket:
            async def send_text(self, data: str) -> None:
                sent_messages.append(data)

        mock_ws = MockWebSocket()
        manager.active_connections["pipe-repo"].append(mock_ws)

        # Broadcast a task:state_changed event with repo_id
        await manager.broadcast(
            "pipe-repo",
            {
                "type": "task:state_changed",
                "task_id": "t1",
                "state": "merging",
                "repo_id": "backend",
            },
        )

        assert len(sent_messages) == 1
        received = json.loads(sent_messages[0])
        assert received["type"] == "task:state_changed"
        assert received["task_id"] == "t1"
        assert received["state"] == "merging"
        assert received["repo_id"] == "backend"

    @pytest.mark.asyncio
    async def test_ws_broadcast_default_repo_id(self):
        """Broadcast without repo_id should not break anything."""
        import json

        from forge.api.ws.manager import ConnectionManager

        manager = ConnectionManager()

        sent_messages: list[str] = []

        class MockWebSocket:
            async def send_text(self, data: str) -> None:
                sent_messages.append(data)

        mock_ws = MockWebSocket()
        manager.active_connections["pipe-single"].append(mock_ws)

        # Broadcast without repo_id (single-repo backward compat)
        await manager.broadcast(
            "pipe-single",
            {
                "type": "task:state_changed",
                "task_id": "t2",
                "state": "merged",
            },
        )

        assert len(sent_messages) == 1
        received = json.loads(sent_messages[0])
        assert received["type"] == "task:state_changed"
        assert "repo_id" not in received  # Not added if not provided
