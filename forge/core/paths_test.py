"""Tests for forge.core.paths module."""

from __future__ import annotations

import os

import pytest

from forge.core.paths import forge_data_dir, forge_db_path, forge_db_url, project_forge_dir


def test_forge_data_dir_respects_env_var(tmp_path, monkeypatch):
    """FORGE_DATA_DIR env var takes highest priority."""
    target = str(tmp_path / "custom-data")
    monkeypatch.setenv("FORGE_DATA_DIR", target)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    result = forge_data_dir()
    assert result == target
    assert os.path.isdir(target)


def test_forge_data_dir_respects_xdg(tmp_path, monkeypatch):
    """Falls back to $XDG_DATA_HOME/forge when FORGE_DATA_DIR is unset."""
    xdg = str(tmp_path / "xdg-data")
    monkeypatch.delenv("FORGE_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", xdg)

    result = forge_data_dir()
    assert result == os.path.join(xdg, "forge")
    assert os.path.isdir(result)


def test_forge_data_dir_fallback(tmp_path, monkeypatch):
    """Falls back to ~/.local/share/forge when no env vars set."""
    monkeypatch.delenv("FORGE_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = forge_data_dir()
    expected = os.path.join(str(tmp_path), ".local", "share", "forge")
    assert result == expected
    assert os.path.isdir(expected)


def test_forge_data_dir_creates_directory(tmp_path, monkeypatch):
    """Directory is created if it doesn't exist."""
    target = str(tmp_path / "new" / "nested" / "dir")
    monkeypatch.setenv("FORGE_DATA_DIR", target)

    assert not os.path.exists(target)
    forge_data_dir()
    assert os.path.isdir(target)


def test_forge_db_path(tmp_path, monkeypatch):
    """forge_db_path returns <data_dir>/forge.db."""
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    result = forge_db_path()
    assert result == os.path.join(str(tmp_path), "forge.db")


def test_forge_db_url(tmp_path, monkeypatch):
    """forge_db_url returns a valid SQLAlchemy async URL."""
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    result = forge_db_url()
    expected_path = os.path.join(str(tmp_path), "forge.db")
    assert result == f"sqlite+aiosqlite:///{expected_path}"


def test_project_forge_dir(tmp_path):
    """project_forge_dir returns <project>/.forge and creates it."""
    project = str(tmp_path / "my-project")
    os.makedirs(project)

    result = project_forge_dir(project)
    expected = os.path.join(project, ".forge")
    assert result == expected
    assert os.path.isdir(expected)


def test_project_forge_dir_creates_directory(tmp_path):
    """project_forge_dir creates the .forge dir if missing."""
    project = str(tmp_path / "new-project")
    # project dir doesn't exist yet either
    result = project_forge_dir(project)
    assert os.path.isdir(result)


def test_project_forge_dir_absolute_path(tmp_path):
    """project_forge_dir always returns an absolute path."""
    result = project_forge_dir("relative/path")
    assert os.path.isabs(result)
    assert result.endswith(os.path.join("relative", "path", ".forge"))
