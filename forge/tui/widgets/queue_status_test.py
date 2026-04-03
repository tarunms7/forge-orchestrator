"""Tests for QueueStatus formatting."""

from forge.tui.widgets.queue_status import format_queue_status


def test_format_queue_status_empty():
    rendered = format_queue_status(None)
    assert "Dispatch map warming up" in rendered


def test_format_queue_status_shows_launching_tasks():
    rendered = format_queue_status(
        {
            "ready_count": 3,
            "active_count": 2,
            "blocked_count": 1,
            "human_wait_count": 0,
            "critical_path_length": 5,
            "dispatching_now": ["auth-api", "auth-ui"],
            "tasks": {},
        }
    )

    assert "ready 3" in rendered
    assert "live 2" in rendered
    assert "cp 5" in rendered
    assert "launching auth-api, auth-ui" in rendered


def test_format_queue_status_falls_back_to_blocked_reason():
    rendered = format_queue_status(
        {
            "ready_count": 0,
            "active_count": 0,
            "blocked_count": 1,
            "human_wait_count": 0,
            "critical_path_length": 2,
            "blocked_task_ids": ["web"],
            "tasks": {"web": {"reason": "Blocked by failed dependency: api"}},
        }
    )

    assert "blocked 1" in rendered
    assert "blocked by api" in rendered
