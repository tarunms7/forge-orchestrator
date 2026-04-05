"""Tests for forge.review.strategy."""

from __future__ import annotations

import pytest

from forge.review.strategy import (
    DiffChunk,
    FileRiskScore,
    FileScore,
    ReviewStrategy,
    _is_test_file,
    _stem,
    build_chunks,
    count_diff_lines,
    extract_interface_context,
    parse_diff_files,
    select_strategy,
)

# ---------------------------------------------------------------------------
# Fixtures / shared diff text
# ---------------------------------------------------------------------------

# A small diff with one Python source file (~50 changed lines)
TEST_SOURCE_DIFF = """\
diff --git a/forge/foo/bar.py b/forge/foo/bar.py
--- a/forge/foo/bar.py
+++ b/forge/foo/bar.py
@@ -1,5 +1,50 @@
+line1
+line2
+line3
+line4
+line5
+line6
+line7
+line8
+line9
+line10
+line11
+line12
+line13
+line14
+line15
+line16
+line17
+line18
+line19
+line20
+line21
+line22
+line23
+line24
+line25
+line26
+line27
+line28
+line29
+line30
+line31
+line32
+line33
+line34
+line35
+line36
+line37
+line38
+line39
+line40
+line41
+line42
+line43
+line44
+line45
+line46
+line47
+line48
+line49
+line50
"""

# A test file diff that corresponds to the source above
TEST_TEST_DIFF = """\
diff --git a/forge/foo/bar_test.py b/forge/foo/bar_test.py
--- a/forge/foo/bar_test.py
+++ b/forge/foo/bar_test.py
@@ -1,3 +1,5 @@
+def test_bar():
+    pass
"""

COMBINED_DIFF = TEST_SOURCE_DIFF + TEST_TEST_DIFF


# ---------------------------------------------------------------------------
# count_diff_lines
# ---------------------------------------------------------------------------


def test_count_diff_lines_basic():
    diff = """\
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 context line
+added line
-removed line
 another context
"""
    assert count_diff_lines(diff) == 2


def test_count_diff_lines_excludes_headers():
    diff = """\
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
+real add
-real remove
"""
    assert count_diff_lines(diff) == 2


def test_count_diff_lines_empty():
    assert count_diff_lines("") == 0


def test_count_diff_lines_none():
    # None must not crash — returns 0
    assert count_diff_lines(None) == 0  # type: ignore[arg-type]


def test_count_diff_lines_whitespace_only():
    assert count_diff_lines("   \n  \n") == 0


# ---------------------------------------------------------------------------
# parse_diff_files
# ---------------------------------------------------------------------------


def test_parse_diff_files_splits_two_files():
    result = parse_diff_files(COMBINED_DIFF)
    assert set(result.keys()) == {"forge/foo/bar.py", "forge/foo/bar_test.py"}


def test_parse_diff_files_preserves_content():
    result = parse_diff_files(TEST_SOURCE_DIFF)
    assert "forge/foo/bar.py" in result
    assert "+line1" in result["forge/foo/bar.py"]


def test_parse_diff_files_empty():
    assert parse_diff_files("") == {}


def test_parse_diff_files_none():
    # None must not crash
    assert parse_diff_files(None) == {}  # type: ignore[arg-type]


def test_parse_diff_files_whitespace():
    assert parse_diff_files("   \n") == {}


# ---------------------------------------------------------------------------
# _is_test_file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "forge/foo/bar_test.py",
        "forge/foo/test_bar.py",
        "tests/test_bar.py",
        "forge/foo/bar.test.ts",
        "forge/foo/bar.spec.ts",
        "forge/foo/bar.test.tsx",
        "forge/foo/bar.spec.tsx",
        "forge/foo/bar.test.jsx",
        "forge/foo/bar.spec.jsx",
        "src/__tests__/bar.ts",
        "__tests__/bar.ts",
        "forge/tests/utils.py",
    ],
)
def test_is_test_file_positive(path):
    assert _is_test_file(path), f"Expected {path!r} to be detected as a test file"


@pytest.mark.parametrize(
    "path",
    [
        "forge/foo/bar.py",
        "forge/foo/bar.ts",
        "forge/foo/bar.tsx",
        "src/components/Button.tsx",
        "forge/strategy.py",
    ],
)
def test_is_test_file_negative(path):
    assert not _is_test_file(path), f"Expected {path!r} NOT to be a test file"


# ---------------------------------------------------------------------------
# _stem
# ---------------------------------------------------------------------------


