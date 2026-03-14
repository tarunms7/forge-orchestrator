"""Gate 2: LLM code review. A fresh Claude instance reviews changes against the task spec."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from claude_code_sdk import ClaudeCodeOptions

from forge.core.sdk_helpers import sdk_query
from forge.review.pipeline import GateResult

logger = logging.getLogger("forge.review")


@dataclass
class ReviewCostInfo:
    """Cost information from an LLM review call."""

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


REVIEW_SYSTEM_PROMPT = """You are a senior code reviewer. Your job is to catch bugs, security issues,
and design problems that would cause production incidents. You are the last
line of defense before code ships.

You will receive a task specification and a git diff. Review the code
thoroughly and respond with EXACTLY one of:

PASS: <explanation covering what you verified>
FAIL: <specific issues with file paths and line references>

## Review Checklist (evaluate ALL categories)

1. CORRECTNESS
   - Does the code actually implement what the task spec requires?
   - Are there logic errors, off-by-one errors, or wrong conditions?
   - Are return values and error states handled correctly?
   - Do edge cases work (empty inputs, None values, boundary conditions)?

2. ERROR HANDLING
   - Are exceptions caught at the right level (not too broad, not missing)?
   - Do error paths clean up resources (files, connections, locks)?
   - Are error messages useful for debugging (not swallowed silently)?

3. SECURITY
   - Is user input validated/sanitized before use?
   - Are secrets handled safely (not logged, not in URLs, not hardcoded)?
   - Are file paths validated (no path traversal)?
   - Are permissions checked where needed?

4. CONCURRENCY & STATE
   - Are shared resources protected from race conditions?
   - Are async operations awaited properly?
   - Is mutable state handled safely across concurrent access?

5. DESIGN QUALITY
   - Is the code doing what it should at the right abstraction level?
   - Are functions/methods focused (single responsibility)?
   - Are there obvious performance issues (N+1 queries, unbounded loops)?

## Rules
- Be thorough. A missed bug in review means a production incident.
- Be specific. Reference exact file paths and line numbers.
- Do NOT pass code just because it "mostly works." If there are real issues, FAIL it.
- Do NOT nitpick pure style preferences (variable naming, import ordering) when
  no linter flags them. Focus on things that affect correctness and reliability.
- If a "Pipeline Task Context" section lists sibling tasks and their file scopes,
  do NOT fail for missing integration code that belongs to a sibling task's scope."""


