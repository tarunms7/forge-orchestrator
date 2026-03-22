from forge.tui.widgets.chat_thread import format_question_card, format_work_log


def test_format_work_log():
    lines = ["📖 Reading auth.py", "🔎 Searching for middleware"]
    result = format_work_log(lines)
    assert "auth.py" in result
    assert "middleware" in result


def test_format_question_card():
    question = {"question": "Which ORM?", "suggestions": ["A", "B"], "context": "Found 2 patterns"}
    result = format_question_card(question)
    assert "Which ORM?" in result
    assert "Found 2 patterns" in result