def test_stem_strips_test_suffix():
    assert _stem("forge/foo/bar_test.py") == "bar"


def test_stem_strips_test_prefix():
    assert _stem("tests/test_bar.py") == "bar"


def test_stem_strips_dot_test():
    assert _stem("src/bar.test.ts") == "bar"


def test_stem_source_file():
    assert _stem("forge/foo/bar.py") == "bar"


# ---------------------------------------------------------------------------
# select_strategy
# ---------------------------------------------------------------------------


def test_select_strategy_small_is_tier1():
    tiny_diff = "+line1\n-line2\n"
    assert select_strategy(tiny_diff) == ReviewStrategy.TIER1


def test_select_strategy_medium_is_tier2():
    # 500 changed lines → above default medium threshold (400)
    lines = "".join(f"+line{i}\n" for i in range(500))
    assert select_strategy(lines) == ReviewStrategy.TIER2


def test_select_strategy_large_is_tier3():
    # 2500 changed lines → above default large threshold (2000)
    lines = "".join(f"+line{i}\n" for i in range(2500))
    assert select_strategy(lines) == ReviewStrategy.TIER3


def test_select_strategy_adaptive_false_always_tier1():
    """Custom thresholds: setting medium_threshold very high forces TIER1."""
    lines = "".join(f"+line{i}\n" for i in range(300))
    assert (
        select_strategy(lines, medium_threshold=99999, large_threshold=999999)
        == ReviewStrategy.TIER1
    )


def test_select_strategy_boundary_large():
    """Exactly large_threshold lines → TIER3; one less → TIER2."""
    # Build a diff with exactly 2000 +/- lines
    lines_2000 = "".join(f"+line{i}\n" for i in range(2000))
    diff_2000 = f"diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -1,1 +1,2000 @@\n{lines_2000}"
    assert select_strategy(diff_2000, 400, 2000) == ReviewStrategy.TIER3

    lines_1999 = "".join(f"+line{i}\n" for i in range(1999))
    diff_1999 = f"diff --git a/big.py b/big.py\n--- a/big.py\n+++ b/big.py\n@@ -1,1 +1,1999 @@\n{lines_1999}"
    assert select_strategy(diff_1999, 400, 2000) == ReviewStrategy.TIER2


# ---------------------------------------------------------------------------
# build_chunks
# ---------------------------------------------------------------------------


def test_build_chunks_single_file():
    scores = [FileScore(path="forge/foo/bar.py", score=1.0, line_count=50)]
    chunks = build_chunks(scores, TEST_SOURCE_DIFF, max_chunk_lines=300)
    assert len(chunks) == 1
    assert chunks[0].paths == ["forge/foo/bar.py"]
    assert chunks[0].chunk_index == 1
    assert chunks[0].total_chunks == 1


def test_build_chunks_keeps_test_with_source():
    """Test files are co-located with their source file."""
    scores = [
        FileScore(path="forge/foo/bar.py", score=2.0, line_count=50),
        FileScore(path="forge/foo/bar_test.py", score=1.0, line_count=2),
    ]
    # max_chunk_lines=55 forces multiple chunks if bar.py is ~50 lines:
    # bar.py uses ~50 lines, bar_test.py uses ~2 lines → combined ~52 ≤ 55 → one chunk
    # BUT if we add a third file, the source+test pair occupies one chunk and the third goes to another.
    # Use a very tight limit to make the co-location assertion meaningful.
    chunks = build_chunks(scores, COMBINED_DIFF, max_chunk_lines=55)
    # Both files should end up in the same chunk regardless of chunk count
    all_paths: list[str] = []
    for c in chunks:
        all_paths.extend(c.paths)
    assert "forge/foo/bar.py" in all_paths
    assert "forge/foo/bar_test.py" in all_paths
    # They must be in the SAME chunk
    for chunk in chunks:
        if "forge/foo/bar_test.py" in chunk.paths:
            assert "forge/foo/bar.py" in chunk.paths, (
                "Test file must be co-located with its source file"
            )
            break

    # Verify total_chunks is back-filled correctly
    for chunk in chunks:
        assert chunk.total_chunks == len(chunks)


