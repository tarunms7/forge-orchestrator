"""Gate 2: LLM code review. Uses provider protocol to review changes against the task spec."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from forge.config.settings import ForgeSettings
from forge.core.provider_config import ensure_provider_registry
from forge.core.sdk_helpers import (
    sdk_query,  # noqa: F401 - backward-compatible export for tests/mocks.
)
from forge.providers import (
    ExecutionMode,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    WorkspaceRoots,
)
from forge.providers.restrictions import REVIEWER_TOOL_POLICY

# ReviewCostInfo lives in pipeline.py to avoid circular imports.
# Re-exported here for backward compatibility with existing callers.
from forge.review.pipeline import (
    GateResult,
    ReviewCostInfo,  # noqa: F401
)

if TYPE_CHECKING:
    from forge.providers.registry import ProviderRegistry

logger = logging.getLogger("forge.review")


REVIEW_SYSTEM_PROMPT = """You are a senior code reviewer. Your job is to catch bugs, security issues,
and design problems that would cause production incidents. You are the last
line of defense before code ships.

You will receive a task specification and a git diff. Review the code
thoroughly and respond with EXACTLY one of:

PASS: <explanation covering what you verified>
FAIL: <specific issues with file paths and line references>
UNCERTAIN: <specific concerns you cannot resolve from the diff alone>

The first non-empty line MUST start with PASS:, FAIL:, or UNCERTAIN:.
Do NOT wrap the verdict in markdown, bullets, headings, or code fences.

## When to use UNCERTAIN
- You see code that MIGHT be correct but depends on context you don't have
- The task spec is ambiguous and the code matches ONE valid interpretation
- You found something suspicious but can't confirm it's a bug without seeing the caller
- The diff is too large to review thoroughly and you need human guidance

Do NOT use UNCERTAIN for:
- Code that is clearly wrong — use FAIL
- Code that is clearly correct — use PASS
- Style preferences — use PASS (not your job)

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
  do NOT fail for missing integration code that belongs to a sibling task's scope.
- You do NOT have live git history or branch state. Do NOT claim you ran `git`
  commands, inspected other branches, or verified repository state unless that
  evidence appears in the provided diff or files you actually read.
- You have workspace tools available: Read, Glob, Grep, and Bash.
- Read the current version of each changed in-scope source file before issuing a
  PASS or FAIL verdict.
- Use Grep/Glob/Read to inspect nearby callers, tests, and related files whenever
  that would materially reduce uncertainty.
- Use Bash for focused verification commands (targeted tests, linters, or grep)
  when it helps confirm or falsify a concern. Prefer the smallest relevant
  command instead of broad expensive suites.
- If validation context says tests/build/lint were skipped or had infra problems,
  treat that as reduced coverage and inspect the changed code more deeply.
- Prior review feedback can be stale on retries. If prior feedback says an
  in-scope deliverable is missing, you MUST read the current file before
  failing for that reason.
