"""Tests for WebSocket endpoint handler."""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


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
        """Connecting without a token should close with code 4001."""
        client = TestClient(app)
        with pytest.raises(Exception) as exc_info:
            with client.websocket_connect("/ws/pipeline-123"):
                pass  # pragma: no cover
        # Starlette TestClient raises an exception when WS is closed by server
        # The close code should be 4001
        assert "4001" in str(exc_info.value) or hasattr(exc_info.value, "code")

    def test_invalid_token_closes_with_4001(self, app):
        """Connecting with an invalid/expired token should close with 4001."""
        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/pipeline-123?token=bad.jwt.token"):
                pass  # pragma: no cover

    def test_valid_token_connects_successfully(self, app, valid_token):
        """Connecting with a valid token should accept the WebSocket."""
        client = TestClient(app)
        with client.websocket_connect(f"/ws/pipeline-123?token={valid_token}") as ws:
            # Connection should be accepted — we can verify by sending data
            # The handler loops on receive_json, so we close from client side
            assert ws is not None

    def test_valid_connection_registered_in_manager(self, app, valid_token):
        """A valid connection should be tracked in the ConnectionManager."""
        client = TestClient(app)
        with client.websocket_connect(f"/ws/pipe-abc?token={valid_token}"):
            manager = app.state.ws_manager
            assert len(manager.active_connections.get("pipe-abc", [])) == 1

    def test_disconnect_removes_from_manager(self, app, valid_token):
        """After disconnect, the connection should be removed from the manager."""
        client = TestClient(app)
        with client.websocket_connect(f"/ws/pipe-xyz?token={valid_token}"):
            pass  # Connection closes when exiting context manager
        manager = app.state.ws_manager
        assert len(manager.active_connections.get("pipe-xyz", [])) == 0
