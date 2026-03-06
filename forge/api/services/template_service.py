"""Service for managing user-owned pipeline templates backed by the database."""

from __future__ import annotations

import json


class TemplateService:
    """Manage pipeline templates stored in the database.

    Args:
        db: A Database instance used for template persistence.
    """

    def __init__(self, db) -> None:
        self._db = db

    async def save(self, user_id: str, name: str, config: dict) -> dict:
        """Create a new user template.

        Returns a dict with the template's id, user_id, name, and config.
        """
        config_json = json.dumps(config)
        row = await self._db.create_user_template(user_id, name, config_json)
        return {
            "id": row.id,
            "user_id": row.user_id,
            "name": row.name,
            "config": json.loads(row.config_json),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def list_all(self, user_id: str | None = None) -> list[dict]:
        """List templates. If user_id is provided, returns only that user's templates."""
        if user_id is None:
            return []
        rows = await self._db.list_user_templates(user_id)
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "name": row.name,
                "config": json.loads(row.config_json),
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in rows
        ]

    async def get(self, template_id: str) -> dict | None:
        """Get a single template by ID.

        Returns the template dict, or None if not found.
        """
        row = await self._db.get_user_template(template_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "user_id": row.user_id,
            "name": row.name,
            "config": json.loads(row.config_json),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def update(self, template_id: str, updates: dict) -> dict | None:
        """Update a template by ID.

        The updates dict may contain 'name' and/or 'config' keys.
        Returns the updated template dict, or None if not found.
        """
        name = updates.get("name")
        config = updates.get("config")
        config_json = json.dumps(config) if config is not None else None
        row = await self._db.update_user_template(template_id, name=name, config_json=config_json)
        if row is None:
            return None
        return {
            "id": row.id,
            "user_id": row.user_id,
            "name": row.name,
            "config": json.loads(row.config_json),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def delete(self, template_id: str) -> bool:
        """Delete a template by ID.

        Returns True if deleted, False if not found.
        """
        return await self._db.delete_user_template(template_id)
