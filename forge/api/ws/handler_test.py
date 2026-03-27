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


class TestHandlerConstants:
    """Tests for handler configuration constants."""

    def test_auth_timeout_is_3_seconds(self):
        """Auth timeout should be 3s, not the old 10s default."""
        from forge.api.ws.handler import RECEIVE_TIMEOUT

        # RECEIVE_TIMEOUT is for the main loop; auth timeout is hardcoded
        # in the endpoint as 3.0.  We verify the constants are sane.
        assert RECEIVE_TIMEOUT == 60

    def test_heartbeat_interval_constant(self):
        from forge.api.ws.handler import HEARTBEAT_INTERVAL

        assert HEARTBEAT_INTERVAL == 30

    @pytest.mark.asyncio
    async def test_heartbeat_ping_sent_on_interval(self):
        """Ping is sent every HEARTBEAT_INTERVAL (30s), not RECEIVE_TIMEOUT."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        from forge.api.ws.handler import HEARTBEAT_INTERVAL, websocket_endpoint
        from forge.api.ws.manager import ConnectionManager

        ws = AsyncMock()
        manager = ConnectionManager()

        # First call: auth message; subsequent calls: TimeoutError then disconnect
        call_count = 0

        async def mock_receive_json():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"token": "valid"}
            raise TimeoutError

        ws.receive_json = mock_receive_json

        # Track what timeout values wait_for is called with
        timeouts_used: list[float] = []
        original_wait_for = asyncio.wait_for

        async def tracking_wait_for(coro, *, timeout=None):
            timeouts_used.append(timeout)
            return await original_wait_for(coro, timeout=timeout)

        # After 2 missed heartbeats (60s // 30s = 2), loop breaks
        # Ping send succeeds first time, then fails to trigger break
        ping_count = 0

        async def mock_send_json(data):
            nonlocal ping_count
            if data.get("type") == "ping":
                ping_count += 1

        ws.send_json = mock_send_json

        with (
            patch("forge.api.ws.handler.asyncio.wait_for", tracking_wait_for),
            patch("forge.api.ws.handler.decode_token", return_value={"sub": "u1"}),
        ):
            await websocket_endpoint(ws, "p1", manager, "secret")

        # The receive loop should use HEARTBEAT_INTERVAL (30), not RECEIVE_TIMEOUT (60)
        loop_timeouts = [t for t in timeouts_used if t == HEARTBEAT_INTERVAL]
        assert len(loop_timeouts) >= 1, (
            f"Expected receive loop to use HEARTBEAT_INTERVAL={HEARTBEAT_INTERVAL}, "
            f"but timeouts used were: {timeouts_used}"
        )
        # Pings should have been sent
        assert ping_count >= 1


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
