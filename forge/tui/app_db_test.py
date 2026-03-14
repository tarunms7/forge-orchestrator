"""Tests for ForgeApp DB integration."""
import os
import pytest
from unittest.mock import patch


@pytest.fixture
def tmp_project(tmp_path):
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    return str(tmp_path)


@pytest.fixture
def central_db_dir(tmp_path):
    """Create a temporary central data directory for forge DB."""
    data_dir = tmp_path / "forge_data"
    data_dir.mkdir()
    return str(data_dir)


@pytest.mark.asyncio
async def test_app_creates_db_on_init_db(tmp_project, central_db_dir):
    from forge.tui.app import ForgeApp
    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()
        assert app._db is not None
        await app._db.close()


@pytest.mark.asyncio
async def test_app_db_path_uses_central_path(tmp_project, central_db_dir):
    from forge.tui.app import ForgeApp
    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        expected = os.path.join(central_db_dir, "forge.db")
        assert app._db_path == expected


@pytest.mark.asyncio
async def test_load_recent_pipelines_empty(tmp_project, central_db_dir):
    from forge.tui.app import ForgeApp
    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()
        result = await app._load_recent_pipelines()
        assert result == []
        await app._db.close()


@pytest.mark.asyncio
async def test_load_recent_pipelines_includes_id(tmp_project, central_db_dir):
    """_load_recent_pipelines should include 'id' and 'total_cost_usd' keys."""
    from forge.tui.app import ForgeApp
    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()
        await app._db.create_pipeline(
            id="test-pipe", description="Test pipeline",
            project_dir="/tmp", model_strategy="auto",
        )
        result = await app._load_recent_pipelines()
        assert len(result) == 1
        assert result[0]["id"] == "test-pipe"
        assert "total_cost_usd" in result[0]
        assert "cost" in result[0]
        await app._db.close()


@pytest.mark.asyncio
async def test_pipeline_replay_loading(tmp_project, central_db_dir):
    """on_pipeline_list_selected should create replay state and push read-only screen."""
    from forge.tui.app import ForgeApp
    from forge.tui.screens.pipeline import PipelineScreen
    from forge.tui.widgets.pipeline_list import PipelineList

    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()

        # Create a pipeline with events
        await app._db.create_pipeline(
            id="replay-pipe", description="Replay test",
            project_dir="/tmp", model_strategy="auto",
        )
        await app._db.log_event(
            pipeline_id="replay-pipe", task_id=None,
            event_type="pipeline:phase_changed",
            payload={"phase": "planning"},
        )
        await app._db.log_event(
            pipeline_id="replay-pipe", task_id=None,
            event_type="pipeline:plan_ready",
            payload={"tasks": [
                {"id": "t1", "title": "Task 1", "description": "D",
                 "files": ["a.py"], "depends_on": [], "complexity": "low"},
            ]},
        )

        # Mock push_screen and verify
        pushed_screens = []
        app.push_screen = lambda s: pushed_screens.append(s)

        event = PipelineList.Selected("replay-pipe")
        await app.on_pipeline_list_selected(event)

        assert len(pushed_screens) == 1
        screen = pushed_screens[0]
        assert isinstance(screen, PipelineScreen)
        assert screen._read_only is True
        # Verify state was hydrated from events
        assert screen._state.phase == "planning"
        assert "t1" in screen._state.tasks

        await app._db.close()


@pytest.mark.asyncio
async def test_action_reset_for_new_task_pushes_home(tmp_project, central_db_dir):
    """action_reset_for_new_task should pop all screens and push a fresh HomeScreen."""
    import asyncio
    from unittest.mock import PropertyMock
    from forge.tui.app import ForgeApp
    from forge.tui.screens.home import HomeScreen

    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()

        pushed_screens = []
        popped_count = [0]
        stack = [1, 2, 3, 4]

        def mock_pop():
            if len(stack) > 1:
                stack.pop()
                popped_count[0] += 1

        app.pop_screen = mock_pop
        app.push_screen = lambda s: pushed_screens.append(s)

        with patch.object(type(app), "screen_stack", new_callable=PropertyMock, return_value=stack):
            app.action_reset_for_new_task()
            await asyncio.sleep(0.05)

        # Should have popped 3 screens (4 -> 1)
        assert popped_count[0] == 3
        assert len(stack) == 1

        # Should have pushed a fresh HomeScreen
        assert len(pushed_screens) == 1
        assert isinstance(pushed_screens[0], HomeScreen)

        # State should be reset
        assert app._final_approval_pushed is False
        assert app._daemon is None
        assert app._pipeline_id is None

        await app._db.close()


