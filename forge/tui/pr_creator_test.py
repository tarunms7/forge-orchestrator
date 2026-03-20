from forge.tui.pr_creator import generate_pr_body


def test_generate_pr_body_includes_tasks():
    tasks = [{"title": "Auth", "added": 89, "removed": 4, "files": 3}]
    body = generate_pr_body(tasks=tasks, time="8m", cost=0.42, questions=[])
    assert "Auth" in body
    assert "+89/-4" in body
    assert "$0.42" in body


def test_generate_pr_body_includes_questions():
    questions = [{"question": "Which ORM?", "answer": "SQLAlchemy 2.0"}]
    body = generate_pr_body(tasks=[], time="5m", cost=0.10, questions=questions)
    assert "Which ORM?" in body
    assert "SQLAlchemy 2.0" in body


def test_pr_body_with_failed_tasks():
    body = generate_pr_body(
        tasks=[
            {"title": "Auth", "added": 100, "removed": 10, "files": 3},
            {"title": "Docs", "added": 50, "removed": 0, "files": 1},
        ],
        failed_tasks=[
            {"title": "API", "error": "timed out (5 attempts)"},
        ],
        time="12m 30s",
        cost=6.57,
        questions=[],
    )
    assert "Completed Tasks" in body
    assert "✅" in body
    assert "Failed Tasks" in body
    assert "❌" in body
    assert "API" in body
    assert "timed out" in body


def test_pr_body_with_details():
    """Tasks with description, implementation_summary, and file_list show details."""
    tasks = [{
        "title": "Add auth middleware",
        "description": "Implement JWT authentication middleware",
        "implementation_summary": "Added JWT validation to all API routes",
        "added": 120,
        "removed": 5,
        "files": 4,
        "file_list": ["forge/api/auth.py", "forge/api/middleware.py"],
    }]
    body = generate_pr_body(tasks=tasks, time="5m", cost=1.00, questions=[])
    assert "Add auth middleware" in body
    assert "+120/-5" in body
    assert "<details>" in body
    assert "JWT authentication middleware" in body
    assert "Added JWT validation" in body
    assert "`forge/api/auth.py`" in body
    assert "`forge/api/middleware.py`" in body


def test_pr_body_no_details_when_empty():
    """Tasks without description/summary/files don't show empty details block."""
    tasks = [{"title": "Quick fix", "added": 3, "removed": 1, "files": 1}]
    body = generate_pr_body(tasks=tasks, time="1m", cost=0.10, questions=[])
    assert "Quick fix" in body
    assert "<details>" not in body


def test_pr_body_zero_stats_no_stats_shown():
    """Tasks with 0 added/removed/files don't show +0/-0."""
    tasks = [{"title": "Config change", "added": 0, "removed": 0, "files": 0}]
    body = generate_pr_body(tasks=tasks, time="1m", cost=0.05, questions=[])
    assert "Config change" in body
    assert "+0/-0" not in body
