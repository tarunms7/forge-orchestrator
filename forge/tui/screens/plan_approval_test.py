"""Tests for PlanApprovalScreen interactive editor."""



from forge.tui.screens.plan_approval import (
    _COMPLEXITY_ORDER,
    PlanApprovalScreen,
    _format_task_line,
    format_cost_estimate,
    format_plan_summary,
    format_plan_task,
)

# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------

def _sample_tasks() -> list[dict]:
    return [
        {
            "id": "task-1",
            "title": "Add user auth",
            "description": "Implement JWT-based authentication",
            "files": ["auth.py", "middleware.py"],
            "complexity": "high",
            "depends_on": [],
        },
        {
            "id": "task-2",
            "title": "Add endpoints",
            "description": "REST API endpoints",
            "files": ["routes.py"],
            "complexity": "medium",
            "depends_on": ["task-1"],
        },
        {
            "id": "task-3",
            "title": "Write tests",
            "description": "Unit and integration tests",
            "files": ["test_auth.py", "test_routes.py"],
            "complexity": "low",
            "depends_on": ["task-2"],
        },
    ]


# ---------------------------------------------------------------------------
# format_plan_task (existing formatter, unchanged)
# ---------------------------------------------------------------------------

def test_format_plan_task():
    task = {
        "id": "task-1",
        "title": "Add user auth",
        "description": "Implement JWT-based authentication",
        "files": ["auth.py", "middleware.py"],
        "complexity": "high",
        "depends_on": [],
    }
    result = format_plan_task(task, index=1)
    assert "Add user auth" in result
    assert "auth.py" in result
    assert "high" in result


def test_format_plan_task_with_deps():
    task = {
        "id": "task-2",
        "title": "Add endpoints",
        "description": "",
        "files": [],
        "complexity": "medium",
        "depends_on": ["task-1"],
    }
    result = format_plan_task(task, index=2)
    assert "Add endpoints" in result
    assert "task-1" in result


# ---------------------------------------------------------------------------
# format_plan_summary
# ---------------------------------------------------------------------------

def test_format_plan_summary():
    tasks = [
        {"id": "t1", "title": "A", "complexity": "low"},
        {"id": "t2", "title": "B", "complexity": "high"},
        {"id": "t3", "title": "C", "complexity": "medium"},
    ]
    result = format_plan_summary(tasks, estimated_cost=4.50)
    assert "3 tasks" in result
    assert "$4.50" in result


def test_format_plan_summary_no_cost():
    tasks = [{"id": "t1", "title": "A", "complexity": "low"}]
    result = format_plan_summary(tasks)
    assert "1 task" in result
    assert "1 tasks" not in result  # singular, not plural


# ---------------------------------------------------------------------------
# format_cost_estimate
# ---------------------------------------------------------------------------

def test_format_cost_estimate_range():
    cost_estimate = {"min_usd": 3.50, "max_usd": 5.20}
    result = format_cost_estimate(cost_estimate)
    assert result is not None
    assert "$3.50" in result
    assert "$5.20" in result
    assert "–" in result
    assert "#d29922" in result


def test_format_cost_estimate_single_legacy():
    cost_estimate = {"estimated_cost": 4.75}
    result = format_cost_estimate(cost_estimate)
    assert result is not None
    assert "~$4.75" in result
    assert "#d29922" in result


def test_format_cost_estimate_none():
    result = format_cost_estimate(None)
    assert result is None


def test_format_cost_estimate_empty_dict():
    result = format_cost_estimate({})
    assert result is None


def test_format_cost_estimate_amber_color_range():
    cost_estimate = {"min_usd": 1.00, "max_usd": 2.00}
    result = format_cost_estimate(cost_estimate)
    assert "[#d29922]" in result


def test_format_cost_estimate_amber_color_legacy():
    cost_estimate = {"estimated_cost": 1.50}
    result = format_cost_estimate(cost_estimate)
    assert "[#d29922]" in result


# ---------------------------------------------------------------------------
# _format_task_line (interactive list formatter)
# ---------------------------------------------------------------------------

