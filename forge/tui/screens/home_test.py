"""Tests for HomeScreen."""

import pytest
from textual.app import App
from textual.widgets import Static

from forge.tui.screens.home import HomeScreen, PromptTextArea, format_recent_pipelines
from forge.tui.widgets.pipeline_list import PipelineList
from forge.tui.widgets.sanitized_text_area import strip_terminal_input_noise
from forge.tui.widgets.shortcut_bar import ShortcutBar

SAMPLE_PIPELINES = [
    {
        "id": "abc",
        "description": "Build auth system",
        "status": "complete",
        "created_at": "2026-03-10",
        "cost": 2.50,
        "total_cost_usd": 2.50,
        "task_count": 3,
        "total_tasks": 3,
        "tasks_done": 3,
        "tasks_error": 0,
        "pr_url": "https://github.com/org/repo/pull/1",
    },
    {
        "id": "def",
        "description": "Fix login bug",
        "status": "error",
        "created_at": "2026-03-09",
        "cost": 0.80,
        "total_cost_usd": 0.80,
        "task_count": 2,
        "total_tasks": 2,
        "tasks_done": 0,
        "tasks_error": 1,
        "pr_url": None,
    },
]


class HomeTestApp(App):
    def __init__(self, pipelines=None):
        super().__init__()
        self._pipelines = pipelines

    def on_mount(self) -> None:
        self.push_screen(HomeScreen(recent_pipelines=self._pipelines))


class FakeRepo:
    def __init__(self, repo_id: str, path: str = "/tmp/repo", base_branch: str = "main") -> None:
        self.id = repo_id
        self.path = path
        self.base_branch = base_branch


class WorkspaceHomeTestApp(App):
    def __init__(self, pipelines=None, repos=None):
        super().__init__()
        self._pipelines = pipelines
        self._repos = repos or []

    def on_mount(self) -> None:
        self.push_screen(
            HomeScreen(
                recent_pipelines=self._pipelines,
                repos=self._repos,
                project_dir="/tmp/workspace",
            )
        )


@pytest.mark.asyncio
async def test_home_screen_mounts():
    app = HomeTestApp()
    async with app.run_test():
        assert app.screen.query_one("ForgeLogo") is not None
        assert app.screen.query_one("PromptTextArea") is not None


def test_format_recent_pipelines():
    pipelines = [
        {
            "id": "abc",
            "description": "Build auth system",
            "status": "complete",
            "created_at": "2026-03-10",
            "cost": 2.50,
        },
        {
            "id": "def",
            "description": "Fix login bug",
            "status": "error",
            "created_at": "2026-03-09",
            "cost": 0.80,
        },
    ]
    result = format_recent_pipelines(pipelines)
    assert "Build auth system" in result
    assert "Fix login bug" in result
    assert "\u2714" in result
    assert "\u2716" in result


def test_format_recent_pipelines_empty():
    result = format_recent_pipelines([])
    assert "No recent pipelines" in result


def test_strip_terminal_input_noise_removes_mouse_packets():
    raw = "hello\x1b[<35;40;23M world <64;12;9Mdone"
    assert strip_terminal_input_noise(raw) == "hello world done"


def test_prompt_text_area_changed_strips_terminal_noise_and_preserves_cursor():
    prompt = PromptTextArea()
    prompt.load_text("Ship it<35;40;23M now")
    prompt.move_cursor((0, len("Ship it<35;40;23M now")))

    prompt.on_text_area_changed(PromptTextArea.Changed(prompt))

    assert prompt.text == "Ship it now"
    assert prompt.cursor_location == (0, len("Ship it now"))


def test_format_recent_pipelines_flattens_multiline_description():
    pipelines = [
        {
            "id": "abc",
            "description": "Build gauntlet\n\nGoal:\nCreate a first-class self-test feature",
            "status": "complete",
            "created_at": "2026-03-10",
            "cost": 2.50,
        }
    ]

    result = format_recent_pipelines(pipelines)

    assert "Build gauntlet Goal: Create a first-class" in result
    assert "Goal:\n" not in result
    assert result.count("\n") == 0


@pytest.mark.asyncio
async def test_home_screen_has_pipeline_list():
    """HomeScreen should contain a PipelineList widget."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test():
        pl = app.screen.query_one(PipelineList)
        assert pl is not None


@pytest.mark.asyncio
async def test_home_screen_pipeline_list_populated():
    """PipelineList should be populated with recent pipelines."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test():
        pl = app.screen.query_one(PipelineList)
        assert len(pl._pipelines) == 2
        assert pl._pipelines[0]["id"] == "abc"


@pytest.mark.asyncio
async def test_home_screen_tab_cycles_focus():
    """Tab should cycle through prompt → branch selectors → pipeline list."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test() as pilot:
        prompt = app.screen.query_one("PromptTextArea")
        prompt.focus()
        await pilot.pause()
        assert prompt.has_focus

        # Tab cycles through: prompt → base-branch → branch-name → pipeline-list
        # Just verify it moves away from prompt (exact order depends on widgets present)
        app.screen.action_cycle_focus()
        await pilot.pause()
        assert not prompt.has_focus  # Focus moved somewhere else


@pytest.mark.asyncio
async def test_pipeline_list_selected_posts_message():
    """Pressing Enter on PipelineList should post Selected message."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test():
        pl = app.screen.query_one(PipelineList)
        pl.focus()
        messages = []
        original_post = pl.post_message
        pl.post_message = lambda m: messages.append(m) or original_post(m)
        pl.action_select_pipeline()
        assert any(isinstance(m, PipelineList.Selected) for m in messages)


