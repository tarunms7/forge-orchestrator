"""Tests for the notification service webhook."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.api.services.notification_service import NotificationService


def _mock_response(status_code: int) -> MagicMock:
    """Create a mock httpx.Response with the given status code."""
    resp = MagicMock()
    resp.status_code = status_code
    return resp


class TestNotificationService:
    """Tests for NotificationService.send_webhook."""

    @pytest.mark.asyncio
    async def test_send_webhook_success(self):
        """send_webhook should return ok=True for successful POST."""
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(200)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("forge.api.services.notification_service.httpx.AsyncClient", return_value=mock_client):
            service = NotificationService()
            result = await service.send_webhook(
                "https://hooks.slack.com/test",
                {"text": "Pipeline complete!"},
            )

        assert result["ok"] is True
        assert result["status_code"] == 200
        mock_client.post.assert_called_once_with(
            "https://hooks.slack.com/test",
            json={"text": "Pipeline complete!"},
        )

    @pytest.mark.asyncio
    async def test_send_webhook_failure(self):
        """send_webhook should return ok=False for 4xx/5xx responses."""
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(400)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("forge.api.services.notification_service.httpx.AsyncClient", return_value=mock_client):
            service = NotificationService()
            result = await service.send_webhook(
                "https://hooks.slack.com/bad",
                {"text": "This will fail"},
            )

        assert result["ok"] is False
        assert result["status_code"] == 400

    @pytest.mark.asyncio
    async def test_send_webhook_discord_format(self):
        """send_webhook should work with Discord-style payload."""
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_response(204)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("forge.api.services.notification_service.httpx.AsyncClient", return_value=mock_client):
            service = NotificationService()
            result = await service.send_webhook(
                "https://discord.com/api/webhooks/test",
                {"content": "Pipeline finished!"},
            )

        assert result["ok"] is True
        assert result["status_code"] == 204
