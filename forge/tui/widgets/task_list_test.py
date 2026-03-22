"""Tests for TaskList widget."""

from forge.tui.widgets.task_list import STATE_ICONS, format_task_line


def test_state_icons_all_states():
    expected = [
        "todo",
        "in_progress",
        "in_review",
        "awaiting_approval",
        "merging",
        "done",
        "cancelled",
        "error",
    ]
    for state in expected:
        assert state in STATE_ICONS, f"Missing icon for {state}"


def test_format_task_line_todo():
    task = {"id": "t1", "title": "Setup database", "state": "todo", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert "Setup database" in line
    assert STATE_ICONS["todo"] in line


def test_format_task_line_selected():
    task = {"id": "t1", "title": "Setup database", "state": "todo", "complexity": "low"}
    line = format_task_line(task, selected=True)
    assert "Setup database" in line
    assert "1f2937" in line  # highlight background color
    assert "►" not in line  # no more arrow indicator


def test_format_task_line_done():
    task = {"id": "t1", "title": "Setup database", "state": "done", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert STATE_ICONS["done"] in line


def test_format_task_line_error():
    task = {"id": "t1", "title": "Setup database", "state": "error", "complexity": "low"}
    line = format_task_line(task, selected=False)
    assert STATE_ICONS["error"] in line


def test_format_task_line_selected_renders_without_markup_error():
    """Selected line markup must be valid Rich markup (no mismatched tags)."""
    from io import StringIO

    from rich.console import Console

    task = {"id": "t1", "title": "Setup database", "state": "in_progress", "complexity": "low"}
    line = format_task_line(task, selected=True)
    console = Console(file=StringIO(), force_terminal=True)
    # This will raise MarkupError if tags are broken
    console.print(line)


# ── File count indicator tests ──────────────────────────────────────────


def test_format_task_line_files_changed_shows_count():
    """Tasks with files_changed should display a dim file count."""
    task = {"id": "t1", "title": "Auth", "state": "done", "files_changed": ["a.py", "b.py"]}
    line = format_task_line(task, selected=False)
    assert "2 files" in line
    assert "#8b949e" in line  # dim color


def test_format_task_line_no_files_changed_no_count():
    """Tasks without files_changed should not display file count."""
    task = {"id": "t1", "title": "Auth", "state": "done"}
    line = format_task_line(task, selected=False)
    assert "files" not in line


def test_format_task_line_empty_files_changed_no_count():
    """Tasks with empty files_changed list should not display file count."""
    task = {"id": "t1", "title": "Auth", "state": "done", "files_changed": []}
    line = format_task_line(task, selected=False)
    assert "files" not in line


def test_format_task_line_files_changed_all_states():
    """File count should appear for all task states, not just error."""
    for state in ["todo", "in_progress", "done", "error"]:
        task = {"id": "t1", "title": "Task", "state": state, "files_changed": ["x.py"]}
        line = format_task_line(task, selected=False)
        assert "1 files" in line, f"File count missing for state={state}"


# ── Error badge tests ───────────────────────────────────────────────────


def test_format_task_line_error_badge():
    """Error-state tasks should have a ⚠ badge."""
    task = {"id": "t1", "title": "Broken task", "state": "error", "files_changed": ["a.py"]}
    line = format_task_line(task, selected=False)
    assert "⚠" in line
    assert "1 files" in line


def test_format_task_line_error_no_files_still_has_badge():
    """Error-state tasks should have ⚠ badge even without files_changed."""
    task = {"id": "t1", "title": "Broken task", "state": "error"}
    line = format_task_line(task, selected=False)
    assert "⚠" in line


def test_format_task_line_non_error_no_badge():
    """Non-error tasks should NOT have the ⚠ badge."""
    task = {"id": "t1", "title": "Good task", "state": "done", "files_changed": ["a.py"]}
    line = format_task_line(task, selected=False)
    assert "⚠" not in line


# ── Title truncation tests ──────────────────────────────────────────────


def test_format_task_line_truncates_long_title_with_files():
    """Long title should be truncated to fit within MAX_WIDTH with file count."""
    task = {
        "id": "t1",
        "title": "A" * 50,  # very long title
        "state": "done",
        "files_changed": ["a.py", "b.py", "c.py"],
    }
    line = format_task_line(task, selected=False)
    assert "…" in line
    assert "3 files" in line


def test_format_task_line_error_badge_with_long_title_truncates():
    """Error tasks with long titles should truncate and still show badge + count."""
    task = {
        "id": "t1",
        "title": "A" * 50,
        "state": "error",
        "files_changed": ["a.py"],
    }
    line = format_task_line(task, selected=False)
    assert "…" in line
    assert "⚠" in line
    assert "1 files" in line


def test_format_task_line_selected_with_files_valid_markup():
    """Selected task with files_changed should produce valid Rich markup."""
    from io import StringIO

    from rich.console import Console

    task = {"id": "t1", "title": "Auth", "state": "error", "files_changed": ["a.py"]}
    line = format_task_line(task, selected=True)
    console = Console(file=StringIO(), force_terminal=True)
    console.print(line)  # Will raise MarkupError if broken


# ── Multi-repo display tests ─────────────────────────────────────────────


class TestFormatTaskLineMultiRepo:
    """Tests for multi-repo prefix in format_task_line."""

    def test_format_task_line_multi_repo(self):
        """When multi_repo=True and task has repo, prepend [repo] prefix."""
        task = {"id": "t1", "title": "Add auth endpoint", "state": "in_progress", "repo": "backend"}
        line = format_task_line(task, selected=False, multi_repo=True)
        assert "backend" in line
        assert "#79c0ff" in line  # repo prefix color
        assert "Add auth endpoint" in line

    def test_format_task_line_single_repo(self):
        """When multi_repo=False, no repo prefix even if task has repo field."""
        task = {"id": "t1", "title": "Add auth endpoint", "state": "in_progress", "repo": "backend"}
        line = format_task_line(task, selected=False, multi_repo=False)
        assert "backend" not in line
        assert "Add auth endpoint" in line

    def test_format_task_line_no_repo_field(self):
        """When multi_repo=True but task has no repo field, no prefix."""
        task = {"id": "t1", "title": "Add auth endpoint", "state": "in_progress"}
        line = format_task_line(task, selected=False, multi_repo=True)
        assert (
            "#79c0ff" not in line or "79c0ff" not in line.split("\\[")[0]
            if "\\[" in line
            else "#79c0ff" not in line
        )
        assert "Add auth endpoint" in line

    def test_format_task_line_multi_repo_selected(self):
        """Selected task with multi_repo should include repo prefix and valid markup."""
        from io import StringIO

        from rich.console import Console

        task = {"id": "t1", "title": "Add auth endpoint", "state": "in_progress", "repo": "backend"}
        line = format_task_line(task, selected=True, multi_repo=True)
        assert "backend" in line
        assert "#79c0ff" in line
        # Verify valid Rich markup
        console = Console(file=StringIO(), force_terminal=True)
        console.print(line)  # Will raise MarkupError if broken

    def test_format_task_line_multi_repo_truncation(self):
        """Repo prefix should reduce available title width, causing earlier truncation."""
        long_title = "A" * 50
        task_no_repo = {"id": "t1", "title": long_title, "state": "todo"}
        task_with_repo = {"id": "t1", "title": long_title, "state": "todo", "repo": "backend"}

        line_no_repo = format_task_line(task_no_repo, selected=False, multi_repo=False)
        line_with_repo = format_task_line(task_with_repo, selected=True, multi_repo=True)

        # Both should truncate
        assert "…" in line_no_repo
        assert "…" in line_with_repo
        # With repo prefix, fewer title chars visible (more truncated)
        # Count A's in each line
        no_repo_as = line_no_repo.count("A")
        with_repo_as = line_with_repo.count("A")
        assert with_repo_as < no_repo_as, (
            f"Expected fewer As with repo prefix: {with_repo_as} vs {no_repo_as}"
        )