@pytest.mark.asyncio
async def test_prompt_text_area_clear_input_empties_text():
    """action_clear_input() should clear all text in PromptTextArea."""
    app = HomeTestApp()
    async with app.run_test() as pilot:
        prompt = app.screen.query_one(PromptTextArea)
        prompt.load_text("hello world")
        await pilot.pause()
        prompt.action_clear_input()
        await pilot.pause()
        assert prompt.text == ""


@pytest.mark.asyncio
async def test_prompt_text_area_clear_input_resets_cursor():
    """action_clear_input() should reset cursor position to (0, 0)."""
    app = HomeTestApp()
    async with app.run_test() as pilot:
        prompt = app.screen.query_one(PromptTextArea)
        prompt.load_text("hello world")
        await pilot.pause()
        prompt.action_clear_input()
        await pilot.pause()
        assert prompt.cursor_location == (0, 0)


@pytest.mark.asyncio
async def test_home_screen_has_shortcuts_panel():
    """HomeScreen should contain a shortcuts panel with id 'shortcuts-panel'."""
    app = HomeTestApp()
    async with app.run_test():
        panel = app.screen.query_one("#shortcuts-panel", Static)
        assert panel is not None


@pytest.mark.asyncio
async def test_shortcuts_panel_contains_all_shortcuts():
    """Shortcuts panel should list all applicable keybindings."""
    app = HomeTestApp()
    async with app.run_test():
        panel = app.screen.query_one("#shortcuts-panel", Static)
        rendered = str(panel.content)
        assert "Ctrl+S" in rendered
        assert "submit" in rendered
        assert "Ctrl+U" in rendered
        assert "clear" in rendered
        assert "Tab" in rendered
        assert "focus" in rendered
        assert "Ctrl+P" in rendered
        assert "palette" in rendered
        assert "?" in rendered
        assert "help" in rendered


@pytest.mark.asyncio
async def test_shortcuts_panel_has_color_codes():
    """Shortcuts panel should use the theme color codes."""
    app = HomeTestApp()
    async with app.run_test():
        panel = app.screen.query_one("#shortcuts-panel", Static)
        rendered = str(panel.content)
        assert "#e6edf3" in rendered  # section title color
        assert "#58a6ff" in rendered  # shortcut key color


@pytest.mark.asyncio
async def test_home_screen_no_submit_hint():
    """Old submit-hint Static should be removed."""
    app = HomeTestApp()
    async with app.run_test():
        results = app.screen.query("#submit-hint")
        # submit-hint should not exist, or if query returns empty
        assert len(results) == 0


@pytest.mark.asyncio
async def test_home_screen_has_input_row():
    """HomeScreen should have a Horizontal input-row container."""
    app = HomeTestApp()
    async with app.run_test():
        row = app.screen.query_one("#input-row")
        assert row is not None


@pytest.mark.asyncio
async def test_shortcut_label_view_for_read_only_pipeline():
    """First pipeline is complete+PR (read-only), Enter always says View."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one(ShortcutBar)
        enter_labels = [label for key, label in bar.shortcuts if key == "Enter"]
        assert enter_labels == ["View"]
        # Non-resumable: no Shift+R shown
        resume_labels = [label for key, label in bar.shortcuts if key == "Shift+R"]
        assert resume_labels == []


@pytest.mark.asyncio
async def test_shortcut_label_resume_for_resumable_pipeline():
    """After navigating to error pipeline, Shift+R Resume should appear."""
    app = HomeTestApp(pipelines=SAMPLE_PIPELINES)
    async with app.run_test() as pilot:
        pl = app.screen.query_one(PipelineList)
        pl.focus()
        await pilot.pause()
        # Navigate to second pipeline (error status = resumable)
        pl.action_cursor_down()
        await pilot.pause()
        bar = app.screen.query_one(ShortcutBar)
        # Enter always says View now
        enter_labels = [label for key, label in bar.shortcuts if key == "Enter"]
        assert enter_labels == ["View"]
        # Shift+R should appear for resumable pipelines
        resume_labels = [label for key, label in bar.shortcuts if key == "Shift+R"]
        assert resume_labels == ["Resume"]


@pytest.mark.asyncio
async def test_workspace_history_scrolls_as_soon_as_fourth_pipeline_is_selected():
    pipelines = [
        {
            "id": f"p{i}",
            "description": f"Pipeline {i}",
            "status": "complete",
            "created_at": "2026-03-10",
            "total_cost_usd": float(i),
        }
        for i in range(1, 8)
    ]
    repos = [FakeRepo("wizbridge"), FakeRepo("temp"), FakeRepo("ultron")]
    app = WorkspaceHomeTestApp(pipelines=pipelines, repos=repos)

    async with app.run_test(size=(140, 57)) as pilot:
        pl = app.screen.query_one(PipelineList)
        await pilot.pause()

        initial = pl.render()
        assert "Pipeline 1" in initial
        assert "Pipeline 3" in initial
        assert "Pipeline 4" not in initial

        for _ in range(3):
            pl.action_cursor_down()
        await pilot.pause()

        rendered = pl.render()

        assert pl._selected_index == 3
        assert pl._scroll_offset == 1
        assert "Pipeline 4" in rendered
        assert "Pipeline 1" not in rendered
