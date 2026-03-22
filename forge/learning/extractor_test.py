"""Tests for forge.learning.extractor."""

from dataclasses import dataclass

import pytest

from forge.learning.extractor import (
    _extract_feedback_theme,
    _resolution_for_error,
    _shorten_command,
    _summarize_feedback,
    classify_scope,
    extract_from_agent_learning,
    extract_from_command_failures,
    extract_from_review_feedback,
    is_infra_noise,
)


@dataclass
class MockFailureRecord:
    command: str
    normalized_command: str
    error_class: str
    stderr_snippet: str
    attempt_number: int


def _make_failure(
    command: str = "pip install foo",
    normalized_command: str = "pip install <pkg>",
    error_class: str = "module_not_found",
    stderr_snippet: str = "ModuleNotFoundError: No module named 'foo'",
    attempt_number: int = 1,
) -> MockFailureRecord:
    return MockFailureRecord(
        command=command,
        normalized_command=normalized_command,
        error_class=error_class,
        stderr_snippet=stderr_snippet,
        attempt_number=attempt_number,
    )


# --- extract_from_command_failures ---


def test_extract_from_command_failures_basic():
    failures = [
        _make_failure(attempt_number=1),
        _make_failure(command="pip install foo==2.0", attempt_number=2),
    ]
    lesson = extract_from_command_failures(failures)
    assert lesson.category == "command_failure"
    assert "pip install foo" in lesson.title
    assert "module_not_found" in lesson.title
    assert lesson.trigger == "pip install <pkg>"
    assert lesson.scope in ("global", "project")
    assert lesson.id  # UUID assigned
    assert lesson.content  # non-empty


def test_extract_from_command_failures_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        extract_from_command_failures([])


def test_extract_from_command_failures_project_scope():
    failures = [
        _make_failure(
            command=".venv/bin/python -m pytest",
            normalized_command=".venv/bin/python -m pytest",
            error_class="test_failure",
            stderr_snippet="FAILED tests/test_foo.py",
        ),
    ]
    lesson = extract_from_command_failures(failures)
    assert lesson.scope == "project"


def test_extract_from_command_failures_global_scope():
    failures = [
        _make_failure(
            command="git status",
            normalized_command="git status",
            error_class="command_not_found",
            stderr_snippet="git: command not found",
        ),
    ]
    lesson = extract_from_command_failures(failures)
    assert lesson.scope == "global"


# --- extract_from_review_feedback ---


def test_extract_from_review_feedback_basic():
    feedback = "Missing error handling in the retry loop. Add try/except around SDK calls."
    lesson = extract_from_review_feedback(feedback, task_title="Implement retry logic")
    assert lesson.category == "review_failure"
    assert lesson.title  # non-empty
    assert lesson.trigger  # non-empty
    assert lesson.content  # non-empty
    assert "Implement retry logic" in lesson.content
    assert lesson.resolution  # non-empty
    assert lesson.id  # UUID assigned


# --- classify_scope ---


def test_classify_scope_global():
    scope = classify_scope(command="git push origin main", error_output="rejected")
    assert scope == "global"


def test_classify_scope_project_venv():
    scope = classify_scope(command=".venv/bin/python test.py", error_output="")
    assert scope == "project"


def test_classify_scope_project_dir():
    scope = classify_scope(
        command="python run.py",
        error_output="Error in /home/user/myproject/main.py",
        project_dir="/home/user/myproject",
    )
    assert scope == "project"


def test_classify_scope_project_config():
    scope = classify_scope(command="cat pyproject.toml", error_output="")
    assert scope == "project"


# --- _shorten_command ---


def test_shorten_command_short():
    assert _shorten_command("git status") == "git status"
    assert _shorten_command("pip install foo") == "pip install foo"


def test_shorten_command_long():
    result = _shorten_command("python -m pytest tests/unit/test_foo.py -v --tb=short")
    assert result == "python -m pytest..."
    assert len(result) < len("python -m pytest tests/unit/test_foo.py -v --tb=short")


# --- _resolution_for_error ---


def test_resolution_for_error_known():
    res = _resolution_for_error("module_not_found", "pip install foo", "")
    assert "pip" in res.lower() or "install" in res.lower()
    res2 = _resolution_for_error("permission_denied", "rm /etc/hosts", "")
    assert "permission" in res2.lower()


def test_resolution_for_error_unknown():
    res = _resolution_for_error("some_weird_error", "weird_cmd", "")
    assert "diagnose" in res.lower() or "different approach" in res.lower()


# --- _summarize_feedback ---


def test_summarize_feedback():
    short = "Fix the imports"
    assert _summarize_feedback(short) == short

    long = "A" * 100
    result = _summarize_feedback(long)
    assert len(result) <= 60
    assert result.endswith("...")


# --- _extract_feedback_theme ---


def test_extract_feedback_theme():
    feedback = "Error in /src/main.py at line 42: missing `return` statement"
    theme = _extract_feedback_theme(feedback)
    # File paths should be stripped
    assert "/src/main.py" not in theme
    # Line numbers should be stripped
    assert "line 42" not in theme
    # Backtick code should be stripped
    assert "`return`" not in theme
    # Core words remain
    assert "error" in theme
    assert "missing" in theme


# --- is_infra_noise ---


class TestIsInfraNoise:
    def test_timeout(self):
        assert is_infra_noise("Command timed out after 90s") is True

    def test_connection_refused(self):
        assert is_infra_noise("ECONNREFUSED on port 8080") is True

    def test_real_code_change(self):
        assert is_infra_noise("Changed import path from foo to bar") is False

    def test_infra_crash_marker(self):
        assert is_infra_noise("[INFRASTRUCTURE CRASH] Task crashed") is True


# --- extract_from_agent_learning ---


class TestExtractFromAgentLearning:
    def test_valid_learning(self):
        data = {
            "trigger": "import path was wrong for the venv package",
            "resolution": "changed from 'import foo' to 'from foo.bar import baz' in utils.py",
            "files": ["utils.py"],
        }
        lesson = extract_from_agent_learning(data, task_title="Fix imports")
        assert lesson is not None
        assert lesson.category == "code_pattern"
        assert lesson.confidence == 0.5

    def test_rejects_missing_trigger(self):
        data = {"resolution": "did something useful here", "files": ["a.py"]}
        assert extract_from_agent_learning(data) is None

    def test_rejects_short_resolution(self):
        data = {"trigger": "something broke badly", "resolution": "fixed", "files": ["a.py"]}
        assert extract_from_agent_learning(data) is None

    def test_rejects_infra_noise(self):
        data = {
            "trigger": "connection refused when calling API",
            "resolution": "retried and the server came back after timeout",
            "files": ["api.py"],
        }
        assert extract_from_agent_learning(data) is None

    def test_rejects_no_action_verb(self):
        data = {
            "trigger": "the module was not available somehow",
            "resolution": "the package needs to be installed in the venv first",
            "files": ["setup.py"],
        }
        assert extract_from_agent_learning(data) is None

    def test_rejects_empty_files(self):
        data = {
            "trigger": "import path was wrong for package",
            "resolution": "changed the import path in the module file",
            "files": [],
        }
        assert extract_from_agent_learning(data) is None
