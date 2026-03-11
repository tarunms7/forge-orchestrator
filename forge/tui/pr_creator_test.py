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