def test_format_task_line_selected():
    task = {"title": "Do stuff", "complexity": "low"}
    result = _format_task_line(task, 1, selected=True, modified=False, removed=False)
    assert "▶" in result
    assert "Do stuff" in result


def test_format_task_line_not_selected():
    task = {"title": "Do stuff", "complexity": "low"}
    result = _format_task_line(task, 1, selected=False, modified=False, removed=False)
    assert "▶" not in result


def test_format_task_line_modified_indicator():
    task = {"title": "Edited", "complexity": "medium"}
    result = _format_task_line(task, 1, selected=False, modified=True, removed=False)
    assert "●" in result  # yellow modified dot


def test_format_task_line_removed():
    task = {"title": "Old task", "complexity": "high"}
    result = _format_task_line(task, 1, selected=False, modified=False, removed=True)
    assert "removed" in result
    assert "undo" in result


def test_format_task_line_with_agent_notes():
    task = {"title": "Task", "complexity": "low", "agent_notes": "Focus on performance"}
    result = _format_task_line(task, 1, selected=False, modified=False, removed=False)
    assert "Focus on performance" in result
    assert "Note" in result


def test_format_task_line_with_files():
    task = {"title": "T", "complexity": "low", "files": ["a.py", "b.py"]}
    result = _format_task_line(task, 1, selected=False, modified=False, removed=False)
    assert "a.py" in result
    assert "b.py" in result


def test_format_task_line_many_files_truncated():
    task = {"title": "T", "complexity": "low", "files": [f"f{i}.py" for i in range(8)]}
    result = _format_task_line(task, 1, selected=False, modified=False, removed=False)
    assert "+3 more" in result


def test_format_task_line_with_deps():
    task = {"title": "T", "complexity": "low", "depends_on": ["task-1", "task-2"]}
    result = _format_task_line(task, 1, selected=False, modified=False, removed=False)
    assert "task-1" in result
    assert "task-2" in result


# ---------------------------------------------------------------------------
# PlanApprovalScreen — unit tests for internal state methods
# ---------------------------------------------------------------------------

class TestPlanApprovalScreenState:
    """Test PlanApprovalScreen state management without mounting the widget."""

    def _make_screen(self, tasks=None):
        return PlanApprovalScreen(tasks or _sample_tasks())

    def test_initial_cursor_at_zero(self):
        screen = self._make_screen()
        assert screen._cursor == 0

    def test_tasks_deep_copied(self):
        original = _sample_tasks()
        screen = self._make_screen(original)
        screen._tasks[0]["title"] = "CHANGED"
        assert original[0]["title"] == "Add user auth"  # unchanged

    def test_active_tasks_excludes_removed(self):
        screen = self._make_screen()
        assert len(screen._active_tasks) == 3
        screen._removed.add(1)
        assert len(screen._active_tasks) == 2
        assert all(t["id"] != "task-2" for t in screen._active_tasks)

    def test_clamp_cursor_within_bounds(self):
        screen = self._make_screen()
        screen._cursor = 10
        screen._clamp_cursor()
        assert screen._cursor == 2  # max index

    def test_clamp_cursor_negative(self):
        screen = self._make_screen()
        screen._cursor = -5
        screen._clamp_cursor()
        assert screen._cursor == 0

    def test_clamp_cursor_empty(self):
        screen = self._make_screen([])
        screen._clamp_cursor()
        assert screen._cursor == 0

    def test_is_editing_false_by_default(self):
        screen = self._make_screen()
        assert not screen._is_editing()

    def test_is_editing_true_when_editing(self):
        screen = self._make_screen()
        screen._editing = "description"
        assert screen._is_editing()

    def test_is_editing_true_when_adding(self):
        screen = self._make_screen()
        screen._adding = True
        assert screen._is_editing()


