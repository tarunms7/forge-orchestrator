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
