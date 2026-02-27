"""Tests for sdk_helpers — streaming on_message callback."""

from unittest.mock import AsyncMock, patch

import pytest
from claude_code_sdk import ClaudeCodeOptions, ResultMessage


def _make_result(**overrides) -> ResultMessage:
    """Create a ResultMessage with sensible defaults."""
    defaults = dict(
        subtype="success",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="test-session",
        total_cost_usd=0.0,
    )
    defaults.update(overrides)
    return ResultMessage(**defaults)


class TestSdkQueryOnMessage:
    """Test that sdk_query calls on_message for every yielded message."""

    async def test_on_message_called_for_each_message(self):
        """Monkeypatch query() with a fake async generator, verify callback is invoked."""
        fake_result = _make_result()

        async def fake_query(**kwargs):
            yield fake_result

        callback = AsyncMock()

        with patch("forge.core.sdk_helpers.query", side_effect=fake_query):
            from forge.core.sdk_helpers import sdk_query

            result = await sdk_query(
                prompt="hello",
                options=ClaudeCodeOptions(max_turns=1),
                on_message=callback,
            )

        callback.assert_awaited_once_with(fake_result)
        assert result is fake_result

    async def test_on_message_none_by_default(self):
        """When on_message is not provided, sdk_query still works normally."""
        fake_result = _make_result()

        async def fake_query(**kwargs):
            yield fake_result

        with patch("forge.core.sdk_helpers.query", side_effect=fake_query):
            from forge.core.sdk_helpers import sdk_query

            result = await sdk_query(
                prompt="hello",
                options=ClaudeCodeOptions(max_turns=1),
            )

        assert result is fake_result

    async def test_on_message_called_for_non_result_messages(self):
        """Callback should fire for ALL messages, not just ResultMessage."""

        class FakeMessage:
            pass

        fake_msg = FakeMessage()
        fake_result = _make_result()

        async def fake_query(**kwargs):
            yield fake_msg
            yield fake_result

        callback = AsyncMock()

        with patch("forge.core.sdk_helpers.query", side_effect=fake_query):
            from forge.core.sdk_helpers import sdk_query

            result = await sdk_query(
                prompt="hello",
                options=ClaudeCodeOptions(max_turns=1),
                on_message=callback,
            )

        assert callback.await_count == 2
        callback.assert_any_await(fake_msg)
        callback.assert_any_await(fake_result)
        assert result is fake_result
