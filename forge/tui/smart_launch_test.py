"""Tests for smart launch detection."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from forge.tui.app import detect_server


@pytest.mark.asyncio
async def test_detect_server_reachable():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        # Patch httpx.AsyncClient used inside detect_server's lazy import
        result = await detect_server("http://localhost:8000")
    assert result is True


@pytest.mark.asyncio
async def test_detect_server_unreachable():
    result = await detect_server("http://localhost:59999", timeout=0.05)
    assert result is False