async def gate2_llm_review(
    task_title: str,
    task_description: str,
    diff: str,
    worktree_path: str | None = None,
    model: str = "sonnet",
    prior_feedback: str | None = None,
    prior_diff: str | None = None,
    project_context: str = "",
    allowed_files: list[str] | None = None,
    delta_diff: str | None = None,
    sibling_context: str | None = None,
    custom_review_focus: str = "",
    on_message: Callable[[Any], Awaitable[None]] | None = None,
) -> tuple[GateResult, ReviewCostInfo]:
    """Run LLM code review on the given diff against the task spec.

    Args:
        prior_feedback: If this is a re-review after a retry, the previous
            reviewer's feedback. The new reviewer is told to focus on
            verifying those specific issues were fixed, not inventing
            new complaints.
        prior_diff: If this is a re-review, the diff from the previous
            (rejected) attempt so the reviewer can compare and verify
            fixes were actually made.
        project_context: Project snapshot context for the reviewer.
        allowed_files: List of files this task is allowed to modify.
            The reviewer will flag any out-of-scope changes.

    Returns:
        A tuple of (GateResult, ReviewCostInfo) with the review verdict
        and accumulated cost information across all retry attempts.
    """
    cost_info = ReviewCostInfo()

    if not diff.strip():
        return (
            GateResult(passed=False, gate="gate2_llm_review", details="No changes to review"),
            cost_info,
        )

    prompt = _build_review_prompt(
        task_title, task_description, diff, prior_feedback,
        prior_diff=prior_diff, project_context=project_context,
        allowed_files=allowed_files, delta_diff=delta_diff,
        sibling_context=sibling_context,
    )

    system_prompt = REVIEW_SYSTEM_PROMPT
    if custom_review_focus:
        system_prompt += "\n\n" + custom_review_focus

    options = ClaudeCodeOptions(
        system_prompt=system_prompt,
        # max_turns=2 gives the model one turn to respond + buffer for
        # rate_limit_event recovery.  The reviewer reads the diff from
        # the prompt — it doesn't need filesystem tools.  Restricting
        # tools prevents wasted turns and permission hangs.
        max_turns=2,
        model=model,
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="acceptEdits",
    )
    if worktree_path:
        options.cwd = worktree_path

    # Retry the SDK call up to 3 times if the result is empty.
    # Empty results are transient SDK issues (rate limits, timeouts) —
    # retrying the review is much cheaper than retrying the entire task.
    review_timeout_seconds = 120  # 2 min — review is a short, focused task
    max_review_attempts = 3
    for attempt in range(1, max_review_attempts + 1):
        try:
            result = await asyncio.wait_for(
                sdk_query(prompt=prompt, options=options, on_message=on_message),
                timeout=review_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning("L2 review timed out after %ds (attempt %d/%d)", review_timeout_seconds, attempt, max_review_attempts)
            if attempt == max_review_attempts:
                return (
                    GateResult(
                        passed=True, gate="gate2_llm_review",
                        details=f"Review timed out after {max_review_attempts} attempts — auto-passing to unblock pipeline",
                    ),
                    cost_info,
                )
            continue
        except Exception as e:
            logger.warning("L2 review SDK call failed (attempt %d/%d): %s", attempt, max_review_attempts, e)
            if attempt == max_review_attempts:
                return (
                    GateResult(
                        passed=False, gate="gate2_llm_review",
                        details=f"SDK error during review after {max_review_attempts} attempts: {e}",
                        retriable=True,
                    ),
                    cost_info,
                )
            continue

        # Accumulate cost info from each attempt
        if result is not None:
            cost_info.cost_usd += result.cost_usd
            cost_info.input_tokens += result.input_tokens
            cost_info.output_tokens += result.output_tokens

        result_text = result.result if result and result.result else ""
        if result_text:
            return (_parse_review_result(result_text), cost_info)

        logger.warning(
            "L2 review returned empty result (attempt %d/%d)", attempt, max_review_attempts,
        )
        if attempt < max_review_attempts:
            await asyncio.sleep(2)  # Brief pause before retrying

    # All attempts returned empty
    return (
        GateResult(
            passed=False, gate="gate2_llm_review",
            details=f"Empty review response after {max_review_attempts} attempts",
            retriable=True,
        ),
        cost_info,
    )


def _build_review_prompt(
    title: str, description: str, diff: str,
    prior_feedback: str | None = None,
    *,
    prior_diff: str | None = None,
    project_context: str = "",
    allowed_files: list[str] | None = None,
    delta_diff: str | None = None,
    sibling_context: str | None = None,
) -> str:
    parts = []
    if project_context:
        parts.append(f"{project_context}\n\n")
    if sibling_context:
        parts.append(f"{sibling_context}\n\n")
    parts += [
        f"Task: {title}\n",
        f"Description: {description}\n\n",
    ]
    if allowed_files:
        parts.append(
            f"File scope: This task is ONLY allowed to modify: {', '.join(allowed_files)}.\n"
            "If the diff contains changes to files outside this list, "
            "FAIL immediately with 'OUT OF SCOPE' and list the violating files.\n\n"
        )
    parts.append(
        f"Git diff of changes:\n```diff\n{diff}\n```\n\n",
    )
    if prior_feedback:
        parts.append(
            "=== PRIOR REVIEW CONTEXT ===\n"
            "A previous reviewer rejected this code with the following feedback:\n"
            f"---\n{prior_feedback}\n---\n\n"
        )
        if prior_diff:
            prior_diff_snippet = prior_diff[:6000]
            parts.append(
                "=== PRIOR DIFF (what was rejected) ===\n"
                f"```diff\n{prior_diff_snippet}\n```\n\n"
            )
        parts.append(
            "The developer has attempted to fix these issues.\n"
            "Verify the specific issues above were addressed, AND do a full review of the\n"
            "current code. If you find new genuine issues (bugs, security, error handling),\n"
            "FAIL — regardless of whether they were in the prior feedback or not.\n"
            "Prior feedback is context, not a ceiling on what you can flag.\n\n"
        )
    if delta_diff:
        delta_snippet = delta_diff[:6000]
        parts.append(
            "=== CHANGES SINCE LAST REVIEW (DELTA) ===\n"
            "These are the changes the developer made in this retry attempt, shown for context.\n"
            f"```diff\n{delta_snippet}\n```\n\n"
            "The full diff above shows the complete current state.\n\n"
        )
    parts.append("Review this code. Respond with PASS or FAIL.")
    return "".join(parts)


def _parse_review_result(text: str) -> GateResult:
    """Parse the LLM reviewer's response to extract a PASS/FAIL verdict.

    The parser checks for the verdict in three ways (in order):
    1. Text starts with PASS/FAIL (ideal format)
    2. A line starts with PASS/FAIL (verdict buried in analysis)
    3. PASS or FAIL appears anywhere in the text (fallback)

    This flexibility is needed because models (especially opus) often
    write detailed analysis before stating their verdict.
    """
    text = text.strip()
    if not text:
        return GateResult(passed=False, gate="gate2_llm_review", details="Empty review response")

    upper = text.upper()

    # 1. Ideal: response starts with verdict
    if upper.startswith("PASS"):
        return GateResult(passed=True, gate="gate2_llm_review", details=text)
    if upper.startswith("FAIL"):
        return GateResult(passed=False, gate="gate2_llm_review", details=text)

    # 2. A line starts with the verdict (opus often writes analysis first)
    for line in text.splitlines():
        line_upper = line.strip().upper()
        if line_upper.startswith("PASS"):
            return GateResult(passed=True, gate="gate2_llm_review", details=text)
        if line_upper.startswith("FAIL"):
            return GateResult(passed=False, gate="gate2_llm_review", details=text)

    # 3. Fallback: PASS/FAIL at the start of any line (stricter than "anywhere")
    pass_match = re.search(r"^PASS\b", upper, re.MULTILINE)
    fail_match = re.search(r"^FAIL\b", upper, re.MULTILINE)
    if pass_match and not fail_match:
        return GateResult(passed=True, gate="gate2_llm_review", details=text)
    if fail_match and not pass_match:
        return GateResult(passed=False, gate="gate2_llm_review", details=text)

    return GateResult(
        passed=False,
        gate="gate2_llm_review",
        details=f"Unclear review response (treating as fail): {text[:200]}",
    )
