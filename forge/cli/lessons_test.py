"""Tests for forge/cli/lessons.py — dedup prompt on add."""

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from forge.cli.lessons import lessons


def _mock_db(find_result=None, add_result="fake-uuid-1234-5678-9abc-def012345678"):
    """Return a mock Database that supports initialize/close/find/add."""
    db = AsyncMock()
    db.initialize = AsyncMock()
    db.close = AsyncMock()
    db.find_matching_lesson = AsyncMock(return_value=find_result)
    db.add_lesson = AsyncMock(return_value=add_result)
    return db


def test_lessons_add_no_duplicate():
    """Should add lesson directly when no duplicate exists."""
    db = _mock_db()
    runner = CliRunner()
    with patch("forge.cli.lessons._get_db", return_value=db):
        result = runner.invoke(
            lessons,
            [
                "add",
                "--global",
                "--category",
                "command_failure",
                "My Lesson",
                "Fix it this way",
            ],
        )
    assert result.exit_code == 0
    assert "Added global lesson" in result.output
    db.add_lesson.assert_called_once()


def test_lessons_add_duplicate_confirm_yes():
    """Should prompt and proceed when user confirms adding duplicate."""
    existing = MagicMock()
    existing.title = "Existing Similar Lesson"
    db = _mock_db(find_result=existing)
    runner = CliRunner()
    with patch("forge.cli.lessons._get_db", return_value=db):
        result = runner.invoke(
            lessons,
            [
                "add",
                "--global",
                "--category",
                "command_failure",
                "My Lesson",
                "Fix it this way",
            ],
            input="y\n",
        )
    assert result.exit_code == 0
    assert "Similar lesson exists" in result.output
    assert "Added global lesson" in result.output
    db.add_lesson.assert_called_once()


def test_lessons_add_duplicate_confirm_no():
    """Should abort when user declines adding duplicate."""
    existing = MagicMock()
    existing.title = "Existing Similar Lesson"
    db = _mock_db(find_result=existing)
    runner = CliRunner()
    with patch("forge.cli.lessons._get_db", return_value=db):
        result = runner.invoke(
            lessons,
            [
                "add",
                "--global",
                "--category",
                "command_failure",
                "My Lesson",
                "Fix it this way",
            ],
            input="n\n",
        )
    assert result.exit_code == 0
    assert "Aborted" in result.output
    db.add_lesson.assert_not_called()


def test_lessons_add_with_trigger():
    """Should use --trigger for dedup check instead of title."""
    db = _mock_db()
    runner = CliRunner()
    with patch("forge.cli.lessons._get_db", return_value=db):
        result = runner.invoke(
            lessons,
            [
                "add",
                "--global",
                "--category",
                "code_pattern",
                "--trigger",
                "custom trigger pattern",
                "My Lesson",
                "Fix it this way",
            ],
        )
    assert result.exit_code == 0
    # find_matching_lesson should have been called with the custom trigger
    db.find_matching_lesson.assert_called_once()
    call_args = db.find_matching_lesson.call_args
    assert call_args[0][0] == "custom trigger pattern"
