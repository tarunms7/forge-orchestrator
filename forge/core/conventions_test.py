"""Tests for forge.core.conventions."""

from __future__ import annotations

import logging
import os

import pytest

from forge.core.conventions import update_conventions_file


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path):
    """Return a temporary project directory path as a string."""
    return str(tmp_path)


def _conventions_path(project_dir: str) -> str:
    return os.path.join(project_dir, ".forge", "conventions.md")


# ---------------------------------------------------------------------------
# Creating from scratch
# ---------------------------------------------------------------------------


class TestCreateFromScratch:
    """File does not exist yet — should be created."""

    def test_creates_file_with_header(self, project):
        update_conventions_file(project, {"styling": "Use Tailwind v4"})
        path = _conventions_path(project)
        assert os.path.isfile(path)
        content = open(path).read()
        assert content.startswith("# Project Conventions\n")

    def test_contains_section_heading(self, project):
        update_conventions_file(project, {"naming": "camelCase for vars"})
        content = open(_conventions_path(project)).read()
        assert "## Naming" in content
        assert "camelCase for vars" in content

    def test_timestamp_separator_present(self, project):
        update_conventions_file(project, {"testing": "pytest only"})
        content = open(_conventions_path(project)).read()
        assert "Auto-discovered by Forge planner on" in content

    def test_multiple_keys_all_written(self, project):
        conventions = {
            "styling": "Tailwind",
            "imports": "Absolute imports only",
        }
        update_conventions_file(project, conventions)
        content = open(_conventions_path(project)).read()
        assert "## Styling" in content
        assert "## Imports" in content


# ---------------------------------------------------------------------------
# Appending to existing file
# ---------------------------------------------------------------------------


class TestAppendToExisting:
    """File already exists — new sections appended, old content preserved."""

    def test_existing_content_preserved(self, project):
        path = _conventions_path(project)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("# Project Conventions\n\n## Styling\n\nUse CSS modules\n")

        update_conventions_file(project, {"naming": "snake_case"})
        content = open(path).read()
        # Old content still present.
        assert "Use CSS modules" in content
        # New section added.
        assert "## Naming" in content
        assert "snake_case" in content

    def test_only_new_sections_added(self, project):
        path = _conventions_path(project)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("# Project Conventions\n\n## Styling\n\nOld styling\n")

        update_conventions_file(
            project,
            {"styling": "New styling", "testing": "Use pytest"},
        )
        content = open(path).read()
        # Styling should NOT be duplicated.
        assert content.count("## Styling") == 1
        # Testing is new.
        assert "## Testing" in content


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------


class TestNoDuplication:
    """Existing headings must not be re-added."""

    def test_exact_heading_not_duplicated(self, project):
        path = _conventions_path(project)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("# Project Conventions\n\n## Naming\n\nOriginal\n")

        update_conventions_file(project, {"naming": "Duplicate attempt"})
        content = open(path).read()
        assert content.count("## Naming") == 1
        assert "Duplicate attempt" not in content

    def test_case_insensitive_match(self, project):
        """Heading `## styling` should match key `styling` → `Styling`."""
        path = _conventions_path(project)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("# Project Conventions\n\n## styling\n\nold rules\n")

        update_conventions_file(project, {"styling": "New rules"})
        content = open(path).read()
        # Should not add a second styling section.
        assert content.count("tyling") == 1  # covers both "## styling" / "## Styling"
        assert "New rules" not in content

    def test_mixed_case_heading_match(self, project):
        path = _conventions_path(project)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("# Conventions\n\n## STATE MANAGEMENT\n\nRedux\n")

        update_conventions_file(project, {"state_management": "Zustand"})
        content = open(path).read()
        assert "Zustand" not in content


# ---------------------------------------------------------------------------
# Falsy value skipping
# ---------------------------------------------------------------------------


class TestSkipFalsyValues:
    """Empty or None values should be silently skipped."""

    def test_empty_string_skipped(self, project):
        update_conventions_file(project, {"styling": ""})
        path = _conventions_path(project)
        assert not os.path.isfile(path)

    def test_none_value_skipped(self, project):
        update_conventions_file(project, {"styling": None})  # type: ignore[dict-item]
        path = _conventions_path(project)
        assert not os.path.isfile(path)

    def test_mixed_falsy_and_valid(self, project):
        update_conventions_file(
            project,
            {"styling": "", "naming": "camelCase", "testing": None},  # type: ignore[dict-item]
        )
        content = open(_conventions_path(project)).read()
        assert "## Naming" in content
        assert "## Styling" not in content
        assert "## Testing" not in content


# ---------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------


class TestSizeCap:
    """Files > 10 KB should not be appended to."""

    def test_large_file_skips_update(self, project, caplog):
        path = _conventions_path(project)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write > 10 KB of existing content.
        with open(path, "w") as fh:
            fh.write("x" * (10 * 1024 + 1))

        with caplog.at_level(logging.WARNING, logger="forge.conventions"):
            update_conventions_file(project, {"styling": "Tailwind"})

        # Content should be unchanged.
        assert os.path.getsize(path) == 10 * 1024 + 1
        assert "exceeds" in caplog.text

    def test_exactly_at_limit_still_updates(self, project):
        path = _conventions_path(project)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write exactly 10 KB (not exceeding).
        with open(path, "w") as fh:
            fh.write("x" * (10 * 1024))

        update_conventions_file(project, {"styling": "Tailwind"})
        content = open(path).read()
        assert "## Styling" in content


# ---------------------------------------------------------------------------
# Directory creation
# ---------------------------------------------------------------------------


class TestDirectoryCreation:
    """`.forge/` should be created if missing."""

    def test_forge_dir_created(self, project):
        forge_dir = os.path.join(project, ".forge")
        assert not os.path.isdir(forge_dir)

        update_conventions_file(project, {"styling": "Tailwind"})
        assert os.path.isdir(forge_dir)
        assert os.path.isfile(os.path.join(forge_dir, "conventions.md"))


# ---------------------------------------------------------------------------
# Key mapping
# ---------------------------------------------------------------------------


class TestKeyMapping:
    """Convention keys should map to the correct headings."""

    @pytest.mark.parametrize(
        ("key", "expected_heading"),
        [
            ("styling", "## Styling"),
            ("state_management", "## State Management"),
            ("component_patterns", "## Component Patterns"),
            ("naming", "## Naming"),
            ("testing", "## Testing"),
            ("imports", "## Imports"),
            ("error_handling", "## Error Handling"),
            ("other", "## Notes"),
        ],
    )
    def test_known_key_produces_heading(self, project, key, expected_heading):
        update_conventions_file(project, {key: "Some value"})
        content = open(_conventions_path(project)).read()
        assert expected_heading in content

    def test_unknown_key_titlecased(self, project):
        update_conventions_file(project, {"code_review": "Always review"})
        content = open(_conventions_path(project)).read()
        assert "## Code Review" in content