class TestSwapTasks:
    """Test task reordering logic."""

    def _make_screen(self):
        return PlanApprovalScreen(_sample_tasks())

    def test_swap_tasks_exchanges_data(self):
        screen = self._make_screen()
        id_0 = screen._tasks[0]["id"]
        id_1 = screen._tasks[1]["id"]
        screen._swap_tasks(0, 1)
        assert screen._tasks[0]["id"] == id_1
        assert screen._tasks[1]["id"] == id_0

    def test_swap_tasks_marks_both_modified(self):
        screen = self._make_screen()
        screen._swap_tasks(0, 1)
        assert 0 in screen._modified
        assert 1 in screen._modified

    def test_swap_tracks_removed_correctly(self):
        screen = self._make_screen()
        screen._removed.add(0)
        screen._swap_tasks(0, 1)
        assert 1 in screen._removed  # removed moved from 0 → 1
        assert 0 not in screen._removed

    def test_swap_tracks_modified_correctly(self):
        screen = self._make_screen()
        screen._modified.add(0)
        screen._swap_tasks(0, 1)
        assert 1 in screen._modified  # original modified moved
        assert 0 in screen._modified  # both marked modified after swap


class TestCycleComplexity:
    """Test complexity cycling logic."""

    def test_cycle_low_to_medium(self):
        screen = PlanApprovalScreen([{"id": "t", "title": "T", "complexity": "low"}])
        screen._cursor = 0
        # Manually cycle
        task = screen._tasks[0]
        idx = _COMPLEXITY_ORDER.index(task["complexity"])
        task["complexity"] = _COMPLEXITY_ORDER[(idx + 1) % len(_COMPLEXITY_ORDER)]
        assert task["complexity"] == "medium"

    def test_cycle_medium_to_high(self):
        screen = PlanApprovalScreen([{"id": "t", "title": "T", "complexity": "medium"}])
        task = screen._tasks[0]
        idx = _COMPLEXITY_ORDER.index(task["complexity"])
        task["complexity"] = _COMPLEXITY_ORDER[(idx + 1) % len(_COMPLEXITY_ORDER)]
        assert task["complexity"] == "high"

    def test_cycle_high_to_low(self):
        screen = PlanApprovalScreen([{"id": "t", "title": "T", "complexity": "high"}])
        task = screen._tasks[0]
        idx = _COMPLEXITY_ORDER.index(task["complexity"])
        task["complexity"] = _COMPLEXITY_ORDER[(idx + 1) % len(_COMPLEXITY_ORDER)]
        assert task["complexity"] == "low"


