"""Tests for error classification."""

from forge.core.error_classifier import (
    ClassifiedError,
    classify_agent_error,
    classify_merge_error,
    classify_review_error,
)


class TestClassifyAgentError:
    def test_rate_limit(self):
        result = classify_agent_error("429 Too Many Requests - rate limit exceeded")
        assert result.category == "sdk_error"
        assert result.retriable

    def test_auth_error(self):
        result = classify_agent_error("Authentication failed: invalid API key")
        assert result.category == "sdk_error"
        assert not result.retriable  # auth errors aren't retriable

    def test_timeout(self):
        result = classify_agent_error("Agent timed out after 600s")
        assert result.category == "agent_timeout"
        assert result.retriable

    def test_max_turns(self):
        result = classify_agent_error("Exceeded max turns limit")
        assert result.category == "agent_timeout"
        assert result.retriable

    def test_no_changes(self):
        result = classify_agent_error("Agent produced no changes")
        assert result.category == "agent_no_changes"
        assert result.retriable

    def test_guard_triggered(self):
        result = classify_agent_error("GuardTriggered: retry loop detected")
        assert result.category == "agent_crash"
        assert result.retriable

    def test_network_error(self):
        result = classify_agent_error("Connection reset by peer")
        assert result.category == "sdk_error"
        assert result.retriable

    def test_unknown_error(self):
        result = classify_agent_error("Something unexpected happened")
        assert result.category == "agent_crash"
        assert result.retriable

    def test_none_error(self):
        result = classify_agent_error(None)
        assert result.category == "agent_crash"


class TestClassifyReviewError:
    def test_build_failure(self):
        result = classify_review_error("gate0_build", "npm run build failed")
        assert result.category == "build_failure"

    def test_lint_timeout(self):
        result = classify_review_error("gate1_auto_check", "Lint timed out after 360s")
        assert result.category == "lint_failure"
        assert "timed out" in result.message.lower()

    def test_lint_failure(self):
        result = classify_review_error("gate1_auto_check", "3 errors found in main.py")
        assert result.category == "lint_failure"

    def test_test_failure(self):
        result = classify_review_error("gate1.5_test", "2 tests failed: test_login, test_auth")
        assert result.category == "test_failure"

    def test_test_infra_error(self):
        result = classify_review_error(
            "gate1.5_test", "ModuleNotFoundError: no module named 'pytest'"
        )
        assert result.category == "infra_error"

    def test_llm_review_rejection(self):
        result = classify_review_error("gate2_llm_review", "Missing error handling in API")
        assert result.category == "review_rejection"


class TestClassifyMergeError:
    def test_conflict(self):
        result = classify_merge_error("CONFLICT (content): Merge conflict in src/main.py")
        assert result.category == "merge_conflict"
        assert result.retriable

    def test_non_fast_forward(self):
        result = classify_merge_error("Updates were rejected because the tip is not a fast-forward")
        assert result.category == "merge_conflict"
        assert result.retriable

    def test_unknown_merge_error(self):
        result = classify_merge_error("fatal: refusing to merge unrelated histories")
        assert result.category == "merge_conflict"


class TestClassifiedError:
    def test_short(self):
        e = ClassifiedError(category="test", message="something broke")
        assert e.short == "[test] something broke"
