"""Audit log service for recording user actions."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.api.models.user import AuditLogRow


class AuditService:
    """Service for creating and querying audit log entries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        user_id: str,
        action: str,
        metadata: dict[str, Any] | None = None,
        ip: str | None = None,
    ) -> None:
        """Insert an audit log entry.

        Args:
            user_id: The ID of the user performing the action.
            action: Short description of the action (e.g. ``"login"``).
            metadata: Optional dictionary of extra context, stored as JSON.
            ip: Optional IP address of the request.
        """
        row = AuditLogRow(
            user_id=user_id,
            action=action,
            metadata_json=json.dumps(metadata) if metadata is not None else None,
            ip_address=ip,
        )
        self._session.add(row)
        await self._session.commit()

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[AuditLogRow]:
        """Return the most recent audit log entries for a given user.

        Args:
            user_id: Filter logs to this user.
            limit: Maximum number of entries to return (default 100).

        Returns:
            List of :class:`AuditLogRow` ordered by timestamp descending.
        """
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.user_id == user_id)
            .order_by(AuditLogRow.timestamp.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