- The current in-scope files and the FULL DIFF are more trustworthy than prior
  feedback. Do not repeat a prior failure blindly without re-checking."""


async def gate2_llm_review(
    task_title: str,
    task_description: str,
    diff: str,
    worktree_path: str | None = None,
    model: str | ModelSpec = "sonnet",
    prior_feedback: str | None = None,
    prior_diff: str | None = None,
    project_context: str = "",
    allowed_files: list[str] | None = None,
    delta_diff: str | None = None,
    sibling_context: str | None = None,
    validation_context: str = "",
    custom_review_focus: str = "",
    prefer_deep_review: bool = False,
    on_message: Callable[[Any], Awaitable[None]] | None = None,
    # NEW: callback for review progress events (strategy_selected, chunk_started, etc.)
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,
    # NEW: adaptive review config (from ReviewConfig)
    adaptive_review: bool = True,
    medium_diff_threshold: int = 400,
    large_diff_threshold: int = 2000,
    max_chunk_lines: int = 600,
    registry: ProviderRegistry | None = None,
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

    # ── Strategy selection ────────────────────────────────────────────────
    from forge.review.strategy import (
        ReviewStrategy,
        build_diff_chunks,
        count_diff_lines,
        score_files,
        select_strategy,
        should_deepen_small_diff_review,
    )
    from forge.review.synthesizer import run_chunked_review

    file_scores = score_files(diff)
    strategy = select_strategy(
        diff,
        medium_diff_threshold,
        large_diff_threshold,
        adaptive=adaptive_review,
    )
    if adaptive_review and strategy == ReviewStrategy.TIER1:
        if prefer_deep_review or should_deepen_small_diff_review(diff, file_scores=file_scores):
            strategy = ReviewStrategy.TIER2

    # Pre-compute file scores and chunks for Tier 3 (reused for both event payload and review)
    _t3_file_scores = None
    _t2_chunks = None
    _t3_chunks = None
    if strategy == ReviewStrategy.TIER2:
        _t3_file_scores = file_scores
        _t2_chunks = build_diff_chunks(file_scores, diff, max_chunk_lines=1)
    if strategy == ReviewStrategy.TIER3:
        _t3_file_scores = file_scores
        _t3_chunks = build_diff_chunks(_t3_file_scores, diff, max_chunk_lines)

    if on_review_event:
        payload: dict = {
            "strategy": strategy.value,
            "diff_lines": count_diff_lines(diff),
        }
        if strategy == ReviewStrategy.TIER2:
            payload["chunk_count"] = len(_t2_chunks)
        if strategy == ReviewStrategy.TIER3:
            payload["chunk_count"] = len(_t3_chunks)
        await on_review_event("review:strategy_selected", payload)

    # ── Tier 2: per-file / paired-file chunk review + synthesis ──────────────
    if strategy == ReviewStrategy.TIER2:
        return await run_chunked_review(
            _t2_chunks,
            _t3_file_scores,
            diff,
            task_title,
            task_description,
            model=model,
            worktree_path=worktree_path,
            sibling_context=sibling_context,
            prior_feedback=prior_feedback,
            delta_diff=delta_diff,
            validation_context=validation_context,
            on_message=on_message,
            on_review_event=on_review_event,
            registry=registry,
            strategy_label=ReviewStrategy.TIER2.value,
        )

    # ── Tier 3: multi-chunk map-reduce ────────────────────────────────────
    if strategy == ReviewStrategy.TIER3:
        return await run_chunked_review(
            _t3_chunks,
            _t3_file_scores,
            diff,
            task_title,
            task_description,
            model=model,
            worktree_path=worktree_path,
            sibling_context=sibling_context,
            prior_feedback=prior_feedback,
            delta_diff=delta_diff,
            validation_context=validation_context,
            on_message=on_message,
            on_review_event=on_review_event,
            registry=registry,
            strategy_label=ReviewStrategy.TIER3.value,
        )

    prompt = _build_review_prompt(
        task_title,
        task_description,
        diff,
        prior_feedback,
        prior_diff=prior_diff,
        project_context=project_context,
        allowed_files=allowed_files,
        delta_diff=delta_diff,
        sibling_context=sibling_context,
        validation_context=validation_context,
    )

    system_prompt = REVIEW_SYSTEM_PROMPT
    if custom_review_focus:
        system_prompt += "\n\n" + custom_review_focus

    # Resolve provider and catalog entry
    model_spec = ModelSpec.parse(model) if isinstance(model, str) else model

    review_timeout_seconds = 600  # 10 min — same as agent timeout
    max_review_attempts = 2

    if registry is None and model_spec.provider == "claude":
        from claude_code_sdk import ClaudeCodeOptions

        for attempt in range(1, max_review_attempts + 1):
            try:
                sdk_result = await asyncio.wait_for(
                    sdk_query(
                        prompt=prompt,
                        options=ClaudeCodeOptions(
                            system_prompt=system_prompt,
                            max_turns=75,
                            model=model_spec.model,
                        ),
                        on_message=on_message,
                    ),
                    timeout=review_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "L2 review timed out after %ds (attempt %d/%d)",
                    review_timeout_seconds,
                    attempt,
                    max_review_attempts,
                )
                if on_review_event:
                    await on_review_event(
                        "review:timeout",
                        {
                            "timeout_seconds": review_timeout_seconds,
                            "attempt": attempt,
                            "max_attempts": max_review_attempts,
                        },
                    )
                if attempt == max_review_attempts:
                    return (
                        GateResult(
                            passed=False,
                            gate="gate2_llm_review",
                            details=f"Review timed out after {max_review_attempts} attempts",
                            retriable=True,
                            review_strategy=strategy.value,
                        ),
                        cost_info,
                    )
                if on_review_event:
                    await on_review_event(
                        "review:retry",
                        {
                            "attempt": attempt + 1,
                            "max_attempts": max_review_attempts,
                            "reason": "timeout",
                        },
                    )
                continue
            except Exception as exc:
                logger.warning(
                    "L2 review SDK call failed (attempt %d/%d): %s",
                    attempt,
                    max_review_attempts,
                    exc,
                )
                if attempt == max_review_attempts:
                    return (
                        GateResult(
                            passed=False,
                            gate="gate2_llm_review",
                            details=f"Provider error during review after {max_review_attempts} attempts: {exc}",
                            retriable=True,
                            review_strategy=strategy.value,
                        ),
                        cost_info,
                    )
                if on_review_event:
                    await on_review_event(
                        "review:retry",
                        {
                            "attempt": attempt + 1,
                            "max_attempts": max_review_attempts,
                            "reason": "error",
                        },
                    )
                continue

            if sdk_result is not None:
                cost_info.cost_usd += sdk_result.cost_usd
                cost_info.input_tokens += sdk_result.input_tokens
                cost_info.output_tokens += sdk_result.output_tokens

            result_text = (sdk_result.result if sdk_result else "").strip()
            if result_text:
                result_gate = _parse_review_result(result_text)
                result_gate.review_strategy = strategy.value
                return result_gate, cost_info

            logger.warning(
                "L2 review returned empty result (attempt %d/%d)",
                attempt,
                max_review_attempts,
            )
            if attempt < max_review_attempts:
                await asyncio.sleep(2**attempt + random.uniform(0, 1))

        logger.warning(
            "L2 review returned empty after %d attempts — escalating to human",
            max_review_attempts,
        )
        return (
            GateResult(
                passed=False,
                gate="gate2_llm_review",
                details=(
                    f"Review could not complete after {max_review_attempts} attempts "
                    "(likely transient provider issue). Human review needed."
                ),
                needs_human=True,
                review_strategy=strategy.value,
            ),
            cost_info,
        )

    registry = ensure_provider_registry(registry, settings=ForgeSettings())
    if registry is None:
        logger.error("Failed to create fallback ProviderRegistry for gate2_llm_review")
        return (
            GateResult(
                passed=False,
                gate="gate2_llm_review",
                details="Internal error: ProviderRegistry not available",
                review_strategy=strategy.value,
            ),
            cost_info,
        )

    provider = registry.get_for_model(model_spec)
    catalog_entry = registry.get_catalog_entry(model_spec)
    workspace = WorkspaceRoots(primary_cwd=worktree_path or ".")

    def _on_event(event: ProviderEvent) -> None:
        if on_message is not None:
            asyncio.ensure_future(on_message(event))

    # Retry the provider call if the result is empty.
    for attempt in range(1, max_review_attempts + 1):
        try:
            handle = provider.start(
                prompt=prompt,
                system_prompt=system_prompt,
                catalog_entry=catalog_entry,
                execution_mode=ExecutionMode.INTELLIGENCE,
                tool_policy=REVIEWER_TOOL_POLICY,
                output_contract=OutputContract(format="freeform"),
                workspace=workspace,
                max_turns=75,
                reasoning_effort=registry.settings.resolve_reasoning_effort(
                    "reviewer",
                    "medium",
                ),
                on_event=_on_event,
            )
            result = await asyncio.wait_for(
                handle.result(),
                timeout=review_timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "L2 review timed out after %ds (attempt %d/%d)",
                review_timeout_seconds,
                attempt,
                max_review_attempts,
            )
            if on_review_event:
                await on_review_event(
                    "review:timeout",
                    {
                        "timeout_seconds": review_timeout_seconds,
                        "attempt": attempt,
                        "max_attempts": max_review_attempts,
                    },
                )
            if attempt == max_review_attempts:
                return (
                    GateResult(
                        passed=False,
                        gate="gate2_llm_review",
                        details=f"Review timed out after {max_review_attempts} attempts",
                        retriable=True,
                        review_strategy=strategy.value,
                    ),
                    cost_info,
                )
            if on_review_event:
                await on_review_event(
                    "review:retry",
                    {
                        "attempt": attempt + 1,
                        "max_attempts": max_review_attempts,
                        "reason": "timeout",
                    },
                )
            continue
        except Exception as e:
            logger.warning(
                "L2 review provider call failed (attempt %d/%d): %s",
                attempt,
                max_review_attempts,
                e,
            )
            if attempt == max_review_attempts:
                return (
                    GateResult(
                        passed=False,
                        gate="gate2_llm_review",
                        details=f"Provider error during review after {max_review_attempts} attempts: {e}",
                        retriable=True,
                        review_strategy=strategy.value,
                    ),
                    cost_info,
                )
            if on_review_event:
                await on_review_event(
                    "review:retry",
                    {
                        "attempt": attempt + 1,
                        "max_attempts": max_review_attempts,
                        "reason": "error",
                    },
                )
            continue

        # Always accumulate cost from provider result
        if result.provider_reported_cost_usd is not None:
            cost_info.cost_usd += result.provider_reported_cost_usd
        cost_info.input_tokens += result.input_tokens
        cost_info.output_tokens += result.output_tokens

        result_text = result.text or ""
        if result_text:
            result_gate = _parse_review_result(result_text)
            result_gate.review_strategy = strategy.value
            return result_gate, cost_info

        # Log diagnostic details to help debug empty responses
        _diag_parts = [f"attempt {attempt}/{max_review_attempts}"]
        _diag_parts.append(f"duration={result.duration_ms}ms")
        logger.warning(
            "L2 review returned empty result (%s)",
            ", ".join(_diag_parts),
        )
        if attempt < max_review_attempts:
            await asyncio.sleep(2**attempt + random.uniform(0, 1))

    # All attempts returned empty — escalate to human
    logger.warning(
        "L2 review returned empty after %d attempts — escalating to human",
        max_review_attempts,
    )
    return (
        GateResult(
            passed=False,
            gate="gate2_llm_review",
            details=f"Review could not complete after {max_review_attempts} attempts (likely transient provider issue). Human review needed.",
            needs_human=True,
            review_strategy=strategy.value,
        ),
        cost_info,
    )


def _build_review_prompt(
    title: str,
    description: str,
    diff: str,
    prior_feedback: str | None = None,
    *,
    prior_diff: str | None = None,
    project_context: str = "",
    allowed_files: list[str] | None = None,
    delta_diff: str | None = None,
    sibling_context: str | None = None,
    validation_context: str = "",
    risk_map_header: str = "",
) -> str:
    parts = []
    if project_context:
        parts.append(f"{project_context}\n\n")
    if sibling_context:
        parts.append(f"{sibling_context}\n\n")
    if validation_context:
        parts.append(f"{validation_context}\n\n")
    parts += [
        f"Task: {title}\n",
        f"Description: {description}\n\n",
    ]
    if allowed_files:
        parts.append(
            f"File scope: This task is ONLY allowed to modify: {', '.join(allowed_files)}.\n"
            "Test files that correspond to in-scope source files "
            "(e.g. `tests/test_<name>.py` or `<name>_test.py`) are also allowed.\n"
            "If the diff contains changes to files outside this list (excluding related test files), "
            "FAIL immediately with 'OUT OF SCOPE' and list the violating files.\n"
            "If prior feedback claims a required in-scope file is missing, read the current file "
            "before failing. Retries may already contain committed fixes from an earlier attempt.\n\n"
        )
    if risk_map_header:
        parts.append(f"{risk_map_header}\n\n")
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
                f"=== PRIOR DIFF (what was rejected) ===\n```diff\n{prior_diff_snippet}\n```\n\n"
            )
        parts.append(
            "The developer has attempted to fix these issues.\n"
            "First, explicitly verify whether each issue above is fixed in the current code.\n"
            "After that, do a full review of the\n"
            "current code. If you find new genuine issues (bugs, security, error handling),\n"
            "FAIL — regardless of whether they were in the prior feedback or not.\n"
            "Prior feedback is context, not a ceiling on what you can flag.\n"
            "Prior feedback may also be stale. If it says a deliverable is missing, re-check the\n"
            "current in-scope file before repeating that claim.\n\n"
        )
    if delta_diff:
        delta_snippet = delta_diff[:6000]
        parts.append(
            "=== CHANGES SINCE LAST REVIEW (DELTA) ===\n"
            "These are the NEW changes the developer added in this retry attempt.\n"
            "IMPORTANT: A delta of zero lines for a file does NOT mean that file is unchanged "
            "from the base — it means the file was already fixed in an earlier commit during "
            "this task's lifetime and was not re-edited in this retry. "
            "The FULL DIFF above is the authoritative source of truth for what the current "
            "code actually contains. If a file appears correct in the full diff, do NOT "
            "complain that it is missing or unchanged.\n"
            f"```diff\n{delta_snippet}\n```\n\n"
        )
    parts.append("Review this code. Respond with PASS, FAIL, or UNCERTAIN.")
    return "".join(parts)


def _parse_review_result(text: str) -> GateResult:
    """Parse the LLM reviewer's response to extract a PASS/FAIL/UNCERTAIN verdict.

    The parser checks for the verdict in three ways (in order):
    1. Text starts with PASS/FAIL/UNCERTAIN (ideal format)
    2. A line starts with PASS/FAIL/UNCERTAIN (verdict buried in analysis)
    3. PASS, FAIL, or UNCERTAIN appears at the start of any line (fallback)

    This flexibility is needed because models (especially opus) often
    write detailed analysis before stating their verdict.

    UNCERTAIN returns needs_human=True so the executor routes it to awaiting_input.
    """
    text = text.strip()
    if not text:
        return GateResult(passed=False, gate="gate2_llm_review", details="Empty review response")

    verdict = _extract_review_verdict(text)
    if verdict == "PASS":
        return GateResult(passed=True, gate="gate2_llm_review", details=text)
    if verdict == "FAIL":
        return GateResult(passed=False, gate="gate2_llm_review", details=text)
    if verdict == "UNCERTAIN":
        return GateResult(passed=False, gate="gate2_llm_review", details=text, needs_human=True)

    return GateResult(
        passed=False,
        gate="gate2_llm_review",
        details=f"Unclear review response (treating as fail): {text[:200]}",
    )


def _extract_review_verdict(text: str) -> str | None:
    """Extract PASS/FAIL/UNCERTAIN from reviewer text.

    Models sometimes wrap the verdict in markdown such as `**PASS:**`,
    `- FAIL:`, or `### UNCERTAIN:` even when explicitly told not to.
    We accept common leading markdown wrappers while still requiring the
    verdict to appear at the start of the text or start of a line.
    """
    verdict = _leading_verdict(text)
    if verdict is not None:
        return verdict

    for line in text.splitlines():
        verdict = _leading_verdict(line)
        if verdict is not None:
            return verdict

    verdict = _labeled_verdict(text)
    if verdict is not None:
        return verdict

    verdict = _boundary_verdict(text)
    if verdict is not None:
        return verdict

    return None


def _leading_verdict(text: str) -> str | None:
    """Return PASS/FAIL/UNCERTAIN if *text* starts with one after markdown trim."""
    candidate = text.strip()
    if not candidate:
        return None

    previous = None
    while candidate and candidate != previous:
        previous = candidate
        candidate = re.sub(r"^(?:[-*+>]+|\d+[.)]|#+)\s*", "", candidate)
        candidate = re.sub(r"^(?:\*\*|__|`{1,3}|[*_~]+)+", "", candidate)
        candidate = candidate.lstrip()

    match = re.match(r"^(PASS|FAIL|UNCERTAIN)\b", candidate, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def _labeled_verdict(text: str) -> str | None:
    """Return PASS/FAIL/UNCERTAIN for explicit final-verdict labels anywhere in text."""
    matches = list(
        re.finditer(
            r"(?i)\b(?:final|overall)?\s*verdict\s*[:\-]\s*(PASS|FAIL|UNCERTAIN)\b",
            text,
        )
    )
    if matches:
        return matches[-1].group(1).upper()
    return None


def _boundary_verdict(text: str) -> str | None:
    """Return a verdict found after a strong boundary near the end of the response.

    This is a last-resort salvage path for providers that concatenate exploratory
    chatter and the final answer into a single blob like:
        "Reviewing files first.PASS: looks good"

    We only accept verdicts after the start of the text, a newline, or sentence-ending
    punctuation to avoid matching ordinary mid-sentence prose like
    "I think this is a FAIL because...".
    """
    tail = text[-4000:]
    matches = list(
        re.finditer(
            r"(?im)(?:^|[\r\n]|(?<=[.!?]))\s*(PASS|FAIL|UNCERTAIN)\b(?::|$)",
            tail,
        )
    )
    if matches:
        return matches[-1].group(1).upper()
    return None
