"""Tests for llm_review — review prompt construction and verdict parsing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.review.llm_review import _build_review_prompt, _parse_review_result, gate2_llm_review


class TestBuildReviewPrompt:
    """_build_review_prompt() constructs the correct prompt for the reviewer."""

    def test_basic_prompt(self):
        """Minimal prompt includes task spec and diff."""
        prompt = _build_review_prompt("Add login", "Implement JWT login", "diff --git a/auth.py")
        assert "Task: Add login" in prompt
        assert "Description: Implement JWT login" in prompt
        assert "diff --git a/auth.py" in prompt
        assert "PASS, FAIL, or UNCERTAIN" in prompt

    def test_includes_project_context(self):
        """Project context appears before the task spec."""
        prompt = _build_review_prompt(
            "T",
            "D",
            "diff",
            project_context="## Project Snapshot\nPython 3.12",
        )
        assert "## Project Snapshot" in prompt
        # Context should come before the task
        assert prompt.index("Project Snapshot") < prompt.index("Task: T")

    def test_includes_sibling_context(self):
        """Sibling context section appears in the prompt when provided."""
        sibling_ctx = (
            "## Pipeline Task Context (DAG Awareness)\n"
            "- **task-1** (Add DB schema): files=[db.py], state=done"
        )
        prompt = _build_review_prompt(
            "T",
            "D",
            "diff",
            sibling_context=sibling_ctx,
        )
        assert "Pipeline Task Context" in prompt
        assert "task-1" in prompt
        assert "Add DB schema" in prompt

    def test_no_sibling_context_when_none(self):
        """No sibling section when sibling_context is None."""
        prompt = _build_review_prompt("T", "D", "diff", sibling_context=None)
        assert "Pipeline Task Context" not in prompt

    def test_includes_file_scope(self):
        """File scope enforcement appears in the prompt."""
        prompt = _build_review_prompt(
            "T",
            "D",
            "diff",
            allowed_files=["src/auth.py", "src/models.py"],
        )
        assert "src/auth.py" in prompt
        assert "src/models.py" in prompt
        assert "OUT OF SCOPE" in prompt

    def test_includes_prior_feedback_on_retry(self):
        """Prior reviewer feedback appears for re-reviews."""
        prompt = _build_review_prompt(
            "T",
            "D",
            "diff",
            prior_feedback="Missing error handling in line 42",
        )
        assert "PRIOR REVIEW CONTEXT" in prompt
        assert "Missing error handling in line 42" in prompt
        assert "Prior feedback is context" in prompt

    def test_includes_prior_diff_on_retry(self):
        """Prior diff appears alongside prior feedback."""
        prompt = _build_review_prompt(
            "T",
            "D",
            "current diff",
            prior_feedback="Bug in auth",
            prior_diff="old diff content here",
        )
        assert "PRIOR DIFF" in prompt
        assert "old diff content here" in prompt

    def test_prior_diff_truncated_to_6000(self):
        """Prior diff is capped at 6000 characters."""
        long_diff = "x" * 10000
        prompt = _build_review_prompt(
            "T",
            "D",
            "diff",
            prior_feedback="Issues",
            prior_diff=long_diff,
        )
        # The truncated diff should be at most 6000 chars of 'x'
        assert "x" * 6001 not in prompt

    def test_includes_delta_diff(self):
        """Delta diff section appears when provided."""
        prompt = _build_review_prompt(
            "T",
            "D",
            "full diff",
            delta_diff="delta changes only",
        )
        assert "CHANGES SINCE LAST REVIEW (DELTA)" in prompt
        assert "delta changes only" in prompt
        assert "shown for context" in prompt

    def test_no_delta_when_none(self):
        """No delta section when delta_diff is None."""
        prompt = _build_review_prompt("T", "D", "diff", delta_diff=None)
        assert "CHANGES SINCE LAST REVIEW" not in prompt

    def test_delta_diff_truncated_to_6000(self):
        """Delta diff is capped at 6000 characters."""
        long_delta = "y" * 10000
        prompt = _build_review_prompt("T", "D", "diff", delta_diff=long_delta)
        assert "y" * 6001 not in prompt

    def test_full_retry_prompt_ordering(self):
        """On a full retry review, all sections appear in correct order."""
        prompt = _build_review_prompt(
            "Add webhook",
            "Create POST endpoint",
            "full diff here",
            prior_feedback="Missing PR creation",
            prior_diff="old diff",
            allowed_files=["webhooks.py"],
            delta_diff="small fix diff",
            sibling_context="## Pipeline Task Context\n- task-3 owns app.py",
        )
        # Verify ordering: sibling context → task → scope → diff → prior → delta → verdict
        assert prompt.index("Pipeline Task Context") < prompt.index("Task: Add webhook")
        assert prompt.index("Task: Add webhook") < prompt.index("File scope")
        assert prompt.index("File scope") < prompt.index("full diff here")
        assert prompt.index("PRIOR REVIEW CONTEXT") < prompt.index("CHANGES SINCE LAST REVIEW")
        assert prompt.index("CHANGES SINCE LAST REVIEW") < prompt.index("PASS, FAIL, or UNCERTAIN")


class TestParseReviewResult:
    """_parse_review_result() extracts PASS/FAIL verdicts from reviewer text."""

    def test_starts_with_pass(self):
        result = _parse_review_result("PASS: looks good")
        assert result.passed is True
        assert result.gate == "gate2_llm_review"

    def test_starts_with_fail(self):
        result = _parse_review_result("FAIL: missing error handling")
        assert result.passed is False

    def test_line_starts_with_pass(self):
        """Verdict on a line after analysis text."""
        result = _parse_review_result("Analysis:\nThe code looks fine.\nPASS: all good")
        assert result.passed is True

    def test_line_starts_with_fail(self):
        result = _parse_review_result("Let me review...\nFAIL: bugs found")
        assert result.passed is False

    def test_pass_mid_sentence_no_longer_matches(self):
        """PASS mid-sentence no longer matches with stricter regex (must be at line start)."""
        result = _parse_review_result("The verdict is PASS for this code")
        assert result.passed is False
        assert "Unclear" in result.details

    def test_fail_mid_sentence_no_longer_matches(self):
        """FAIL mid-sentence no longer matches with stricter regex (must be at line start)."""
        result = _parse_review_result("I would say this is a FAIL because of bugs")
        assert result.passed is False
        assert "Unclear" in result.details

    def test_empty_text(self):
        result = _parse_review_result("")
        assert result.passed is False
        assert "Empty" in result.details

    def test_unclear_response_treated_as_fail(self):
        result = _parse_review_result("The code needs some work")
        assert result.passed is False
        assert "Unclear" in result.details

    def test_case_insensitive(self):
        result = _parse_review_result("pass: looks fine")
        assert result.passed is True

    def test_pass_mid_sentence_not_matched(self):
        """'PASS' embedded mid-sentence should NOT match as PASS (falls through to fail-safe)."""
        result = _parse_review_result("The data would PASS through the function")
        assert result.passed is False
        assert "Unclear" in result.details

    def test_pass_at_start_of_line_with_colon(self):
        """'PASS: looks good' should PASS."""
        result = _parse_review_result("PASS: looks good")
        assert result.passed is True

    def test_pass_at_start_of_second_line(self):
        """PASS at start of second line should PASS."""
        result = _parse_review_result("After analysis\nPASS")
        assert result.passed is True

    def test_both_pass_and_fail_ambiguous(self):
        """Ambiguous: both PASS and FAIL present — caught as FAIL (starts with FAIL)."""
        result = _parse_review_result("FAIL: critical bug\nBut PASS case exists")
        # Stage 1 catches "FAIL" at the start of the text, so this is FAIL.
        assert result.passed is False


class TestOnMessagePassthrough:
    """gate2_llm_review() passes on_message to sdk_query."""

    @pytest.mark.asyncio
    async def test_on_message_passed_to_sdk_query(self):
        """on_message callback is forwarded to sdk_query."""
        mock_result = MagicMock()
        mock_result.result = "PASS: looks good"
        mock_result.cost_usd = 0.01
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50

        callback = AsyncMock()

        with patch("forge.review.llm_review.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.return_value = mock_result
            result, cost = await gate2_llm_review(
                "Test task",
                "Test desc",
                "diff content",
                on_message=callback,
            )

        assert result.passed is True
        # Verify on_message was passed through to sdk_query
        mock_sdk.assert_called_once()
        call_kwargs = mock_sdk.call_args
        assert call_kwargs.kwargs.get("on_message") is callback

    @pytest.mark.asyncio
    async def test_on_message_none_by_default(self):
        """on_message defaults to None when not provided."""
        mock_result = MagicMock()
        mock_result.result = "PASS: ok"
        mock_result.cost_usd = 0.0
        mock_result.input_tokens = 0
        mock_result.output_tokens = 0

        with patch("forge.review.llm_review.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.return_value = mock_result
            await gate2_llm_review("T", "D", "diff")

        call_kwargs = mock_sdk.call_args
        assert call_kwargs.kwargs.get("on_message") is None

    @pytest.mark.asyncio
    async def test_on_message_passed_on_retry(self):
        """on_message is passed on every retry attempt."""
        mock_result_empty = MagicMock()
        mock_result_empty.result = ""
        mock_result_empty.cost_usd = 0.0
        mock_result_empty.input_tokens = 0
        mock_result_empty.output_tokens = 0

        mock_result_ok = MagicMock()
        mock_result_ok.result = "PASS: ok"
        mock_result_ok.cost_usd = 0.01
        mock_result_ok.input_tokens = 100
        mock_result_ok.output_tokens = 50

        callback = AsyncMock()

        with patch("forge.review.llm_review.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.side_effect = [mock_result_empty, mock_result_ok]
            result, _ = await gate2_llm_review(
                "T",
                "D",
                "diff",
                on_message=callback,
            )

        assert result.passed is True
        # Called twice (first empty, then success)
        assert mock_sdk.call_count == 2
        for call in mock_sdk.call_args_list:
            assert call.kwargs.get("on_message") is callback


class TestDeadCodeRemoval:
    """Verify that the dead get_diff function was removed."""

    def test_get_diff_not_importable(self):
        """get_diff should no longer exist in llm_review."""
        with pytest.raises(ImportError):
            from forge.review.llm_review import get_diff  # noqa: F401


class TestReviewSystemPrompt:
    """Verify the system prompt has the comprehensive review checklist."""

    def test_prompt_has_checklist_categories(self):
        from forge.review.llm_review import REVIEW_SYSTEM_PROMPT

        assert "CORRECTNESS" in REVIEW_SYSTEM_PROMPT
        assert "ERROR HANDLING" in REVIEW_SYSTEM_PROMPT
        assert "SECURITY" in REVIEW_SYSTEM_PROMPT
        assert "CONCURRENCY & STATE" in REVIEW_SYSTEM_PROMPT
        assert "DESIGN QUALITY" in REVIEW_SYSTEM_PROMPT

    def test_prompt_has_strict_framing(self):
        from forge.review.llm_review import REVIEW_SYSTEM_PROMPT

        assert "senior code reviewer" in REVIEW_SYSTEM_PROMPT
        assert "production incidents" in REVIEW_SYSTEM_PROMPT

    def test_prompt_forbids_style_nitpicking(self):
        from forge.review.llm_review import REVIEW_SYSTEM_PROMPT

        assert "Do NOT nitpick pure style preferences" in REVIEW_SYSTEM_PROMPT


class TestRetryPromptNoSuppression:
    """Retry prompt no longer suppresses thorough review."""

    def test_retry_prompt_no_suppression_language(self):
        prompt = _build_review_prompt("T", "D", "diff", prior_feedback="Bug in line 42")
        assert "PRIMARY job" not in prompt
        assert "Do NOT invent new stylistic complaints" not in prompt
        assert "focus on the prior feedback" not in prompt

    def test_retry_prompt_allows_new_issues(self):
        prompt = _build_review_prompt("T", "D", "diff", prior_feedback="Bug in line 42")
        assert "full review" in prompt.lower() or "full review" in prompt
        assert "not a ceiling" in prompt or "Prior feedback is context" in prompt

    def test_delta_diff_neutral_framing(self):
        prompt = _build_review_prompt("T", "D", "full diff", delta_diff="delta changes")
        assert "shown for context" in prompt
        assert "Focus your review on these delta changes" not in prompt


class TestCustomReviewFocusSeparator:
    """custom_review_focus gets proper separator from system prompt."""

    @pytest.mark.asyncio
    async def test_custom_focus_has_separator(self):
        mock_result = MagicMock()
        mock_result.result = "PASS: ok"
        mock_result.cost_usd = 0.0
        mock_result.input_tokens = 0
        mock_result.output_tokens = 0

        captured_options = []

        async def capture_sdk_query(*, prompt, options, on_message=None):
            captured_options.append(options)
            return mock_result

        with patch("forge.review.llm_review.sdk_query", side_effect=capture_sdk_query):
            await gate2_llm_review(
                "T",
                "D",
                "diff",
                custom_review_focus="Focus on error handling paths.",
            )

        assert len(captured_options) == 1
        system_prompt = captured_options[0].system_prompt
        assert "\n\nFocus on error handling paths." in system_prompt


class TestUncertainVerdict:
    """UNCERTAIN verdict should return needs_human=True."""

    def test_uncertain_at_start(self):
        result = _parse_review_result(
            "UNCERTAIN: Can't tell if edge case is handled without seeing caller"
        )
        assert result.passed is False
        assert result.needs_human is True
        assert "edge case" in result.details

    def test_uncertain_on_line(self):
        text = "Analysis:\nThe code looks reasonable but...\nUNCERTAIN: Missing context about the caller's intent"
        result = _parse_review_result(text)
        assert result.passed is False
        assert result.needs_human is True

    def test_pass_still_works(self):
        result = _parse_review_result("PASS: All checks verified")
        assert result.passed is True
        assert result.needs_human is False

    def test_fail_still_works(self):
        result = _parse_review_result("FAIL: Bug on line 42")
        assert result.passed is False
        assert result.needs_human is False

    def test_unclear_response_still_fails_not_uncertain(self):
        result = _parse_review_result("I'm not sure what to think about this code")
        assert result.passed is False
        assert result.needs_human is False


class TestEmptyReviewEscalation:
    """Empty L2 review should escalate to human, not auto-pass."""

    @pytest.mark.asyncio
    async def test_empty_review_returns_needs_human(self):
        """Empty SDK response should return needs_human=True, not passed=True."""
        mock_result = MagicMock()
        mock_result.result = ""
        mock_result.cost_usd = 0
        mock_result.input_tokens = 0
        mock_result.output_tokens = 0
        mock_result.num_turns = 0
        mock_result.duration_ms = 100
        mock_result.duration_api_ms = 50

        with patch("forge.review.llm_review.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.return_value = mock_result
            gate_result, cost_info = await gate2_llm_review(
                "Test task", "Test desc", "diff --git a/test.py", model="sonnet"
            )

        assert gate_result.passed is False
        assert gate_result.needs_human is True
        assert "Human review needed" in gate_result.details

    @pytest.mark.asyncio
    async def test_successful_review_no_needs_human(self):
        """Successful review should not set needs_human."""
        mock_result = MagicMock()
        mock_result.result = "PASS: Looks good"
        mock_result.cost_usd = 0.01
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50

        with patch("forge.review.llm_review.sdk_query", new_callable=AsyncMock) as mock_sdk:
            mock_sdk.return_value = mock_result
            gate_result, cost_info = await gate2_llm_review(
                "Test task", "Test desc", "diff --git a/test.py", model="sonnet"
            )

        assert gate_result.passed is True
        assert gate_result.needs_human is False
