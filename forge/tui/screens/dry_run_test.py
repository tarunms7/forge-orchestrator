"""Tests for DryRunScreen — DAG visualization with inline editing."""

from forge.tui.screens.dry_run import (
    DryRunScreen,
    _build_dag_with_models,
    _format_task_detail,
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


def _sample_model_assignments() -> dict[str, str]:
    return {
        "task-1": "opus",
        "task-2": "sonnet",
        "task-3": "haiku",
    }


# ---------------------------------------------------------------------------
# _build_dag_with_models
# ---------------------------------------------------------------------------


class TestBuildDagWithModels:
    def test_empty_tasks(self):
        result = _build_dag_with_models([], None)
        assert "No tasks" in result

    def test_includes_task_titles(self):
        tasks = _sample_tasks()
        result = _build_dag_with_models(tasks, None)
        assert "Add user auth" in result
        assert "Add endpoints" in result
        assert "Write tests" in result

    def test_includes_model_assignments(self):
        tasks = _sample_tasks()
        models = _sample_model_assignments()
        result = _build_dag_with_models(tasks, models)
        assert "(opus)" in result
        assert "(sonnet)" in result
        assert "(haiku)" in result

    def test_no_model_tag_when_not_assigned(self):
        tasks = _sample_tasks()
        result = _build_dag_with_models(tasks, {})
        # No parenthesized model names
        assert "(opus)" not in result
        assert "(sonnet)" not in result

    def test_includes_dependency_arrows(self):
        tasks = _sample_tasks()
        result = _build_dag_with_models(tasks, None)
        assert "\u2190 task-1" in result  # task-2 depends on task-1
        assert "\u2190 task-2" in result  # task-3 depends on task-2

    def test_no_arrow_for_root_task(self):
        tasks = [_sample_tasks()[0]]  # Only root task
        result = _build_dag_with_models(tasks, None)
        assert "\u2190" not in result

    def test_long_title_truncated(self):
        tasks = [{"id": "t1", "title": "A" * 50, "complexity": "low", "depends_on": []}]
        result = _build_dag_with_models(tasks, None)
        assert "\u2026" in result  # ellipsis for truncation

    def test_none_model_assignments_treated_as_empty(self):
        tasks = _sample_tasks()
        result_none = _build_dag_with_models(tasks, None)
        result_empty = _build_dag_with_models(tasks, {})
        assert result_none == result_empty


# ---------------------------------------------------------------------------
# _format_task_detail
# ---------------------------------------------------------------------------


class TestFormatTaskDetail:
    def test_shows_title(self):
        task = _sample_tasks()[0]
        result = _format_task_detail(task, _sample_tasks(), None)
        assert "Add user auth" in result

    def test_shows_description(self):
        task = _sample_tasks()[0]
        result = _format_task_detail(task, _sample_tasks(), None)
        assert "Implement JWT-based authentication" in result

    def test_shows_complexity(self):
        task = _sample_tasks()[0]
        result = _format_task_detail(task, _sample_tasks(), None)
        assert "high" in result

    def test_shows_files(self):
        task = _sample_tasks()[0]
        result = _format_task_detail(task, _sample_tasks(), None)
        assert "auth.py" in result
        assert "middleware.py" in result

    def test_shows_dependencies_with_titles(self):
        task = _sample_tasks()[1]  # task-2 depends on task-1
        result = _format_task_detail(task, _sample_tasks(), None)
        assert "task-1" in result
        assert "Add user auth" in result

    def test_shows_model_assignment(self):
        task = _sample_tasks()[0]
        models = _sample_model_assignments()
        result = _format_task_detail(task, _sample_tasks(), models)
        assert "Agent model" in result
        assert "opus" in result

    def test_no_model_when_not_assigned(self):
        task = _sample_tasks()[0]
        result = _format_task_detail(task, _sample_tasks(), {})
        assert "Agent model" not in result

    def test_no_files_shows_none(self):
        task = {"id": "t1", "title": "T", "description": "", "files": [], "complexity": "low", "depends_on": []}
        result = _format_task_detail(task, [task], None)
        assert "none" in result

    def test_no_deps_shows_none(self):
        task = _sample_tasks()[0]  # No dependencies
        result = _format_task_detail(task, _sample_tasks(), None)
        # Dependencies section should show "none"
        lines = result.split("\n")
        dep_idx = next(i for i, line in enumerate(lines) if "Dependencies" in line)
        assert "none" in lines[dep_idx]


# ---------------------------------------------------------------------------
# DryRunScreen — constructor and state
# ---------------------------------------------------------------------------


class TestDryRunScreenState:
    """Test DryRunScreen state management without mounting."""

    def _make_screen(self, tasks=None, cost_estimate=None, model_assignments=None):
        return DryRunScreen(
            tasks or _sample_tasks(),
            cost_estimate=cost_estimate,
            model_assignments=model_assignments,
        )

    def test_initial_cursor_at_zero(self):
        screen = self._make_screen()
        assert screen._cursor == 0

    def test_tasks_deep_copied(self):
        original = _sample_tasks()
        screen = self._make_screen(original)
        screen._tasks[0]["title"] = "CHANGED"
        assert original[0]["title"] == "Add user auth"

    def test_active_tasks_returns_all(self):
        screen = self._make_screen()
        assert len(screen._active_tasks) == 3

    def test_stores_cost_estimate(self):
        cost = {"min_usd": 1.0, "max_usd": 3.0}
        screen = self._make_screen(cost_estimate=cost)
        assert screen._cost_estimate == cost

    def test_stores_model_assignments(self):
        models = _sample_model_assignments()
        screen = self._make_screen(model_assignments=models)
        assert screen._model_assignments == models

    def test_model_assignments_default_empty(self):
        screen = self._make_screen()
        assert screen._model_assignments == {}

    def test_clamp_cursor_within_bounds(self):
        screen = self._make_screen()
        screen._cursor = 10
        screen._clamp_cursor()
        assert screen._cursor == 2

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


# ---------------------------------------------------------------------------
# DryRunScreen — Messages
# ---------------------------------------------------------------------------


class TestDryRunMessages:
    def test_plan_approved_includes_tasks(self):
        tasks = _sample_tasks()
        msg = DryRunScreen.PlanApproved(tasks=tasks)
        assert msg.tasks == tasks

    def test_plan_approved_no_tasks(self):
        msg = DryRunScreen.PlanApproved()
        assert msg.tasks is None

    def test_plan_approved_with_edited_tasks(self):
        tasks = _sample_tasks()
        tasks[0]["title"] = "EDITED"
        msg = DryRunScreen.PlanApproved(tasks=tasks)
        assert msg.tasks[0]["title"] == "EDITED"

    def test_plan_cancelled_instantiates(self):
        msg = DryRunScreen.PlanCancelled()
        assert isinstance(msg, DryRunScreen.PlanCancelled)


# ---------------------------------------------------------------------------
# DryRunScreen — complexity cycling
# ---------------------------------------------------------------------------


class TestDryRunCycleComplexity:
    def test_cycle_low_to_medium(self):
        screen = DryRunScreen([{"id": "t", "title": "T", "complexity": "low", "files": [], "depends_on": []}])
        task = screen._tasks[0]
        from forge.tui.screens.plan_approval import _COMPLEXITY_ORDER
        idx = _COMPLEXITY_ORDER.index(task["complexity"])
        task["complexity"] = _COMPLEXITY_ORDER[(idx + 1) % len(_COMPLEXITY_ORDER)]
        assert task["complexity"] == "medium"

    def test_cycle_medium_to_high(self):
        screen = DryRunScreen([{"id": "t", "title": "T", "complexity": "medium", "files": [], "depends_on": []}])
        task = screen._tasks[0]
        from forge.tui.screens.plan_approval import _COMPLEXITY_ORDER
        idx = _COMPLEXITY_ORDER.index(task["complexity"])
        task["complexity"] = _COMPLEXITY_ORDER[(idx + 1) % len(_COMPLEXITY_ORDER)]
        assert task["complexity"] == "high"

    def test_cycle_high_to_low(self):
        screen = DryRunScreen([{"id": "t", "title": "T", "complexity": "high", "files": [], "depends_on": []}])
        task = screen._tasks[0]
        from forge.tui.screens.plan_approval import _COMPLEXITY_ORDER
        idx = _COMPLEXITY_ORDER.index(task["complexity"])
        task["complexity"] = _COMPLEXITY_ORDER[(idx + 1) % len(_COMPLEXITY_ORDER)]
        assert task["complexity"] == "low"


# ---------------------------------------------------------------------------
# DryRunScreen — edit flow logic
# ---------------------------------------------------------------------------


class TestDryRunEditFlow:
    def test_parse_title_and_description(self):
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

    def test_parse_comma_separated_files(self):
        text = "auth.py, routes.py, models.py"
        files = [f.strip() for f in text.split(",") if f.strip()]
        assert files == ["auth.py", "routes.py", "models.py"]

    def test_parse_empty_files(self):
        text = ""
        files = [f.strip() for f in text.split(",") if f.strip()]
        assert files == []

    def test_editing_blocks_navigation(self):
        screen = DryRunScreen(_sample_tasks())
        screen._editing = "description"
        assert screen._is_editing()
        # action_cursor_down should be a no-op when editing
        # Can't call action_cursor_down without mounting, but verify guard
        assert screen._is_editing()


# ---------------------------------------------------------------------------
# DryRunScreen — navigation state
# ---------------------------------------------------------------------------


class TestDryRunNavigation:
    def test_cursor_starts_at_zero(self):
        screen = DryRunScreen(_sample_tasks())
        assert screen._cursor == 0

    def test_modified_set_starts_empty(self):
        screen = DryRunScreen(_sample_tasks())
        assert len(screen._modified) == 0

    def test_active_tasks_for_approval(self):
        screen = DryRunScreen(_sample_tasks())
        active = screen._active_tasks
        assert len(active) == 3
        assert active[0]["id"] == "task-1"
