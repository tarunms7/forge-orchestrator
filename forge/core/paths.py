"""Centralized path resolution for all Forge data directories and files."""

from __future__ import annotations

import os


def forge_data_dir() -> str:
    """Return the central Forge data directory, creating it if needed.

    Resolution order:
    1. $FORGE_DATA_DIR env var
    2. $XDG_DATA_HOME/forge
    3. ~/.local/share/forge
    """
    explicit = os.environ.get("FORGE_DATA_DIR")
    if explicit:
        path = os.path.abspath(explicit)
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            path = os.path.join(xdg, "forge")
        else:
            path = os.path.join(os.path.expanduser("~"), ".local", "share", "forge")

    os.makedirs(path, exist_ok=True)
    return path


def forge_db_path() -> str:
    """Return absolute path to the central SQLite database file."""
    return os.path.join(forge_data_dir(), "forge.db")


def forge_db_url() -> str:
    """Return SQLAlchemy async connection URL for the central database."""
    return f"sqlite+aiosqlite:///{forge_db_path()}"


def forge_web_dir() -> str:
    """Return the path to the Forge web frontend directory.

    Resolution order:
    1. $FORGE_WEB_DIR env var
    2. <forge_data_dir>/repo/web  (installed by install.sh)
    3. <package_dir>/../../web    (development: running from git clone)
    """
    explicit = os.environ.get("FORGE_WEB_DIR")
    if explicit and os.path.isdir(explicit):
        return os.path.abspath(explicit)

    # Installed location: ~/.local/share/forge/repo/web
    installed = os.path.join(forge_data_dir(), "repo", "web")
    if os.path.isdir(installed):
        return installed

    # Development: relative to this file (forge/core/paths.py → ../../web)
    dev = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "web"))
    if os.path.isdir(dev):
        return dev

    return installed  # Return installed path even if missing — caller will error


def project_forge_dir(project_dir: str) -> str:
    """Return <project_dir>/.forge for project-local artifacts.

    Creates the directory if it doesn't exist.
    """
    path = os.path.join(os.path.abspath(project_dir), ".forge")
    os.makedirs(path, exist_ok=True)
    return path
