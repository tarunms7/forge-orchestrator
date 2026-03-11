"""Tests for PlanApprovalScreen."""
from forge.tui.screens.plan_approval import format_plan_task, format_plan_summary, format_cost_estimate

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
