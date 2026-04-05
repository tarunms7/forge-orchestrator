"""Tier 3 review: per-chunk LLM review and synthesis aggregation.

Each chunk is reviewed independently by a provider agent producing structured
JSON. A final synthesis call aggregates findings into PASS/FAIL/UNCERTAIN.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from forge.providers import (
    ExecutionMode,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.restrictions import REVIEWER_TOOL_POLICY
from forge.review.pipeline import GateResult, ReviewCostInfo
from forge.review.strategy import DiffChunk, FileRiskScore, extract_interface_context

if TYPE_CHECKING:
    from forge.providers.registry import ProviderRegistry

logger = logging.getLogger("forge.review")


# ── Data structures ────────────────────────────────────────────────────────


@dataclass
class ChunkReviewResult:
    """Result of reviewing a single DiffChunk."""

    chunk_index: int
    verdict: str  # "PASS", "FAIL", "UNCERTAIN"
    confidence: int  # 1–5
    issues: list[dict]  # [{severity, file, line_hint, description}]
    cross_chunk_concerns: list[str]
    summary: str
    cost_info: ReviewCostInfo = field(default_factory=ReviewCostInfo)
    raw_text: str = ""
    timed_out: bool = False  # True if SDK/timeout failure (not a review verdict)


# ── System prompts ────────────────────────────────────────────────────────

CHUNK_REVIEW_SYSTEM_PROMPT = """You are a senior code reviewer. You are reviewing ONE CHUNK of a
larger diff — other files will be reviewed separately. Focus only on the files in your chunk.

You MUST respond with valid JSON matching this exact schema:
{
  "verdict": "PASS" | "FAIL" | "UNCERTAIN",
  "confidence": <integer 1-5>,
  "issues": [
    {
      "severity": "HIGH" | "MEDIUM" | "LOW",
      "file": "<file path>",
      "line_hint": "<approximate line, e.g. ~42>",
      "description": "<clear, specific description>"
    }
  ],
  "cross_chunk_concerns": ["<concern about something in another file not in your chunk>"],
  "summary": "<one sentence summary of your finding>"
}

Confidence scale:
  5 = Completely confident (code clearly correct OR clearly broken)
  4 = Confident (strong assessment, minor residual doubt)
  3 = Reasonably confident (sound assessment, some assumptions)
  2 = Low (significant uncertainty, may have missed context)
  1 = Very low (could not properly evaluate)

Review checklist (check ALL for your chunk files):
1. CORRECTNESS — Logic errors, off-by-one, wrong conditions, edge cases
2. ERROR HANDLING — Exceptions caught at right level, resources cleaned up
3. SECURITY — Input validation, secrets safe, no path traversal
4. CONCURRENCY — Race conditions, async operations awaited, mutable state safe
5. DESIGN — Single responsibility, no obvious N+1 queries, right abstraction level

Rules:
- FAIL only for real bugs. Not style preferences.
- UNCERTAIN when code might be correct but depends on context you cannot see.
- Do NOT fail for missing integration code that belongs to sibling chunks.
- Be specific: file path + approximate line for every issue."""

SYNTHESIS_SYSTEM_PROMPT = """You are synthesizing the results of a multi-chunk code review.
You will receive the findings from each chunk reviewer. Your job is to:
1. Produce a final PASS/FAIL/UNCERTAIN verdict
2. Write consolidated, human-readable feedback (not just a list of the chunk results)
3. Surface any cross-chunk concerns that multiple reviewers flagged

Respond with EXACTLY one of:
PASS: <explanation>
FAIL: <specific issues with file paths>
UNCERTAIN: <what you need to resolve>

Rules:
- If any chunk has a FAIL with confidence ≥ 3, the overall verdict is FAIL.
- If any chunk has a FAIL with confidence ≤ 2, treat as UNCERTAIN.
- If any chunk is UNCERTAIN, the overall verdict is UNCERTAIN.
- If all chunks PASS but any has confidence ≤ 2, the overall verdict is UNCERTAIN.
- Be concise. Don't repeat all chunk details — just the important issues."""


# ── JSON parsing ──────────────────────────────────────────────────────────