def test_build_chunks_splits_unrelated_files():
    """Unrelated files that overflow max_chunk_lines go into separate chunks."""
    # Create a diff with two completely unrelated large files
    file_a_diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,60 @@\n" + "".join(
        f"+lineA{i}\n" for i in range(60)
    )
    file_b_diff = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1,60 @@\n" + "".join(
        f"+lineB{i}\n" for i in range(60)
    )
    combined = file_a_diff + file_b_diff
    scores = [
        FileScore(path="a.py", score=2.0, line_count=60),
        FileScore(path="b.py", score=1.0, line_count=60),
    ]
    chunks = build_chunks(scores, combined, max_chunk_lines=70)
    assert len(chunks) == 2
    assert chunks[0].paths == ["a.py"]
    assert chunks[1].paths == ["b.py"]


def test_build_chunks_total_chunks_backfill():
    """total_chunks is set correctly on every chunk."""
    file_a_diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,60 @@\n" + "".join(
        f"+lineA{i}\n" for i in range(60)
    )
    file_b_diff = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1,60 @@\n" + "".join(
        f"+lineB{i}\n" for i in range(60)
    )
    combined = file_a_diff + file_b_diff
    scores = [
        FileScore(path="a.py", score=2.0),
        FileScore(path="b.py", score=1.0),
    ]
    chunks = build_chunks(scores, combined, max_chunk_lines=70)
    for chunk in chunks:
        assert chunk.total_chunks == 2


def test_build_chunks_empty_scores():
    chunks = build_chunks([], "", max_chunk_lines=300)
    assert chunks == []


def test_build_chunks_file_not_in_diff():
    """Files not in full_diff get empty diff text but still appear in a chunk."""
    scores = [FileScore(path="missing.py", score=1.0)]
    chunks = build_chunks(scores, "", max_chunk_lines=300)
    assert len(chunks) == 1
    assert "missing.py" in chunks[0].paths
    assert chunks[0].diff_text == ""


def test_extract_interface_context_includes_imported_sibling_structure():
    full_diff = """\
diff --git a/forge/providers/base.py b/forge/providers/base.py
--- a/forge/providers/base.py
+++ b/forge/providers/base.py
@@ -1,2 +1,7 @@
+from dataclasses import dataclass
+
+@dataclass
+class ProviderResult:
+    model_canonical_id: str = ""
+    raw: object | None = None
diff --git a/forge/providers/registry_test.py b/forge/providers/registry_test.py
--- a/forge/providers/registry_test.py
+++ b/forge/providers/registry_test.py
@@ -1,2 +1,4 @@
+from forge.providers.base import ProviderResult
+
+def test_uses_provider_result():
+    assert ProviderResult().model_canonical_id == ""
"""
    chunk = DiffChunk(
        index=1,
        total=2,
        files=["forge/providers/registry_test.py"],
        diff_text=parse_diff_files(full_diff)["forge/providers/registry_test.py"],
        line_count=4,
        risk_label="MEDIUM",
        risk_scores={"forge/providers/registry_test.py": 10.0},
    )
    all_scores = [
        FileRiskScore(path="forge/providers/registry_test.py", score=10.0, line_count=4),
        FileRiskScore(path="forge/providers/base.py", score=9.0, line_count=5),
    ]

    context = extract_interface_context(chunk, all_scores, full_diff)

    assert "forge/providers/base.py" in context
    assert "class ProviderResult:" in context
    assert 'model_canonical_id: str = ""' in context


def test_extract_interface_context_skips_unrelated_siblings():
    full_diff = """\
diff --git a/forge/review/strategy.py b/forge/review/strategy.py
--- a/forge/review/strategy.py
+++ b/forge/review/strategy.py
@@ -1,2 +1,3 @@
+def helper():
+    return "ok"
diff --git a/forge/providers/registry_test.py b/forge/providers/registry_test.py
--- a/forge/providers/registry_test.py
+++ b/forge/providers/registry_test.py
@@ -1,2 +1,4 @@
+from forge.providers.base import ProviderResult
+
+def test_uses_provider_result():
+    assert ProviderResult().model_canonical_id == ""
"""
    chunk = DiffChunk(
        index=1,
        total=2,
        files=["forge/providers/registry_test.py"],
        diff_text=parse_diff_files(full_diff)["forge/providers/registry_test.py"],
        line_count=4,
        risk_label="MEDIUM",
        risk_scores={"forge/providers/registry_test.py": 10.0},
    )
    all_scores = [
        FileRiskScore(path="forge/providers/registry_test.py", score=10.0, line_count=4),
        FileRiskScore(path="forge/review/strategy.py", score=6.0, line_count=2),
    ]

    context = extract_interface_context(chunk, all_scores, full_diff)

    assert "  - forge/review/strategy.py" in context
    assert "# forge/review/strategy.py" not in context
    assert "def helper():" not in context
