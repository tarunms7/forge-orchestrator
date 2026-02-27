"""Gate 2: LLM code review. A fresh Claude instance reviews changes against the task spec."""

import subprocess

from claude_code_sdk import ClaudeCodeOptions, ResultMessage, query

from forge.review.pipeline import GateResult

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
) -> GateResult:
    """Run LLM code review on the given diff against the task spec."""
    if not diff.strip():
        return GateResult(passed=False, gate="gate2_llm_review", details="No changes to review")

    prompt = _build_review_prompt(task_title, task_description, diff)

    options = ClaudeCodeOptions(
        system_prompt=REVIEW_SYSTEM_PROMPT,
        max_turns=1,
        model=model,
    )
    if worktree_path:
        options.cwd = worktree_path

    result_text = ""
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            if message.result:
                result_text = message.result
            break

    return _parse_review_result(result_text)


def _build_review_prompt(title: str, description: str, diff: str) -> str:
    return (
        f"Task: {title}\n"
        f"Description: {description}\n\n"
        f"Git diff of changes:\n```diff\n{diff}\n```\n\n"
        "Review this code. Respond with PASS or FAIL."
    )


def _parse_review_result(text: str) -> GateResult:
    text = text.strip()
    upper = text.upper()
    if upper.startswith("PASS"):
        return GateResult(passed=True, gate="gate2_llm_review", details=text)
    if upper.startswith("FAIL"):
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
