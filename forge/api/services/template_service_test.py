"""Tests for the TemplateService (save, list, get, delete)."""

import os

import pytest

from forge.api.services.template_service import TemplateService


@pytest.fixture
def templates_dir(tmp_path):
    """Provide a temporary directory for template storage."""
    return str(tmp_path / "templates")


@pytest.fixture
def service(templates_dir):
    """Create a TemplateService pointed at a temp directory."""
    return TemplateService(templates_dir=templates_dir)


def test_save_creates_template_file(service, templates_dir):
    """save() should create a JSON file in the templates directory."""
    service.save(name="REST API", description="Build a REST API", category="backend")

    files = os.listdir(templates_dir)
    assert len(files) == 1
    assert files[0].endswith(".json")


def test_save_and_get_roundtrip(service):
    """save() then get() should return the same template data."""
    service.save(name="CLI Tool", description="Build a CLI tool", category="tooling")

    result = service.get("CLI Tool")
    assert result is not None
    assert result["name"] == "CLI Tool"
    assert result["description"] == "Build a CLI tool"
    assert result["category"] == "tooling"


def test_get_nonexistent_returns_none(service):
    """get() for a missing template should return None."""
    result = service.get("does-not-exist")
    assert result is None


def test_list_all_returns_all_templates(service):
    """list_all() should return all saved templates."""
    service.save(name="REST API", description="Build a REST API", category="backend")
    service.save(name="CLI Tool", description="Build a CLI tool", category="tooling")
    service.save(name="Bug Fix", description="Fix a bug", category="maintenance")

    templates = service.list_all()
    assert len(templates) == 3

    names = {t["name"] for t in templates}
    assert names == {"REST API", "CLI Tool", "Bug Fix"}


def test_list_all_empty_dir(service):
    """list_all() should return an empty list when no templates exist."""
    templates = service.list_all()
    assert templates == []


def test_delete_existing_template(service):
    """delete() should remove a template and return True."""
    service.save(name="Temp", description="Temporary", category="test")

    result = service.delete("Temp")
    assert result is True
    assert service.get("Temp") is None


def test_delete_nonexistent_returns_false(service):
    """delete() for a missing template should return False."""
    result = service.delete("ghost")
    assert result is False


def test_save_overwrites_existing(service):
    """save() with the same name should overwrite the previous template."""
    service.save(name="API", description="v1", category="backend")
    service.save(name="API", description="v2", category="backend")

    result = service.get("API")
    assert result is not None
    assert result["description"] == "v2"

    templates = service.list_all()
    assert len(templates) == 1


def test_list_all_contains_expected_keys(service):
    """Each template dict from list_all() should have name, description, category."""
    service.save(name="Test", description="A test", category="testing")

    templates = service.list_all()
    assert len(templates) == 1

    t = templates[0]
    assert "name" in t
    assert "description" in t
    assert "category" in t