def _parse_chunk_json(raw_text: str, chunk_index: int) -> ChunkReviewResult:
    """Parse a chunk reviewer's JSON response.

    Falls back to plain-text PASS/FAIL/UNCERTAIN detection for non-JSON responses.
    """
    text = raw_text.strip()

    # Try to parse JSON (may be wrapped in markdown code fence)
    json_text = text
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
        if m:
            json_text = m.group(1).strip()

    try:
        data = json.loads(json_text)
        verdict = str(data.get("verdict", "UNCERTAIN")).upper()
        if verdict not in ("PASS", "FAIL", "UNCERTAIN"):
            verdict = "UNCERTAIN"
        return ChunkReviewResult(
            chunk_index=chunk_index,
            verdict=verdict,
            confidence=max(1, min(5, int(data.get("confidence", 3)))),
            issues=data.get("issues", []),
            cross_chunk_concerns=data.get("cross_chunk_concerns", []),
            summary=str(data.get("summary", "")),
            raw_text=raw_text,
        )
    except (json.JSONDecodeError, ValueError, TypeError):
        # Fallback: plain-text verdict detection
        upper = text.upper()
        if upper.startswith("PASS"):
            verdict = "PASS"
        elif upper.startswith("FAIL"):
            verdict = "FAIL"
        elif upper.startswith("UNCERTAIN"):
            verdict = "UNCERTAIN"
        else:
            # Check line-by-line
            verdict = "UNCERTAIN"
            for line in text.splitlines():
                lu = line.strip().upper()
                if lu.startswith("PASS"):
                    verdict = "PASS"
                    break
                if lu.startswith("FAIL"):
                    verdict = "FAIL"
                    break
                if lu.startswith("UNCERTAIN"):
                    verdict = "UNCERTAIN"
                    break
        return ChunkReviewResult(
            chunk_index=chunk_index,
            verdict=verdict,
            confidence=3,
            issues=[],
            cross_chunk_concerns=[],
            summary=text[:200],
            raw_text=raw_text,
        )


# ── Synthesis helpers ─────────────────────────────────────────────────────


def _apply_synthesis_rules(
    results: list[ChunkReviewResult],
) -> tuple[str, str]:
    """Determine overall verdict from chunk results.

    Returns (verdict, reason_string).

    Priority order (from spec):
    1. FAIL with confidence >= 3 -> FAIL (confirmed defect beats uncertainty)
    2. FAIL with confidence <= 2 -> UNCERTAIN (low-confidence FAIL is uncertain)
    3. Any UNCERTAIN -> UNCERTAIN
    4. All PASS but any confidence <= 2 -> UNCERTAIN
    5. All PASS with confidence >= 3 -> PASS
    """
    if not results:
        return "UNCERTAIN", "No chunk results to aggregate."

    # Check for high-confidence FAILs first — a confirmed defect blocks regardless
    for r in results:
        if r.verdict == "FAIL":
            if r.confidence >= 3:
                return "FAIL", f"Chunk {r.chunk_index} FAIL (confidence {r.confidence}/5)."
            else:
                return "UNCERTAIN", (
                    f"Chunk {r.chunk_index} FAIL with low confidence ({r.confidence}/5) — "
                    "treating as UNCERTAIN."
                )

    # Then check for UNCERTAIN
    for r in results:
        if r.verdict == "UNCERTAIN":
            return (
                "UNCERTAIN",
                f"Chunk {r.chunk_index} verdict is UNCERTAIN (confidence {r.confidence}).",
            )

    # All PASS — check confidence
    low_conf = [r for r in results if r.confidence <= 2]
    if low_conf:
        return "UNCERTAIN", (
            f"All chunks PASS but chunk(s) {[r.chunk_index for r in low_conf]} "
            f"have low confidence (≤ 2/5)."
        )

    return "PASS", "All chunks passed with adequate confidence."


