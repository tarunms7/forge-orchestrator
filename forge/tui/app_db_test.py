"""Tests for ForgeApp DB integration."""
import os
import pytest


@pytest.fixture
def tmp_project(tmp_path):
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    return str(tmp_path)


@pytest.mark.asyncio
async def test_app_creates_db_on_init_db(tmp_project):
    from forge.tui.app import ForgeApp
    app = ForgeApp(project_dir=tmp_project)
    await app._init_db()
    assert app._db is not None
    await app._db.close()


@pytest.mark.asyncio
async def test_app_db_path(tmp_project):
    from forge.tui.app import ForgeApp
    app = ForgeApp(project_dir=tmp_project)
    expected = os.path.join(tmp_project, ".forge", "forge.db")
    assert app._db_path == expected


@pytest.mark.asyncio
async def test_load_recent_pipelines_empty(tmp_project):
    from forge.tui.app import ForgeApp
    app = ForgeApp(project_dir=tmp_project)
    await app._init_db()
    result = await app._load_recent_pipelines()
    assert result == []
    await app._db.close()
