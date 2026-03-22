"""Tests for forge.core.sanitize — task_id and repo_id validation."""

import pytest

from forge.core.sanitize import (
    UnsafeInputError,
    validate_repo_id,
    validate_task_id,
)


# ── validate_task_id: valid inputs ──────────────────────────────────────

class TestValidateTaskIdValid:
    def test_simple_alphanumeric(self):
        assert validate_task_id("task1") == "task1"

    def test_with_hyphens(self):
        assert validate_task_id("my-task-42") == "my-task-42"

    def test_with_underscores(self):
        assert validate_task_id("my_task_42") == "my_task_42"

    def test_mixed_case(self):
        assert validate_task_id("MyTask") == "MyTask"

    def test_single_char(self):
        assert validate_task_id("a") == "a"

    def test_starts_with_digit(self):
        assert validate_task_id("1task") == "1task"

    def test_max_length_64(self):
        tid = "a" * 64
        assert validate_task_id(tid) == tid

    def test_typical_forge_id(self):
        assert validate_task_id("followup-abcd1234-task-1") == "followup-abcd1234-task-1"


# ── validate_task_id: invalid inputs ────────────────────────────────────

class TestValidateTaskIdInvalid:
    def test_empty_string(self):
        with pytest.raises(UnsafeInputError, match="must not be empty"):
            validate_task_id("")

    def test_path_traversal_dotdot(self):
        with pytest.raises(UnsafeInputError, match="path traversal"):
            validate_task_id("../etc/passwd")

    def test_path_traversal_slash(self):
        with pytest.raises(UnsafeInputError, match="path traversal"):
            validate_task_id("task/../../etc")

    def test_path_traversal_backslash(self):
        with pytest.raises(UnsafeInputError, match="path traversal"):
            validate_task_id("task\\..\\secret")

    def test_too_long(self):
        with pytest.raises(UnsafeInputError, match="invalid characters or is too long"):
            validate_task_id("a" * 65)

    def test_starts_with_hyphen(self):
        with pytest.raises(UnsafeInputError, match="invalid characters"):
            validate_task_id("-task")

    def test_starts_with_underscore(self):
        with pytest.raises(UnsafeInputError, match="invalid characters"):
            validate_task_id("_task")

    def test_special_chars(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("task@home")

    def test_spaces(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("task 1")

    def test_semicolon_injection(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("task;rm -rf /")

    def test_dollar_sign(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("$HOME")

    def test_backtick_injection(self):
        with pytest.raises(UnsafeInputError):
            validate_task_id("`whoami`")


# ── validate_repo_id: valid inputs ──────────────────────────────────────

class TestValidateRepoIdValid:
    def test_simple(self):
        assert validate_repo_id("default") == "default"

    def test_with_hyphens(self):
        assert validate_repo_id("my-repo") == "my-repo"

    def test_digits(self):
        assert validate_repo_id("repo42") == "repo42"

    def test_single_char(self):
        assert validate_repo_id("a") == "a"

    def test_starts_with_digit(self):
        assert validate_repo_id("1repo") == "1repo"


# ── validate_repo_id: invalid inputs ────────────────────────────────────

class TestValidateRepoIdInvalid:
    def test_empty_string(self):
        with pytest.raises(UnsafeInputError, match="must not be empty"):
            validate_repo_id("")

    def test_path_traversal_dotdot(self):
        with pytest.raises(UnsafeInputError, match="path traversal"):
            validate_repo_id("../etc")

    def test_path_traversal_slash(self):
        with pytest.raises(UnsafeInputError, match="path traversal"):
            validate_repo_id("repo/evil")

    def test_path_traversal_backslash(self):
        with pytest.raises(UnsafeInputError, match="path traversal"):
            validate_repo_id("repo\\evil")

    def test_uppercase_rejected(self):
        with pytest.raises(UnsafeInputError, match="uppercase"):
            validate_repo_id("MyRepo")

    def test_underscores_rejected(self):
        with pytest.raises(UnsafeInputError, match="invalid characters"):
            validate_repo_id("my_repo")

    def test_starts_with_hyphen(self):
        with pytest.raises(UnsafeInputError, match="invalid characters"):
            validate_repo_id("-repo")

    def test_special_chars(self):
        with pytest.raises(UnsafeInputError):
            validate_repo_id("repo@home")

    def test_spaces(self):
        with pytest.raises(UnsafeInputError):
            validate_repo_id("my repo")


# ── UnsafeInputError is a ValueError ────────────────────────────────────

class TestUnsafeInputError:
    def test_is_value_error(self):
        err = UnsafeInputError("test message")
        assert isinstance(err, ValueError)

    def test_message_preserved(self):
        err = UnsafeInputError("bad id")
        assert str(err) == "bad id"

    def test_catchable_as_value_error(self):
        with pytest.raises(ValueError):
            raise UnsafeInputError("test")
