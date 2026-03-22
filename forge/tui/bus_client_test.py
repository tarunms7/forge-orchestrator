"""Tests for ClientSource."""

import json

import pytest

from forge.tui.bus import ClientSource, EventBus


@pytest.mark.asyncio
async def test_client_source_parses_messages():
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("task:state_changed", handler)

    source = ClientSource("ws://localhost:8000/api/ws/test-pipeline", bus, token="fake")
    message = {"type": "task:state_changed", "task_id": "t1", "state": "done"}
    await source._handle_message(json.dumps(message))

    assert len(received) == 1
    assert received[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_client_source_ignores_auth_ok():
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("auth_ok", handler)

    source = ClientSource("ws://localhost:8000/api/ws/test", bus, token="fake")
    await source._handle_message(json.dumps({"type": "auth_ok", "user_id": "u1"}))

    assert len(received) == 0
    assert source._authenticated


@pytest.mark.asyncio
async def test_client_source_ignores_invalid_json():
    bus = EventBus()
    source = ClientSource("ws://localhost:8000/api/ws/test", bus, token="fake")
    await source._handle_message("not json at all")  # should not crash


@pytest.mark.asyncio
async def test_client_source_ignores_missing_type():
    bus = EventBus()
    received = []

    async def handler(data):
        received.append(data)

    bus.subscribe("task:state_changed", handler)

    source = ClientSource("ws://localhost:8000/api/ws/test", bus, token="fake")
    await source._handle_message(json.dumps({"data": "no type field"}))

    assert len(received) == 0
