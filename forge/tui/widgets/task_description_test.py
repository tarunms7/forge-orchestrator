"""Tests for TaskDescriptionOverlay widget."""

from forge.tui.widgets.task_description import (
    TaskDescriptionOverlay,
    format_task_description,
)


# --- format_task_description ---


def test_format_basic():
    result = format_task_description("Add auth to API", [])
    assert "TASK INFO" in result
    assert "Add auth to API" in result


def test_format_with_tasks():
    tasks = [
        {
            "title": "Setup DB",
            "state": "done",
            "complexity": "low",
            "description": "Initialize database",
            "files": ["db.py"],
        },
        {
            "title": "Add endpoints",
            "state": "in_progress",
            "complexity": "medium",
            "description": "",
            "files": [],
        },
    ]
    result = format_task_description("Build API", tasks)
    assert "Task Breakdown" in result
    assert "Setup DB" in result
    assert "Add endpoints" in result
    assert "1/2 tasks complete" in result


def test_format_with_created_at():
    result = format_task_description("Task", [], created_at="2026-03-14 10:00")
    assert "2026-03-14 10:00" in result


def test_format_with_scroll():
    tasks = [
        {
            "title": f"Task {i}",
            "state": "todo",
            "complexity": "low",
            "description": f"Desc {i}",
            "files": [f"f{i}.py"],
        }
        for i in range(20)
    ]
    result = format_task_description("Big project", tasks, max_visible=5)
    assert "more lines" in result


def test_format_truncates_long_description():
    tasks = [
        {"title": "T", "state": "todo", "complexity": "low", "description": "A" * 150, "files": []}
    ]
    result = format_task_description("Task", tasks)
    assert "..." in result


def test_format_truncates_many_files():
    files = [f"file{i}.py" for i in range(10)]
    tasks = [
        {"title": "T", "state": "todo", "complexity": "low", "description": "", "files": files}
    ]
    result = format_task_description("Task", tasks)
    assert "+5 more" in result


def test_format_state_icons():
    states = ["todo", "in_progress", "done", "error", "awaiting_input"]
    for state in states:
        tasks = [{"title": "T", "state": state, "complexity": "low"}]
        result = format_task_description("Task", tasks)
        assert "T" in result  # Just ensure no crash


def test_format_multiline_description():
    result = format_task_description("Line 1\nLine 2\nLine 3", [])
    assert "Line 1" in result
    assert "Line 2" in result
    assert "Line 3" in result


# --- TaskDescriptionOverlay widget ---


def test_overlay_init():
    overlay = TaskDescriptionOverlay(
        description="Test task",
        tasks=[],
        created_at="2026-03-14",
    )
    assert overlay.description == "Test task"
    assert overlay.tasks == []
    assert overlay.scroll_offset == 0
    assert not overlay.is_open


def test_overlay_init_defaults():
    overlay = TaskDescriptionOverlay()
    assert overlay.description == ""
    assert overlay.tasks == []
    assert overlay.scroll_offset == 0


def test_overlay_open_close():
    overlay = TaskDescriptionOverlay(description="Test")
    overlay.open()
    assert overlay.is_open
    overlay.close()
    assert not overlay.is_open


def test_overlay_open_updates_content():
    overlay = TaskDescriptionOverlay(description="Old")
    overlay.open(description="New", tasks=[{"title": "T", "state": "todo"}])
    assert overlay.description == "New"
    assert len(overlay.tasks) == 1


def test_overlay_open_resets_scroll():
    overlay = TaskDescriptionOverlay(description="Test")
    overlay._scroll_offset = 5
    overlay.open()
    assert overlay.scroll_offset == 0


def test_overlay_close_resets_scroll():
    overlay = TaskDescriptionOverlay(description="Test")
    overlay.open()
    overlay._scroll_offset = 3
    overlay.close()
    assert overlay.scroll_offset == 0


def test_overlay_scroll_down():
    overlay = TaskDescriptionOverlay(
        description="Test",
        tasks=[
            {
                "title": f"T{i}",
                "state": "todo",
                "complexity": "low",
                "description": f"D{i}",
                "files": [f"f{i}.py"],
            }
            for i in range(20)
        ],
    )
    overlay._max_visible = 5
    overlay.action_scroll_down()
    assert overlay.scroll_offset == 1


def test_overlay_scroll_up():
    overlay = TaskDescriptionOverlay(description="Test")
    overlay._scroll_offset = 3
    overlay.action_scroll_up()
    assert overlay.scroll_offset == 2


def test_overlay_scroll_up_at_top():
    overlay = TaskDescriptionOverlay(description="Test")
    overlay.action_scroll_up()
    assert overlay.scroll_offset == 0


def test_overlay_render():
    overlay = TaskDescriptionOverlay(
        description="Build feature",
        tasks=[{"title": "Task 1", "state": "done", "complexity": "low"}],
        created_at="2026-03-14",
    )
    result = overlay.render()
    assert "TASK INFO" in result
    assert "Build feature" in result
    assert "Task 1" in result


def test_overlay_tasks_is_copy():
    """Tasks property should return a copy, not the internal list."""
    tasks = [{"title": "T", "state": "todo"}]
    overlay = TaskDescriptionOverlay(tasks=tasks)
    returned = overlay.tasks
    returned.append({"title": "Extra"})
    assert len(overlay.tasks) == 1


def test_overlay_importable_from_package():
    """TaskDescriptionOverlay should be importable from widgets package."""
    from forge.tui.widgets import TaskDescriptionOverlay as TDO

    assert TDO is TaskDescriptionOverlay
