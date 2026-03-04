"""Auto-update utility for project conventions file.

Reads planner-discovered conventions and appends new sections to
`.forge/conventions.md` without modifying existing content.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("forge.conventions")

# ---------------------------------------------------------------------------
# Key → heading mapping
# ---------------------------------------------------------------------------

_KEY_HEADINGS: dict[str, str] = {
    "styling": "Styling",
    "state_management": "State Management",
    "component_patterns": "Component Patterns",
    "naming": "Naming",
    "testing": "Testing",
    "imports": "Imports",
    "error_handling": "Error Handling",
    "other": "Notes",
}

_MAX_FILE_SIZE = 10 * 1024  # 10 KB


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update_conventions_file(
    project_dir: str,
    planner_conventions: dict[str, str],
) -> None:
    """Append newly discovered conventions to ``.forge/conventions.md``.

    Existing content is never modified or deleted.  Only convention keys
    that do not already have a corresponding ``## Heading`` in the file
    are appended under a timestamped separator.

    Args:
        project_dir: Absolute path to the project root.
        planner_conventions: Mapping of convention keys (e.g. ``"styling"``)
            to free-text descriptions discovered by the planner.
    """
    forge_dir = os.path.join(project_dir, ".forge")
    filepath = os.path.join(forge_dir, "conventions.md")

    os.makedirs(forge_dir, exist_ok=True)

    # Read existing content (empty string if file doesn't exist yet).
    existing_content = ""
    if os.path.isfile(filepath):
        existing_content = _read_file(filepath)

    # Guard: skip update if file already exceeds the size cap.
    if len(existing_content.encode("utf-8")) > _MAX_FILE_SIZE:
        logger.warning(
            "conventions.md exceeds %d bytes — skipping auto-update",
            _MAX_FILE_SIZE,
        )
        return

    existing_headings = _extract_headings(existing_content)

    # Collect new sections to append.
    new_sections: list[str] = []
    for key, value in planner_conventions.items():
        if not value:
            continue
        heading = _KEY_HEADINGS.get(key, key.replace("_", " ").title())
        if heading.lower() in existing_headings:
            continue
        new_sections.append(f"## {heading}\n\n{value}")

    if not new_sections:
        return

    # Build the file content to write.
    parts: list[str] = []
    if not existing_content:
        parts.append("# Project Conventions\n")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    separator = f"---\n_Auto-discovered by Forge planner on {timestamp}:_"
    parts.append(separator)
    parts.extend(new_sections)

    append_text = "\n\n".join(parts) + "\n"

    # Append (or create) the file.
    with open(filepath, "a", encoding="utf-8") as fh:
        if existing_content:
            fh.write("\n\n")
        fh.write(append_text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_file(filepath: str) -> str:
    """Read a UTF-8 text file, returning empty string on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _extract_headings(content: str) -> set[str]:
    """Return a set of lowercased ``## Heading`` texts found in *content*."""
    headings: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            headings.add(stripped[3:].strip().lower())
    return headings
