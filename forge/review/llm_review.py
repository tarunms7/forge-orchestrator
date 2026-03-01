"""Gate 2: LLM code review. A fresh Claude instance reviews changes against the task spec."""

import logging
import subprocess

from claude_code_sdk import ClaudeCodeOptions

from forge.core.sdk_helpers import sdk_query
from forge.review.pipeline import GateResult

logger = logging.getLogger("forge.review")

REVIEW_SYSTEM_PROMPT = """You are a code reviewer for the Forge multi-agent orchestration engine.

You will receive:
1. A task specification (what the code should do)
2. A git diff showing the changes made

Review the code and respond with EXACTLY one of these formats:

PASS: <brief explanation of why the code looks good>

FAIL: <specific issues that need fixing>

Be strict but fair. Check for:
- Does the code actually satisfy the task specification?
- Are there obvious bugs or logic errors?
- Does the code follow basic quality standards (no dead code, reasonable naming)?
- Are there any security concerns?"""


async def gate2_llm_review(
    task_title: str,
    task_description: str,
    diff: str,
    worktree_path: str | None = None,
    model: str = "sonnet",
    prior_feedback: str | None = None,
) -> GateResult:
    """Run LLM code review on the given diff against the task spec.

    Args:
        prior_feedback: If this is a re-review after a retry, the previous
            reviewer's feedback. The new reviewer is told to focus on
            verifying those specific issues were fixed, not inventing
            new complaints.
    """
    if not diff.strip():
        return GateResult(passed=False, gate="gate2_llm_review", details="No changes to review")

    prompt = _build_review_prompt(task_title, task_description, diff, prior_feedback)

    options = ClaudeCodeOptions(
        system_prompt=REVIEW_SYSTEM_PROMPT,
        # max_turns=2 gives the model one turn to respond + buffer for
        # rate_limit_event recovery. The reviewer doesn't need tools.
        max_turns=2,
        model=model,
    )
    if worktree_path:
        options.cwd = worktree_path

    try:
        result = await sdk_query(prompt=prompt, options=options)
    except Exception as e:
        logger.warning("L2 review SDK call failed: %s", e)
        return GateResult(
            passed=False, gate="gate2_llm_review",
            details=f"SDK error during review: {e}",
        )

    result_text = result.result if result and result.result else ""
    if not result_text:
        logger.warning("L2 review returned empty result (SDK returned None or empty)")
    return _parse_review_result(result_text)


def _build_review_prompt(
    title: str, description: str, diff: str,
    prior_feedback: str | None = None,
) -> str:
    parts = [
        f"Task: {title}\n",
        f"Description: {description}\n\n",
        f"Git diff of changes:\n```diff\n{diff}\n```\n\n",
    ]
    if prior_feedback:
        parts.append(
            "=== PRIOR REVIEW CONTEXT ===\n"
            "A previous reviewer rejected this code with the following feedback:\n"
            f"---\n{prior_feedback}\n---\n\n"
            "The developer has attempted to fix these issues. Your PRIMARY job is to:\n"
            "1. Verify that the specific issues above were actually fixed\n"
            "2. Only flag NEW issues if they are genuine bugs or security concerns\n"
            "3. Do NOT invent new stylistic complaints — focus on the prior feedback\n\n"
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

    # 3. Fallback: verdict appears anywhere (e.g. "Verdict: PASS")
    if "PASS" in upper and "FAIL" not in upper:
        return GateResult(passed=True, gate="gate2_llm_review", details=text)
    if "FAIL" in upper and "PASS" not in upper:
        return GateResult(passed=False, gate="gate2_llm_review", details=text)

    return GateResult(
        passed=False,
        gate="gate2_llm_review",
        details=f"Unclear review response (treating as fail): {text[:200]}",
    )


def get_diff(worktree_path: str) -> str:
    """Get the git diff for changes in a worktree."""
    result = subprocess.run(
        ["git", "diff", "HEAD~1", "--", "."],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "diff", "--cached", "--", "."],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
    return result.stdout
