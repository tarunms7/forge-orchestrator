"""Tests for sdk_helpers — streaming on_message callback and SdkResult."""

from unittest.mock import AsyncMock, patch

from claude_code_sdk import ClaudeCodeOptions, ResultMessage

from forge.core.sdk_helpers import SdkResult


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


class TestSdkResult:
    """Test the SdkResult dataclass and from_result_message classmethod."""

    def test_from_result_message_basic(self):
        msg = _make_result(result="hello", total_cost_usd=0.05)
        sdk_result = SdkResult.from_result_message(msg)
        assert sdk_result.result_text == "hello"
        assert sdk_result.cost_usd == 0.05
        assert sdk_result.is_error is False
        assert sdk_result.session_id == "test-session"
        assert sdk_result.duration_ms == 100

    def test_from_result_message_with_usage(self):
        msg = _make_result(usage={"input_tokens": 500, "output_tokens": 200})
        sdk_result = SdkResult.from_result_message(msg)
        assert sdk_result.input_tokens == 500
        assert sdk_result.output_tokens == 200

    def test_from_result_message_no_usage(self):
        msg = _make_result(usage=None)
        sdk_result = SdkResult.from_result_message(msg)
        assert sdk_result.input_tokens == 0
        assert sdk_result.output_tokens == 0

    def test_from_result_message_none_result(self):
        msg = _make_result(result=None)
        sdk_result = SdkResult.from_result_message(msg)
        assert sdk_result.result_text == ""

    def test_result_property_alias(self):
        msg = _make_result(result="test output")
        sdk_result = SdkResult.from_result_message(msg)
        assert sdk_result.result == sdk_result.result_text
        assert sdk_result.result == "test output"

    def test_from_result_message_error(self):
        msg = _make_result(is_error=True, result="Something failed")
        sdk_result = SdkResult.from_result_message(msg)
        assert sdk_result.is_error is True
        assert sdk_result.result_text == "Something failed"


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
        assert isinstance(result, SdkResult)
        assert result.session_id == "test-session"

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

        assert isinstance(result, SdkResult)
        assert result.session_id == "test-session"

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
        assert isinstance(result, SdkResult)

    async def test_returns_none_when_no_result(self):
        """sdk_query returns None when no ResultMessage is yielded."""

        class FakeMessage:
            pass

        async def fake_query(**kwargs):
            yield FakeMessage()

        with patch("forge.core.sdk_helpers.query", side_effect=fake_query):
            from forge.core.sdk_helpers import sdk_query

            result = await sdk_query(
                prompt="hello",
                options=ClaudeCodeOptions(max_turns=1),
            )

        assert result is None