def _deduplicate_issues(issues: list[dict]) -> list[dict]:
    """Deduplicate issues by (file, line_hint, description) key."""
    seen: set[tuple] = set()
    unique: list[dict] = []
    for issue in issues:
        key = (
            issue.get("file", ""),
            issue.get("line_hint", ""),
            issue.get("description", "")[:80],
        )
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def _format_chunks_for_synthesis(
    chunks: list[DiffChunk],
    results: list[ChunkReviewResult],
) -> str:
    """Format all chunk findings as a readable block for the synthesis prompt."""
    lines = ["## Chunk Review Findings\n"]
    chunk_by_index = {c.index: c for c in chunks}

    for result in results:
        chunk = chunk_by_index.get(result.chunk_index)
        file_list = ", ".join(chunk.files[:4]) if chunk else f"chunk {result.chunk_index}"
        if chunk and len(chunk.files) > 4:
            file_list += f" (+{len(chunk.files) - 4} more)"
        risk = f" [{chunk.risk_label}]" if chunk else ""
        total_chunks = chunks[-1].total if chunks else (results[-1].chunk_index if results else "?")

        lines.append(f"**Chunk {result.chunk_index}/{total_chunks}**{risk}: {file_list}")
        lines.append(f"  Verdict: {result.verdict} (confidence {result.confidence}/5)")
        lines.append(f"  Summary: {result.summary}")

        if result.issues:
            lines.append("  Issues:")
            for issue in result.issues:
                lines.append(
                    f"    - [{issue.get('severity', '?')}] "
                    f"{issue.get('file', '?')} {issue.get('line_hint', '')}: "
                    f"{issue.get('description', '')}"
                )

        if result.cross_chunk_concerns:
            lines.append("  Cross-chunk concerns:")
            for concern in result.cross_chunk_concerns:
                lines.append(f"    - {concern}")
        lines.append("")

    return "\n".join(lines)


# ── Per-chunk review ──────────────────────────────────────────────────────


