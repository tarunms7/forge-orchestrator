"""Tests for forge.review.synthesizer — chunk review aggregation."""

from __future__ import annotations

import json

from forge.review.pipeline import GateResult, ReviewCostInfo
from forge.review.strategy import DiffChunk
from forge.review.synthesizer import (
    ChunkReviewResult,
    _apply_synthesis_rules,
    _deduplicate_issues,
    _format_chunks_for_synthesis,
    _parse_chunk_json,
)

# ── _parse_chunk_json ─────────────────────────────────────────────────────


def test_parse_chunk_json_pass():
    raw = json.dumps(
        {
            "verdict": "PASS",
            "confidence": 5,
            "issues": [],
            "cross_chunk_concerns": [],
            "summary": "All good.",
        }
    )
    result = _parse_chunk_json(raw, chunk_index=1)
    assert result.verdict == "PASS"
    assert result.confidence == 5
    assert result.issues == []


def test_parse_chunk_json_fail_with_issues():
    raw = json.dumps(
        {
            "verdict": "FAIL",
            "confidence": 4,
            "issues": [
                {
                    "severity": "HIGH",
                    "file": "foo.py",
                    "line_hint": "~42",
                    "description": "Bad thing",
                }
            ],
            "cross_chunk_concerns": [],
            "summary": "Found a bug.",
        }
    )
    result = _parse_chunk_json(raw, chunk_index=1)
    assert result.verdict == "FAIL"
    assert len(result.issues) == 1
    assert result.issues[0]["severity"] == "HIGH"


def test_parse_chunk_json_fallback_on_bad_json():
    """If response is not JSON, fallback to existing _parse_review_result logic."""
    raw = "FAIL: Missing null check in foo.py line 42."
    result = _parse_chunk_json(raw, chunk_index=1)
    assert result.verdict == "FAIL"


def test_parse_chunk_json_fallback_pass():
    raw = "PASS: All checks passed."
    result = _parse_chunk_json(raw, chunk_index=1)
    assert result.verdict == "PASS"


def test_parse_chunk_json_uncertain():
    raw = json.dumps(
        {
            "verdict": "UNCERTAIN",
            "confidence": 2,
            "issues": [],
            "cross_chunk_concerns": ["Possible issue in sibling file"],
            "summary": "Not sure.",
        }
    )
    result = _parse_chunk_json(raw, chunk_index=1)
    assert result.verdict == "UNCERTAIN"


# ── _apply_synthesis_rules ────────────────────────────────────────────────


def _make_result(verdict: str, confidence: int, issues: list | None = None) -> ChunkReviewResult:
    return ChunkReviewResult(
        chunk_index=1,
        verdict=verdict,
        confidence=confidence,
        issues=issues or [],
        cross_chunk_concerns=[],
        summary="",
        cost_info=ReviewCostInfo(),
        raw_text="",
    )


def test_synthesis_all_pass():
    results = [_make_result("PASS", 5), _make_result("PASS", 4)]
    verdict, _ = _apply_synthesis_rules(results)
    assert verdict == "PASS"


def test_synthesis_any_fail_high_confidence():
    results = [_make_result("PASS", 5), _make_result("FAIL", 4)]
    verdict, _ = _apply_synthesis_rules(results)
    assert verdict == "FAIL"


def test_synthesis_fail_low_confidence_becomes_uncertain():
    results = [_make_result("PASS", 5), _make_result("FAIL", 2)]
    verdict, _ = _apply_synthesis_rules(results)
    assert verdict == "UNCERTAIN"


def test_synthesis_any_uncertain_propagates():
    results = [_make_result("PASS", 5), _make_result("UNCERTAIN", 3)]
    verdict, _ = _apply_synthesis_rules(results)
    assert verdict == "UNCERTAIN"


def test_synthesis_all_pass_low_confidence_is_uncertain():
    results = [_make_result("PASS", 5), _make_result("PASS", 2)]
    verdict, _ = _apply_synthesis_rules(results)
    assert verdict == "UNCERTAIN"


def test_synthesis_fail_high_confidence_beats_uncertain():
    """High-confidence FAIL takes priority over UNCERTAIN from another chunk."""
    results = [_make_result("UNCERTAIN", 3), _make_result("FAIL", 4)]
    verdict, _ = _apply_synthesis_rules(results)
    assert verdict == "FAIL"


def test_synthesis_empty_results():
    """Empty results list returns UNCERTAIN."""
    verdict, reason = _apply_synthesis_rules([])
    assert verdict == "UNCERTAIN"
    assert "No chunk" in reason


