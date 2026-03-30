"""Tests for forge.review.synthesizer — chunk review aggregation."""

from __future__ import annotations

import json

from forge.review.pipeline import ReviewCostInfo
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
