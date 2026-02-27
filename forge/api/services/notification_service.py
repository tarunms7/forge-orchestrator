"""Notification service: webhook delivery for Slack/Discord integrations."""

from __future__ import annotations

import httpx


class NotificationService:
    """Send notifications via webhooks (Slack/Discord format)."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def send_webhook(self, url: str, payload: dict) -> dict:
        """Send an async HTTP POST to a webhook URL.

        Args:
            url: The webhook URL (Slack or Discord endpoint).
            payload: JSON payload to send. For Slack, use ``{"text": "..."}``
                format. For Discord, use ``{"content": "..."}`` format.

        Returns:
            Dict with ``status_code`` and ``ok`` keys.

        Raises:
            httpx.HTTPError: If the HTTP request fails.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload)

        return {
            "status_code": response.status_code,
            "ok": 200 <= response.status_code < 300,
        }