def test_parse_chunk_json_null_verdict_becomes_uncertain():
    """JSON with null verdict falls through to UNCERTAIN."""
    import json as _json

    raw = _json.dumps(
        {
            "verdict": None,
            "confidence": 3,
            "issues": [],
            "cross_chunk_concerns": [],
            "summary": "Unclear.",
        }
    )
    result = _parse_chunk_json(raw, chunk_index=1)
    assert result.verdict == "UNCERTAIN"


# ── _deduplicate_issues ───────────────────────────────────────────────────


def test_deduplicate_removes_exact_duplicates():
    issue = {"severity": "HIGH", "file": "foo.py", "line_hint": "~42", "description": "Bad"}
    deduped = _deduplicate_issues([issue, issue])
    assert len(deduped) == 1


def test_deduplicate_keeps_different_files():
    i1 = {"severity": "HIGH", "file": "foo.py", "line_hint": "~42", "description": "Bad"}
    i2 = {"severity": "HIGH", "file": "bar.py", "line_hint": "~42", "description": "Bad"}
    deduped = _deduplicate_issues([i1, i2])
    assert len(deduped) == 2


# ── _format_chunks_for_synthesis ──────────────────────────────────────────


def test_chunk_escalation_uses_correct_indices():
    """Timeout escalation should use loop position, not chunk.index (1-based)."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    chunks = [
        DiffChunk(
            index=1, total=3, files=["a.py"], diff_text="diff a",
            line_count=10, risk_label="LOW", risk_scores={"a.py": 10.0},
        ),
        DiffChunk(
            index=2, total=3, files=["b.py"], diff_text="diff b",
            line_count=10, risk_label="MEDIUM", risk_scores={"b.py": 30.0},
        ),
        DiffChunk(
            index=3, total=3, files=["c.py"], diff_text="diff c",
            line_count=10, risk_label="HIGH", risk_scores={"c.py": 50.0},
        ),
    ]

    # First chunk succeeds, second times out
    ok_result = ChunkReviewResult(
        chunk_index=1, verdict="PASS", confidence=5, issues=[],
        cross_chunk_concerns=[], summary="ok", cost_info=ReviewCostInfo(),
        raw_text="", timed_out=False,
    )
    timeout_result = ChunkReviewResult(
        chunk_index=2, verdict="UNCERTAIN", confidence=1, issues=[],
        cross_chunk_concerns=[], summary="timed out", cost_info=ReviewCostInfo(),
        raw_text="", timed_out=True,
    )

    call_count = 0

    async def mock_review_chunk(chunk, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ok_result
        return timeout_result

    with patch("forge.review.synthesizer.review_chunk", side_effect=mock_review_chunk):
        with patch("forge.review.synthesizer.synthesize_results", new_callable=AsyncMock) as mock_synth:
            mock_synth.return_value = (
                GateResult(passed=False, gate="gate2_llm_review", details="timeout"),
                ReviewCostInfo(),
            )
            from forge.review.synthesizer import run_chunked_review

            loop = asyncio.new_event_loop()
            try:
                _, _ = loop.run_until_complete(
                    run_chunked_review(
                        chunks, [], "diff", "title", "desc",
                    )
                )
            finally:
                loop.close()

            # synthesize_results should receive 3 results:
            # chunk 1 (ok), chunk 2 (timeout), chunk 3 (placeholder)
            synth_call_args = mock_synth.call_args
            chunk_results = synth_call_args[0][1]  # second positional arg
            assert len(chunk_results) == 3
            # Chunk 3 should be a placeholder (skipped due to timeout)
            assert chunk_results[2].timed_out is True
            assert chunk_results[2].chunk_index == 3
            assert "Skipped" in chunk_results[2].summary


def test_format_chunks_for_synthesis_includes_verdict():
    result = _make_result(
        "FAIL",
        4,
        issues=[{"severity": "HIGH", "file": "foo.py", "line_hint": "~10", "description": "Bug"}],
    )
    result.summary = "Found a bug."
    chunk = DiffChunk(
        index=1,
        total=2,
        files=["foo.py"],
        diff_text="",
        line_count=10,
        risk_label="HIGH",
        risk_scores={"foo.py": 55.0},
    )
    text = _format_chunks_for_synthesis([chunk], [result])
    assert "FAIL" in text
    assert "foo.py" in text
    assert "Bug" in text