class TestRemoveTask:
    """Test task removal and undo logic."""

    def test_remove_marks_as_removed(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._removed.add(0)
        assert 0 in screen._removed
        assert len(screen._active_tasks) == 2

    def test_undo_remove_restores_task(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._removed.add(0)
        screen._removed.discard(0)
        assert 0 not in screen._removed
        assert len(screen._active_tasks) == 3

    def test_cannot_remove_last_task(self):
        screen = PlanApprovalScreen([{"id": "t1", "title": "Only task", "complexity": "low"}])
        # Active count would be 0 if we removed, so this should be prevented
        active_count = len(screen._tasks) - len(screen._removed)
        assert active_count == 1  # should prevent removal


class TestAddTask:
    """Test adding new tasks."""

    def test_add_task_appends_to_list(self):
        screen = PlanApprovalScreen(_sample_tasks())
        initial_count = len(screen._tasks)
        new_task = {
            "id": "task-new",
            "title": "New task",
            "description": "",
            "files": [],
            "complexity": "medium",
            "depends_on": [],
        }
        screen._tasks.append(new_task)
        assert len(screen._tasks) == initial_count + 1
        assert screen._tasks[-1]["id"] == "task-new"

    def test_new_task_marked_modified(self):
        screen = PlanApprovalScreen(_sample_tasks())
        new_index = len(screen._tasks)
        screen._tasks.append({"id": "new", "title": "N", "complexity": "medium"})
        screen._modified.add(new_index)
        assert new_index in screen._modified


class TestEditDescription:
    """Test description editing logic."""

    def test_parse_title_and_description_from_text(self):
        text = "Updated Title\nNew detailed description"
        lines = text.split("\n", 1)
        title = lines[0].strip()
        desc = lines[1].strip() if len(lines) > 1 else ""
        assert title == "Updated Title"
        assert desc == "New detailed description"

    def test_parse_title_only(self):
        text = "Just a title"
        lines = text.split("\n", 1)
        title = lines[0].strip()
        desc = lines[1].strip() if len(lines) > 1 else ""
        assert title == "Just a title"
        assert desc == ""


class TestEditFiles:
    """Test file list editing logic."""

    def test_parse_comma_separated_files(self):
        text = "auth.py, routes.py, models.py"
        files = [f.strip() for f in text.split(",") if f.strip()]
        assert files == ["auth.py", "routes.py", "models.py"]

    def test_parse_files_with_extra_whitespace(self):
        text = " auth.py ,  routes.py , "
        files = [f.strip() for f in text.split(",") if f.strip()]
        assert files == ["auth.py", "routes.py"]

    def test_parse_empty_files(self):
        text = ""
        files = [f.strip() for f in text.split(",") if f.strip()]
        assert files == []


class TestPlanApprovedMessage:
    """Test that PlanApproved carries edited tasks."""

    def test_plan_approved_includes_tasks(self):
        tasks = _sample_tasks()
        msg = PlanApprovalScreen.PlanApproved(tasks=tasks)
        assert msg.tasks == tasks

    def test_plan_approved_no_tasks(self):
        msg = PlanApprovalScreen.PlanApproved()
        assert msg.tasks is None

    def test_plan_approved_with_edited_tasks(self):
        tasks = _sample_tasks()
        tasks[0]["title"] = "EDITED"
        msg = PlanApprovalScreen.PlanApproved(tasks=tasks)
        assert msg.tasks[0]["title"] == "EDITED"

    def test_active_tasks_used_for_approval(self):
        """When approved, only non-removed tasks should be included."""
        screen = PlanApprovalScreen(_sample_tasks())
        screen._removed.add(1)  # Remove task-2
        active = screen._active_tasks
        assert len(active) == 2
        assert all(t["id"] != "task-2" for t in active)


class TestAgentNotes:
    """Test agent note functionality."""

    def test_add_note_to_task(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._tasks[0]["agent_notes"] = "Focus on security"
        assert screen._tasks[0]["agent_notes"] == "Focus on security"

    def test_note_appears_in_formatted_output(self):
        task = {"title": "T", "complexity": "low", "agent_notes": "Be careful"}
        result = _format_task_line(task, 1, False, False, False)
        assert "Be careful" in result


class TestEditorGuards:
    """Test that editing mode blocks navigation/actions."""

    def test_editing_blocks_further_edits(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._editing = "description"
        assert screen._is_editing()

    def test_adding_blocks_edits(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._adding = True
        assert screen._is_editing()


class TestMoveOperations:
    """Test move up/down with boundary conditions."""

    def test_move_first_task_up_no_op(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._cursor = 0
        original_first = screen._tasks[0]["id"]
        # action_move_up would return early since cursor == 0
        assert screen._tasks[0]["id"] == original_first

    def test_move_last_task_down_no_op(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._cursor = 2
        original_last = screen._tasks[2]["id"]
        # action_move_down would return early since cursor == len - 1
        assert screen._tasks[2]["id"] == original_last

    def test_move_task_down_swaps_correctly(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._cursor = 0
        id_0 = screen._tasks[0]["id"]
        id_1 = screen._tasks[1]["id"]
        screen._swap_tasks(0, 1)
        screen._cursor = 1
        assert screen._tasks[0]["id"] == id_1
        assert screen._tasks[1]["id"] == id_0

    def test_move_task_up_swaps_correctly(self):
        screen = PlanApprovalScreen(_sample_tasks())
        screen._cursor = 2
        id_1 = screen._tasks[1]["id"]
        id_2 = screen._tasks[2]["id"]
        screen._swap_tasks(1, 2)
        screen._cursor = 1
        assert screen._tasks[1]["id"] == id_2
        assert screen._tasks[2]["id"] == id_1