async def review_chunk(
    chunk: DiffChunk,
    task_title: str,
    task_description: str,
    all_file_scores: list[FileRiskScore],
    full_diff: str,
    *,
    model: str | ModelSpec = "sonnet",
    worktree_path: str | None = None,
    sibling_context: str | None = None,
    prior_feedback: str | None = None,
    on_message: Callable[[Any], Awaitable[None]] | None = None,
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,
    registry: ProviderRegistry | None = None,
) -> ChunkReviewResult:
    """Run one provider review call for a single DiffChunk.

    Returns a ChunkReviewResult. On provider error or timeout, retries once
    before returning a timed_out=True result.
    """
    if on_review_event:
        await on_review_event(
            "review:chunk_started",
            {
                "chunk_index": chunk.index,
                "chunk_total": chunk.total,
                "files": chunk.files,
                "risk_label": chunk.risk_label,
            },
        )

    interface_ctx = extract_interface_context(chunk, all_file_scores, full_diff)

    # Build prompt
    parts = []
    if sibling_context:
        parts.append(f"{sibling_context}\n\n")
    parts.append(f"Task: {task_title}\nDescription: {task_description}\n\n")
    if interface_ctx:
        parts.append(f"{interface_ctx}\n\n")
    parts.append(
        f"This is chunk {chunk.index} of {chunk.total}. "
        f"Files in your chunk: {', '.join(chunk.files)}.\n"
        "Other files will be reviewed in separate chunks. "
        "Only report issues for files listed above.\n\n"
    )
    parts.append(f"Git diff for your chunk:\n```diff\n{chunk.diff_text}\n```\n\n")
    if prior_feedback:
        truncated = prior_feedback[:2000]
        parts.append(
            "=== PRIOR REVIEW FEEDBACK ===\n"
            "A previous review flagged these issues — verify they are fixed in your "
            "chunk's files if any are mentioned:\n"
            f"---\n{truncated}\n---\n\n"
        )
    parts.append("Respond with JSON matching the required schema.")

    prompt = "".join(parts)

    model_spec = ModelSpec.parse(model) if isinstance(model, str) else model

    if registry is None:
        # Fallback: construct a temporary registry with ClaudeProvider
        try:
            from forge.config.settings import ForgeSettings
            from forge.providers.claude import ClaudeProvider
            from forge.providers.registry import ProviderRegistry as _PR

            registry = _PR(ForgeSettings())
            registry.register(ClaudeProvider())
        except Exception:
            return ChunkReviewResult(
                chunk_index=chunk.index,
                verdict="UNCERTAIN",
                confidence=1,
                issues=[],
                cross_chunk_concerns=[],
                summary="ProviderRegistry not available",
                timed_out=True,
            )

    provider = registry.get_for_model(model_spec)
    catalog_entry = registry.get_catalog_entry(model_spec)
    workspace = WorkspaceRoots(primary_cwd=worktree_path or ".")

    def _on_event(event: ProviderEvent) -> None:
        if on_message is not None:
            asyncio.ensure_future(on_message(event))

    cost_info = ReviewCostInfo()
    max_attempts = 2

    for attempt in range(1, max_attempts + 1):
        try:
            handle = provider.start(
                prompt=prompt,
                system_prompt=CHUNK_REVIEW_SYSTEM_PROMPT,
                catalog_entry=catalog_entry,
                execution_mode=ExecutionMode.INTELLIGENCE,
                tool_policy=REVIEWER_TOOL_POLICY,
                output_contract=OutputContract(format="json"),
                workspace=workspace,
                max_turns=40,
                on_event=_on_event,
            )
            result = await asyncio.wait_for(handle.result(), timeout=600)
        except (TimeoutError, Exception) as exc:
            logger.warning(
                "Chunk %d/%d review failed on attempt %d/%d: %s",
                chunk.index,
                chunk.total,
                attempt,
                max_attempts,
                exc,
            )
            if attempt == max_attempts:
                chunk_result = ChunkReviewResult(
                    chunk_index=chunk.index,
                    verdict="UNCERTAIN",
                    confidence=1,
                    issues=[],
                    cross_chunk_concerns=[],
                    summary=f"Review failed: {exc}",
                    cost_info=cost_info,
                    raw_text="",
                    timed_out=True,
                )
                if on_review_event:
                    await on_review_event(
                        "review:chunk_complete",
                        {
                            "chunk_index": chunk.index,
                            "chunk_total": chunk.total,
                            "verdict": "TIMEOUT",
                            "issue_count": 0,
                            "confidence": 1,
                        },
                    )
                return chunk_result
            await asyncio.sleep(2**attempt + random.uniform(0, 1))
            continue

        # Always accumulate cost
        if result.provider_reported_cost_usd is not None:
            cost_info.add(
                ReviewCostInfo(
                    cost_usd=result.provider_reported_cost_usd,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            )

        raw_text = result.text or ""
        if not raw_text:
            if attempt == max_attempts:
                chunk_result = ChunkReviewResult(
                    chunk_index=chunk.index,
                    verdict="UNCERTAIN",
                    confidence=1,
                    issues=[],
                    cross_chunk_concerns=[],
                    summary="Empty response from reviewer",
                    cost_info=cost_info,
                    raw_text="",
                    timed_out=True,
                )
                if on_review_event:
                    await on_review_event(
                        "review:chunk_complete",
                        {
                            "chunk_index": chunk.index,
                            "chunk_total": chunk.total,
                            "verdict": "TIMEOUT",
                            "issue_count": 0,
                            "confidence": 1,
                        },
                    )
                return chunk_result
            await asyncio.sleep(2**attempt + random.uniform(0, 1))
            continue

        chunk_result = _parse_chunk_json(raw_text, chunk.index)
        chunk_result.cost_info = cost_info

        if on_review_event:
            await on_review_event(
                "review:chunk_complete",
                {
                    "chunk_index": chunk.index,
                    "chunk_total": chunk.total,
                    "verdict": chunk_result.verdict,
                    "issue_count": len(chunk_result.issues),
                    "confidence": chunk_result.confidence,
                },
            )
        return chunk_result

    # Should never reach here
    return ChunkReviewResult(
        chunk_index=chunk.index,
        verdict="UNCERTAIN",
        confidence=1,
        issues=[],
        cross_chunk_concerns=[],
        summary="Unreachable fallback",
        cost_info=cost_info,
        raw_text="",
        timed_out=True,
    )


# ── Synthesis ─────────────────────────────────────────────────────────────


async def synthesize_results(
    chunks: list[DiffChunk],
    chunk_results: list[ChunkReviewResult],
    task_title: str,
    task_description: str,
    *,
    model: str | ModelSpec = "sonnet",
    worktree_path: str | None = None,
    prior_feedback: str | None = None,
    delta_diff: str | None = None,
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,
    registry: ProviderRegistry | None = None,
) -> tuple[GateResult, ReviewCostInfo]:
    """Aggregate chunk review results into a final GateResult.

    First applies deterministic synthesis rules.
    Then runs a synthesis LLM call for consolidated human-readable feedback.
    On synthesis failure, falls back to rule-based verdict with raw chunk summaries.
    """
    # Check for any timed-out chunks — escalate to human immediately
    timed_out = [r for r in chunk_results if r.timed_out]
    if timed_out:
        failed_files: list[str] = []
        for r in timed_out:
            chunk = next((c for c in chunks if c.index == r.chunk_index), None)
            if chunk:
                failed_files.extend(chunk.files[:3])
        details = (
            f"Chunk review failed for {len(timed_out)} chunk(s). "
            f"Affected files: {', '.join(failed_files[:6])}. "
            "Cannot produce a reliable review without all chunk results."
        )
        total_cost = ReviewCostInfo()
        for r in chunk_results:
            total_cost.add(r.cost_info)
        return (
            GateResult(
                passed=False,
                gate="gate2_llm_review",
                details=details,
                needs_human=True,
                review_strategy="tier3",
                chunk_count=len(chunks),
                chunk_verdicts=[r.verdict for r in chunk_results],
            ),
            total_cost,
        )

    # Apply deterministic verdict rules
    pre_verdict, pre_reason = _apply_synthesis_rules(chunk_results)

    # Emit synthesis_started event
    total_issues = sum(len(r.issues) for r in chunk_results)
    if on_review_event:
        await on_review_event(
            "review:synthesis_started",
            {"chunk_count": len(chunks), "total_issues": total_issues},
        )

    # Build synthesis prompt
    chunk_summary = _format_chunks_for_synthesis(chunks, chunk_results)
    all_cross = list({c for r in chunk_results for c in r.cross_chunk_concerns})

    parts = [
        f"Task: {task_title}\nDescription: {task_description}\n\n",
        chunk_summary,
    ]
    if all_cross:
        parts.append("## Cross-Chunk Concerns\n" + "\n".join(f"- {c}" for c in all_cross) + "\n\n")
    if prior_feedback:
        parts.append(f"=== PRIOR REVIEW FEEDBACK ===\n{prior_feedback[:3000]}\n\n")
    if delta_diff:
        parts.append(f"=== CHANGES SINCE LAST REVIEW ===\n```diff\n{delta_diff[:6000]}\n```\n\n")
    parts.append(
        f"Pre-analysis: {pre_verdict} ({pre_reason})\n\n"
        "Produce the final PASS/FAIL/UNCERTAIN verdict with consolidated feedback."
    )

    prompt = "".join(parts)

    total_cost = ReviewCostInfo()
    for r in chunk_results:
        total_cost.add(r.cost_info)

    # Import here (not at module level) to avoid circular imports
    from forge.review.llm_review import _parse_review_result

    if registry is None:
        # Fallback: construct a temporary registry with ClaudeProvider
        try:
            from forge.config.settings import ForgeSettings
            from forge.providers.claude import ClaudeProvider
            from forge.providers.registry import ProviderRegistry as _PR

            registry = _PR(ForgeSettings())
            registry.register(ClaudeProvider())
        except Exception:
            return _synthesis_fallback(chunk_results, chunks, pre_verdict, total_cost)

    model_spec = ModelSpec.parse(model) if isinstance(model, str) else model
    provider = registry.get_for_model(model_spec)
    catalog_entry = registry.get_catalog_entry(model_spec)
    workspace = WorkspaceRoots(primary_cwd=worktree_path or ".")

    # Synthesis uses no tools — empty allowlist
    synthesis_tool_policy = ToolPolicy(mode="allowlist", allowed_tools=[])

    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            handle = provider.start(
                prompt=prompt,
                system_prompt=SYNTHESIS_SYSTEM_PROMPT,
                catalog_entry=catalog_entry,
                execution_mode=ExecutionMode.INTELLIGENCE,
                tool_policy=synthesis_tool_policy,
                output_contract=OutputContract(format="freeform"),
                workspace=workspace,
                max_turns=5,
            )
            result = await asyncio.wait_for(handle.result(), timeout=120)
        except (TimeoutError, Exception) as exc:
            logger.warning("Synthesis attempt %d/%d failed: %s", attempt, max_attempts, exc)
            if attempt == max_attempts:
                return _synthesis_fallback(chunk_results, chunks, pre_verdict, total_cost)
            await asyncio.sleep(2**attempt)
            continue

        # Always accumulate cost
        if result.provider_reported_cost_usd is not None:
            total_cost.add(
                ReviewCostInfo(
                    cost_usd=result.provider_reported_cost_usd,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            )
        raw = result.text or ""
        if not raw:
            if attempt == max_attempts:
                return _synthesis_fallback(chunk_results, chunks, pre_verdict, total_cost)
            await asyncio.sleep(2**attempt)
            continue

        gate_result = _parse_review_result(raw)
        # Override needs_human: UNCERTAIN from synthesis always goes to human
        if pre_verdict == "UNCERTAIN" or gate_result.needs_human:
            gate_result.needs_human = True
        gate_result.review_strategy = "tier3"
        gate_result.chunk_count = len(chunks)
        gate_result.chunk_verdicts = [r.verdict for r in chunk_results]
        return gate_result, total_cost

    return _synthesis_fallback(chunk_results, chunks, pre_verdict, total_cost)


def _synthesis_fallback(
    chunk_results: list[ChunkReviewResult],
    chunks: list[DiffChunk],
    pre_verdict: str,
    total_cost: ReviewCostInfo,
) -> tuple[GateResult, ReviewCostInfo]:
    """Fallback when synthesis LLM call fails: use rule-based verdict."""
    summaries = "\n".join(
        f"Chunk {r.chunk_index}: {r.verdict} (conf {r.confidence}) — {r.summary}"
        for r in chunk_results
    )
    details = (
        f"[Synthesis LLM failed — rule-based verdict: {pre_verdict}]\n\n"
        f"Chunk summaries:\n{summaries}"
    )
    passed = pre_verdict == "PASS"
    needs_human = pre_verdict == "UNCERTAIN"
    return (
        GateResult(
            passed=passed,
            gate="gate2_llm_review",
            details=details,
            needs_human=needs_human,
            review_strategy="tier3",
            chunk_count=len(chunks),
            chunk_verdicts=[r.verdict for r in chunk_results],
        ),
        total_cost,
    )


# ── Full Tier 3 orchestration ─────────────────────────────────────────────


async def run_chunked_review(
    chunks: list[DiffChunk],
    all_file_scores: list[FileRiskScore],
    full_diff: str,
    task_title: str,
    task_description: str,
    *,
    model: str | ModelSpec = "sonnet",
    worktree_path: str | None = None,
    sibling_context: str | None = None,
    prior_feedback: str | None = None,
    delta_diff: str | None = None,
    on_message: Callable[[Any], Awaitable[None]] | None = None,
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,
    registry: ProviderRegistry | None = None,
) -> tuple[GateResult, ReviewCostInfo]:
    """Run all chunk reviews sequentially then synthesize.

    Sequential (not parallel) to avoid rate-limit floods on large diffs.
    """
    chunk_results: list[ChunkReviewResult] = []

    for chunk_pos, chunk in enumerate(chunks):
        result = await review_chunk(
            chunk,
            task_title,
            task_description,
            all_file_scores,
            full_diff,
            model=model,
            worktree_path=worktree_path,
            sibling_context=sibling_context,
            prior_feedback=prior_feedback,
            on_message=on_message,
            on_review_event=on_review_event,
            registry=registry,
        )
        chunk_results.append(result)

        # Abort early if a chunk timed out — no point reviewing more chunks
        if result.timed_out:
            # Fill remaining chunks with placeholder results
            for remaining_chunk in chunks[chunk_pos + 1 :]:
                chunk_results.append(
                    ChunkReviewResult(
                        chunk_index=remaining_chunk.index,
                        verdict="UNCERTAIN",
                        confidence=1,
                        issues=[],
                        cross_chunk_concerns=[],
                        summary="Skipped due to prior chunk timeout.",
                        timed_out=True,
                    )
                )
            break

    return await synthesize_results(
        chunks,
        chunk_results,
        task_title,
        task_description,
        model=model,
        worktree_path=worktree_path,
        prior_feedback=prior_feedback,
        delta_diff=delta_diff,
        on_review_event=on_review_event,
        registry=registry,
    )
