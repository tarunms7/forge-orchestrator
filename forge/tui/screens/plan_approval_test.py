"""Tests for PlanApprovalScreen."""
from forge.tui.screens.plan_approval import format_plan_task, format_plan_summary

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
    assert "1 tasks" in result
