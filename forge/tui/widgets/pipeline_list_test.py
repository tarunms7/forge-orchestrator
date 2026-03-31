"""Tests for PipelineList widget."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical

from forge.tui.widgets.pipeline_list import (
    PipelineList,
    _progress_text,
    is_pipeline_resumable,
)

SAMPLE_PIPELINES = [
    {
        "id": "p1",
        "description": "Build auth system",
        "status": "complete",
        "created_at": "2026-03-10T12:00:00",
        "task_count": 5,
        "total_cost_usd": 2.50,
        "project_dir": "/Users/foo/my-project",
    },
    {
        "id": "p2",
        "description": "Fix login bug",
        "status": "error",
        "created_at": "2026-03-09T10:00:00",
        "task_count": 3,
        "total_cost_usd": 0.80,
        "project_dir": "/home/bar/another-repo",
    },
    {
        "id": "p3",
        "description": "Add caching layer",
        "status": "in_progress",
        "created_at": "2026-03-11T08:00:00",
        "task_count": 4,
        "total_cost_usd": 1.20,
        "project_dir": "",
    },
]


class TestPipelineListWidget:
    """Tests for PipelineList widget."""

    def test_init_empty(self):
        pl = PipelineList()
        assert pl._pipelines == []
        assert pl._selected_index == 0

    def test_update_pipelines(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        assert len(pl._pipelines) == 3

    def test_selected_pipeline(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        assert pl.selected_pipeline is not None
        assert pl.selected_pipeline["id"] == "p1"

    def test_selected_pipeline_empty(self):
        pl = PipelineList()
        assert pl.selected_pipeline is None

    def test_cursor_down(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        pl.action_cursor_down()
        assert pl._selected_index == 1
        assert pl.selected_pipeline["id"] == "p2"

    def test_cursor_down_at_end(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        pl._selected_index = 2
        pl.action_cursor_down()
        assert pl._selected_index == 2  # Stays at end

    def test_cursor_up(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        pl._selected_index = 2
        pl.action_cursor_up()
        assert pl._selected_index == 1
        assert pl.selected_pipeline["id"] == "p2"

    def test_cursor_up_at_start(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        pl.action_cursor_up()
        assert pl._selected_index == 0  # Stays at start

    def test_render_empty(self):
        pl = PipelineList()
        rendered = pl.render()
        assert "No recent pipelines" in rendered

    def test_render_with_pipelines(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        rendered = pl.render()
        assert "Build auth system" in rendered
        assert "Fix login bug" in rendered
        assert "Add caching layer" in rendered

    def test_render_shows_cost(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        rendered = pl.render()
        assert "$2.50" in rendered
        assert "$0.80" in rendered

    def test_select_pipeline_posts_message(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        messages = []
        pl.post_message = lambda m: messages.append(m)
        pl.action_select_pipeline()
        assert len(messages) == 1
        assert isinstance(messages[0], PipelineList.Selected)
        assert messages[0].pipeline_id == "p1"

    def test_select_pipeline_empty(self):
        pl = PipelineList()
        messages = []
        pl.post_message = lambda m: messages.append(m)
        pl.action_select_pipeline()
        assert len(messages) == 0

    def test_navigate_then_select(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        pl.action_cursor_down()
        messages = []
        pl.post_message = lambda m: messages.append(m)
        pl.action_select_pipeline()
        assert messages[0].pipeline_id == "p2"

    def test_update_clamps_index(self):
        pl = PipelineList()
        pl._selected_index = 10
        pl.update_pipelines(SAMPLE_PIPELINES)
        assert pl._selected_index == 2  # Clamped to last

    def test_status_icons_used(self):
        """Pipeline statuses should use status icons from task_list."""
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "test",
                    "status": "complete",
                    "created_at": "",
                    "task_count": 0,
                    "total_cost_usd": 0,
                },
            ]
        )
        rendered = pl.render()
        assert "✔" in rendered

    def test_cost_fallback_to_cost_key(self):
        """Should fall back to 'cost' key if 'total_cost_usd' not present."""
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "test",
                    "status": "complete",
                    "created_at": "",
                    "task_count": 0,
                    "cost": 1.23,
                },
            ]
        )
        rendered = pl.render()
        assert "$1.23" in rendered

    def test_render_shows_project_folder(self):
        """Folder basename from project_dir should appear as a dim tag."""
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "Test task",
                    "status": "complete",
                    "created_at": "2026-03-10T12:00:00",
                    "task_count": 1,
                    "total_cost_usd": 0.0,
                    "project_dir": "/Users/foo/my-project",
                }
            ]
        )
        rendered = pl.render()
        assert "my-project" in rendered

    def test_render_no_project_tag_when_empty(self):
        """No project tag should appear when project_dir is empty."""
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "Test task",
                    "status": "complete",
                    "created_at": "2026-03-10T12:00:00",
                    "task_count": 1,
                    "total_cost_usd": 0.0,
                    "project_dir": "",
                }
            ]
        )
        rendered = pl.render()
        # Should render description and cost but no extraneous folder name
        assert "Test task" in rendered
        assert "$0.00" in rendered


class TestResumeEligibility:
    """Tests for is_pipeline_resumable()."""

    def test_planning_is_resumable(self):
        assert is_pipeline_resumable({"status": "planning"}) is True

    def test_planned_is_resumable(self):
        assert is_pipeline_resumable({"status": "planned"}) is True

    def test_contracts_is_resumable(self):
        assert is_pipeline_resumable({"status": "contracts"}) is True

    def test_countdown_is_resumable(self):
        assert is_pipeline_resumable({"status": "countdown"}) is True

    def test_interrupted_is_resumable(self):
        assert is_pipeline_resumable({"status": "interrupted"}) is True

    def test_executing_is_resumable(self):
        assert is_pipeline_resumable({"status": "executing"}) is True

    def test_partial_success_is_resumable(self):
        assert is_pipeline_resumable({"status": "partial_success"}) is True

    def test_error_is_resumable(self):
        assert is_pipeline_resumable({"status": "error"}) is True

    def test_retrying_is_resumable(self):
        assert is_pipeline_resumable({"status": "retrying"}) is True

    def test_complete_with_pr_is_read_only(self):
        assert (
            is_pipeline_resumable(
                {"status": "complete", "pr_url": "https://github.com/org/repo/pull/1"}
            )
            is False
        )

    def test_complete_without_pr_is_resumable(self):
        assert is_pipeline_resumable({"status": "complete", "pr_url": None}) is True

    def test_complete_empty_pr_is_resumable(self):
        assert is_pipeline_resumable({"status": "complete"}) is True

    def test_cancelled_is_read_only(self):
        assert is_pipeline_resumable({"status": "cancelled"}) is False

    def test_unknown_status_is_read_only(self):
        assert is_pipeline_resumable({"status": "unknown"}) is False


class TestProgressText:
    """Tests for _progress_text()."""

    def test_executing_shows_task_count(self):
        p = {"status": "executing", "total_tasks": 5, "tasks_done": 2}
        assert _progress_text(p) == "2/5 tasks done"

    def test_interrupted_shows_task_count(self):
        p = {"status": "interrupted", "total_tasks": 3, "tasks_done": 1}
        assert _progress_text(p) == "1/3 tasks done"

    def test_partial_success_shows_task_count(self):
        p = {"status": "partial_success", "total_tasks": 4, "tasks_done": 3}
        assert _progress_text(p) == "3/4 tasks done"

    def test_retrying_shows_task_count(self):
        p = {"status": "retrying", "total_tasks": 6, "tasks_done": 4}
        assert _progress_text(p) == "4/6 tasks done"

    def test_complete_with_pr(self):
        p = {"status": "complete", "pr_url": "https://github.com/org/repo/pull/42"}
        assert _progress_text(p) == "✓ PR created"

    def test_complete_without_pr(self):
        p = {"status": "complete", "pr_url": None}
        assert _progress_text(p) == "✓ Done — no PR yet"

    def test_planning(self):
        assert _progress_text({"status": "planning"}) == "Planning…"

    def test_planned(self):
        assert _progress_text({"status": "planned"}) == "Plan ready"

    def test_contracts(self):
        assert _progress_text({"status": "contracts"}) == "Preparing…"

    def test_countdown(self):
        assert _progress_text({"status": "countdown"}) == "Preparing…"

    def test_error(self):
        assert _progress_text({"status": "error"}) == "✗ Failed"

    def test_cancelled(self):
        assert _progress_text({"status": "cancelled"}) == "Cancelled"

    def test_unknown_empty(self):
        assert _progress_text({"status": "unknown"}) == ""


class TestProgressRendering:
    """Tests that progress text and resume indicators appear in rendered output."""

    def test_resume_indicator_resumable(self):
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "t",
                    "status": "interrupted",
                    "created_at": "",
                    "total_cost_usd": 0,
                }
            ]
        )
        rendered = pl.render()
        assert "▶" in rendered

    def test_resume_indicator_read_only(self):
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "t",
                    "status": "cancelled",
                    "created_at": "",
                    "total_cost_usd": 0,
                }
            ]
        )
        rendered = pl.render()
        assert "●" in rendered

    def test_progress_text_in_render(self):
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "t",
                    "status": "executing",
                    "created_at": "",
                    "total_cost_usd": 0,
                    "total_tasks": 5,
                    "tasks_done": 3,
                }
            ]
        )
        rendered = pl.render()
        assert "3/5 tasks done" in rendered

    def test_error_progress_in_render(self):
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "t",
                    "status": "error",
                    "created_at": "",
                    "total_cost_usd": 0,
                }
            ]
        )
        rendered = pl.render()
        assert "✗ Failed" in rendered

    def test_graceful_fallback_missing_fields(self):
        """Should not crash when enriched fields are missing."""
        pl = PipelineList()
        pl.update_pipelines(
            [
                {
                    "id": "x",
                    "description": "t",
                    "status": "executing",
                    "created_at": "",
                    "total_cost_usd": 0,
                }
            ]
        )
        rendered = pl.render()
        assert "0/0 tasks done" in rendered

    def test_cursor_move_posts_message(self):
        pl = PipelineList()
        pl.update_pipelines(SAMPLE_PIPELINES)
        messages = []
        pl.post_message = lambda m: messages.append(m)
        pl.action_cursor_down()
        cursor_msgs = [m for m in messages if isinstance(m, PipelineList.CursorMoved)]
        assert len(cursor_msgs) == 1
        assert cursor_msgs[0].pipeline["id"] == "p2"


class TestViewportScrolling:
    """Tests that the selected pipeline remains visible in constrained layouts."""

    def _make_pipelines(self, count: int) -> list[dict]:
        return [
            {
                "id": f"p{i}",
                "description": f"Pipeline {i}",
                "status": "complete",
                "created_at": "2026-03-10T12:00:00",
                "total_cost_usd": float(i),
            }
            for i in range(1, count + 1)
        ]

    def test_render_limits_to_visible_window(self):
        pl = PipelineList()
        pl._visible_pipeline_count = lambda: 2
        pl.update_pipelines(self._make_pipelines(5))

        rendered = pl.render()

        assert "Pipeline 1" in rendered
        assert "Pipeline 2" in rendered
        assert "Pipeline 3" not in rendered

    def test_cursor_down_scrolls_window_to_keep_selection_visible(self):
        pl = PipelineList()
        pl._visible_pipeline_count = lambda: 2
        pl.update_pipelines(self._make_pipelines(5))

        for _ in range(3):
            pl.action_cursor_down()

        rendered = pl.render()

        assert pl._selected_index == 3
        assert pl._scroll_offset == 2
        assert "Pipeline 3" in rendered
        assert "Pipeline 4" in rendered
        assert "Pipeline 2" not in rendered

    def test_cursor_up_scrolls_window_back_when_selection_moves_above_view(self):
        pl = PipelineList()
        pl._visible_pipeline_count = lambda: 2
        pl.update_pipelines(self._make_pipelines(5))
        pl._selected_index = 4
        pl._scroll_offset = 3

        pl.action_cursor_up()
        pl.action_cursor_up()

        rendered = pl.render()

        assert pl._selected_index == 2
        assert pl._scroll_offset == 2
        assert "Pipeline 3" in rendered
        assert "Pipeline 4" in rendered
        assert "Pipeline 5" not in rendered


class PipelineListViewportApp(App[None]):
    """Mount PipelineList in a constrained host to exercise real viewport sizing."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #host {
        width: 100;
        overflow: hidden;
    }
    PipelineList {
        width: 100%;
        height: 1fr;
    }
    """

    def __init__(self, host_height: int) -> None:
        super().__init__()
        self._host_height = host_height

    def compose(self) -> ComposeResult:
        with Vertical(id="host"):
            yield PipelineList()

    def on_mount(self) -> None:
        self.query_one("#host", Vertical).styles.height = self._host_height


@pytest.mark.asyncio
async def test_cursor_down_respects_visible_screen_slice():
    app = PipelineListViewportApp(host_height=12)

    async with app.run_test(size=(120, 9)) as pilot:
        pl = app.query_one(PipelineList)
        pl.styles.margin = (3, 0, 0, 0)
        pl.update_pipelines(TestViewportScrolling()._make_pipelines(6))
        await pilot.pause()

        rendered = pl.render()
        assert "Pipeline 1" in rendered
        assert "Pipeline 3" in rendered
        assert "Pipeline 4" not in rendered

        for _ in range(3):
            pl.action_cursor_down()
        await pilot.pause()

        rendered = pl.render()

        assert pl._selected_index == 3
        assert pl._scroll_offset == 1
        assert "Pipeline 4" in rendered
        assert "Pipeline 1" not in rendered


@pytest.mark.asyncio
async def test_render_uses_available_width_for_long_descriptions():
    app = PipelineListViewportApp(host_height=12)

    async with app.run_test(size=(120, 12)) as pilot:
        pl = app.query_one(PipelineList)
        pl.update_pipelines(
            [
                {
                    "id": "p1",
                    "description": "Audit the code, every small detail, find out the issues everywhere",
                    "status": "complete",
                    "created_at": "2026-03-10T12:00:00",
                    "total_cost_usd": 1.0,
                }
            ]
        )
        await pilot.pause()

        rendered = pl.render()

        assert "issues everywhere" in rendered


def test_render_flattens_multiline_descriptions():
    pl = PipelineList()
    pl.update_pipelines(
        [
            {
                "id": "p1",
                "description": 'Build a v1 of "Forge Gauntlet"\n\nGoal:\nCreate a first-class self-test feature',
                "status": "complete",
                "created_at": "2026-03-31T12:00:00",
                "total_cost_usd": 8.80,
            }
        ]
    )

    rendered = pl.render()

    assert len(rendered.splitlines()) == 2
    assert 'Build a v1 of "Forge Gauntlet" Goal:' in rendered
    assert "\nGoal:" not in rendered
