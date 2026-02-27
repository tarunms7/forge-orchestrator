"""Tests for WebSocket ConnectionManager."""

import json



class FakeWebSocket:
    """Fake WebSocket for testing — mimics FastAPI WebSocket interface."""

    def __init__(self, *, open: bool = True):
        self.sent: list[str] = []
        self.accepted: bool = False
        self._open = open

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        if not self._open:
            raise RuntimeError("WebSocket is closed")
        self.sent.append(data)

    def close(self) -> None:
        self._open = False


class TestConnectionManager:
    """Tests for ConnectionManager."""

    async def test_connect_accepts_and_stores(self):
        from forge.api.ws.manager import ConnectionManager

        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, user_id="u1", pipeline_id="p1")

        assert ws.accepted is True
        assert ws in mgr.active_connections["p1"]

    async def test_disconnect_removes_websocket(self):
        from forge.api.ws.manager import ConnectionManager

        mgr = ConnectionManager()
        ws = FakeWebSocket()
        await mgr.connect(ws, user_id="u1", pipeline_id="p1")
        mgr.disconnect(ws, pipeline_id="p1")

        assert ws not in mgr.active_connections.get("p1", [])

    async def test_disconnect_nonexistent_is_safe(self):
        from forge.api.ws.manager import ConnectionManager

        mgr = ConnectionManager()
        ws = FakeWebSocket()
        # Should not raise even if never connected
        mgr.disconnect(ws, pipeline_id="p1")

    async def test_broadcast_sends_json_to_all(self):
        from forge.api.ws.manager import ConnectionManager

        mgr = ConnectionManager()
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect(ws1, user_id="u1", pipeline_id="p1")
        await mgr.connect(ws2, user_id="u2", pipeline_id="p1")

        msg = {"event": "status", "data": "running"}
        await mgr.broadcast("p1", msg)

        expected = json.dumps(msg)
        assert ws1.sent == [expected]
        assert ws2.sent == [expected]

    async def test_broadcast_removes_dead_connections(self):
        from forge.api.ws.manager import ConnectionManager

        mgr = ConnectionManager()
        ws_alive = FakeWebSocket()
        ws_dead = FakeWebSocket(open=False)
        await mgr.connect(ws_alive, user_id="u1", pipeline_id="p1")
        await mgr.connect(ws_dead, user_id="u2", pipeline_id="p1")

        msg = {"event": "done"}
        await mgr.broadcast("p1", msg)

        # Dead connection should be pruned
        assert ws_dead not in mgr.active_connections["p1"]
        # Alive connection should still be there and have received the message
        assert ws_alive in mgr.active_connections["p1"]
        assert ws_alive.sent == [json.dumps(msg)]

    async def test_broadcast_to_unknown_pipeline_is_noop(self):
        from forge.api.ws.manager import ConnectionManager

        mgr = ConnectionManager()
        # Should not raise
        await mgr.broadcast("nonexistent", {"event": "test"})

    async def test_multiple_pipelines_isolated(self):
        from forge.api.ws.manager import ConnectionManager

        mgr = ConnectionManager()
        ws_p1 = FakeWebSocket()
        ws_p2 = FakeWebSocket()
        await mgr.connect(ws_p1, user_id="u1", pipeline_id="p1")
        await mgr.connect(ws_p2, user_id="u2", pipeline_id="p2")

        await mgr.broadcast("p1", {"event": "only_p1"})

        assert len(ws_p1.sent) == 1
        assert len(ws_p2.sent) == 0