@pytest.mark.asyncio
async def test_action_switch_home_pushes_home(tmp_project, central_db_dir):
    """action_switch_home should pop all screens and push a fresh HomeScreen."""
    import asyncio
    from unittest.mock import PropertyMock
    from forge.tui.app import ForgeApp
    from forge.tui.screens.home import HomeScreen

    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()

        pushed_screens = []
        stack = [1, 2, 3]

        def mock_pop():
            if len(stack) > 1:
                stack.pop()

        app.pop_screen = mock_pop
        app.push_screen = lambda s: pushed_screens.append(s)

        with patch.object(type(app), "screen_stack", new_callable=PropertyMock, return_value=stack):
            app.action_switch_home()
            await asyncio.sleep(0.05)

        assert len(stack) == 1
        assert len(pushed_screens) == 1
        assert isinstance(pushed_screens[0], HomeScreen)

        await app._db.close()


@pytest.mark.asyncio
async def test_action_reset_state_cleanup(tmp_project, central_db_dir):
    """action_reset_for_new_task should reset all pipeline state."""
    import asyncio
    from unittest.mock import PropertyMock
    from forge.tui.app import ForgeApp

    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()

        # Set up some pipeline state
        app._final_approval_pushed = True
        app._daemon = "fake"
        app._daemon_task = "fake"
        app._graph = "fake"
        app._pipeline_id = "pipe-123"
        app._cached_pipeline_branch = "forge/branch"
        app._pipeline_start_time = 12345.0

        stack = [1, 2]
        app.pop_screen = lambda: stack.pop() if len(stack) > 1 else None
        app.push_screen = lambda s: None

        with patch.object(type(app), "screen_stack", new_callable=PropertyMock, return_value=stack):
            app.action_reset_for_new_task()
            await asyncio.sleep(0.05)

        assert app._final_approval_pushed is False
        assert app._daemon is None
        assert app._daemon_task is None
        assert app._graph is None
        assert app._pipeline_id is None
        assert app._cached_pipeline_branch == ""
        assert app._pipeline_start_time is None

        await app._db.close()


@pytest.mark.asyncio
async def test_pipeline_replay_missing_pipeline(tmp_project, central_db_dir):
    """on_pipeline_list_selected notifies when pipeline not found."""
    from forge.tui.app import ForgeApp
    from forge.tui.widgets.pipeline_list import PipelineList

    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()

        notifications = []
        app.notify = lambda msg, **kw: notifications.append(msg)

        event = PipelineList.Selected("nonexistent")
        await app.on_pipeline_list_selected(event)

        assert any("not found" in n for n in notifications)
        await app._db.close()


@pytest.mark.asyncio
async def test_run_plan_passes_project_path_to_create_pipeline(tmp_project, central_db_dir):
    """_run_plan should pass project_path and project_name to create_pipeline."""
    from unittest.mock import AsyncMock, MagicMock
    from forge.tui.app import ForgeApp

    with patch("forge.core.paths.forge_data_dir", return_value=central_db_dir):
        app = ForgeApp(project_dir=tmp_project)
        await app._init_db()

        # Mock daemon and plan
        mock_daemon = MagicMock()
        mock_graph = MagicMock()
        mock_graph.tasks = [MagicMock(
            id="t1", title="T", description="D",
            files=[], depends_on=[], complexity=MagicMock(value="low"),
        )]
        mock_daemon.plan = AsyncMock(return_value=mock_graph)

        # Track the create_pipeline call
        original_create = app._db.create_pipeline
        create_calls = []

        async def tracked_create(**kwargs):
            create_calls.append(kwargs)
            return await original_create(**kwargs)

        app._db.create_pipeline = tracked_create

        with patch("forge.core.daemon.ForgeDaemon", return_value=mock_daemon), \
             patch("forge.core.events.EventEmitter"), \
             patch.object(app, "push_screen"):
            app._bus = MagicMock()
            app._source = MagicMock()
            app._source.connect = MagicMock()
            with patch("forge.tui.app.EventBus", return_value=app._bus), \
                 patch("forge.tui.app.EmbeddedSource", return_value=app._source):
                await app._run_plan("test task")

        assert len(create_calls) == 1
        assert create_calls[0]["project_path"] == tmp_project
        assert create_calls[0]["project_name"] == os.path.basename(tmp_project)

        await app._db.close()
