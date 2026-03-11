from forge.tui.screens.final_approval import format_summary_stats, format_task_table


def test_format_summary_stats():
    stats = {"added": 342, "removed": 28, "files": 12, "elapsed": "8m 23s", "cost": 0.42, "questions": 2}
    result = format_summary_stats(stats)
    assert "+342" in result
    assert "$0.42" in result


def test_format_task_table():
    tasks = [
        {"title": "JWT middleware", "added": 89, "removed": 4, "tests_passed": 14, "tests_total": 14, "review": "passed"},
    ]
    result = format_task_table(tasks)
    assert "JWT middleware" in result
    assert "14/14" in result
