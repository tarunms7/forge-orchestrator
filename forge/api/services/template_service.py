"""Service for saving and loading task templates as JSON files."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


class TemplateService:
    """Manage task templates stored as individual JSON files.

    Args:
        templates_dir: Directory where template JSON files are stored.
            Created automatically if it does not exist.
    """

    def __init__(self, templates_dir: str | None = None) -> None:
        if templates_dir is None:
            templates_dir = os.path.join(os.path.expanduser("~"), ".forge", "templates")
        self._dir = Path(templates_dir)

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _slugify(name: str) -> str:
        """Convert a template name to a safe filename slug."""
        slug = name.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug or "template"

    def _path_for(self, name: str) -> Path:
        return self._dir / f"{self._slugify(name)}.json"

    def save(self, name: str, description: str, category: str) -> dict:
        """Save a template to a JSON file.

        If a template with the same name already exists, it is overwritten.

        Returns the saved template dict.
        """
        self._ensure_dir()
        template = {"name": name, "description": description, "category": category}
        path = self._path_for(name)
        path.write_text(json.dumps(template, indent=2))
        return template

    def list_all(self) -> list[dict]:
        """List all saved templates.

        Returns a list of dicts, each containing name, description, and category.
        """
        self._ensure_dir()
        templates: list[dict] = []
        for file in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(file.read_text())
                templates.append(
                    {
                        "name": data["name"],
                        "description": data["description"],
                        "category": data["category"],
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return templates

    def get(self, name: str) -> dict | None:
        """Get a single template by name.

        Returns the template dict, or None if not found.
        """
        path = self._path_for(name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return {"name": data["name"], "description": data["description"], "category": data["category"]}
        except (json.JSONDecodeError, KeyError):
            return None

    def delete(self, name: str) -> bool:
        """Delete a template by name.

        Returns True if deleted, False if the template did not exist.
        """
        path = self._path_for(name)
        if not path.exists():
            return False
        path.unlink()
        return True
