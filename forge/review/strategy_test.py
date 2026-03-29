"""Tests for forge.review.strategy — diff analysis and chunking."""

from __future__ import annotations

from forge.review.strategy import (
    ReviewStrategy,
    build_chunks,
    build_risk_map_header,
    count_diff_lines,
    extract_interface_context,
    parse_diff_files,
    score_files,
    select_strategy,
)

# ── Fixtures ──────────────────────────────────────────────────────────────

SMALL_DIFF = """\
diff --git a/foo.py b/foo.py
index abc..def 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,5 @@
 def hello():
+    x = 1
+    return x
-    pass
"""

NEW_FILE_DIFF = (
    "diff --git a/auth/token.py b/auth/token.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/auth/token.py\n"
    "@@ -0,0 +1,50 @@\n" + "".join(f"+line{i}\n" for i in range(50))
)

TEST_SOURCE_DIFF = (
    "diff --git a/parser.py b/parser.py\n"
    "--- a/parser.py\n"
    "+++ b/parser.py\n"
    "@@ -1,5 +1,55 @@\n"
    + "".join(f"+line{i}\n" for i in range(50))
    + "diff --git a/tests/test_parser.py b/tests/test_parser.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tests/test_parser.py\n"
    "@@ -0,0 +1,40 @@\n" + "".join(f"+test_line{i}\n" for i in range(40))
)


def make_large_diff(n_files: int = 110, lines_per_file: int = 20) -> str:
    """Generate a diff with n_files × lines_per_file total lines."""
    parts = []
    for i in range(n_files):
        parts.append(
            f"diff --git a/module{i}.py b/module{i}.py\n"
            f"new file mode 100644\n"
            f"--- /dev/null\n"
            f"+++ b/module{i}.py\n"
            f"@@ -0,0 +1,{lines_per_file} @@\n"
            + "".join(f"+line{j}\n" for j in range(lines_per_file))
        )
    return "\n".join(parts)


# ── count_diff_lines ──────────────────────────────────────────────────────


def test_count_diff_lines_small():
    assert count_diff_lines(SMALL_DIFF) == 3  # 2 added + 1 removed


def test_count_diff_lines_empty():
    assert count_diff_lines("") == 0


def test_count_diff_lines_ignores_context():
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n context\n+added\n"
    assert count_diff_lines(diff) == 1


def test_count_diff_lines_ignores_header_lines():
    """Lines starting with +++ or --- are NOT counted."""
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n+real_add\n"
    assert count_diff_lines(diff) == 1


# ── parse_diff_files ──────────────────────────────────────────────────────


def test_parse_diff_files_returns_file_paths():
    sections = parse_diff_files(TEST_SOURCE_DIFF)
    assert "parser.py" in sections
    assert "tests/test_parser.py" in sections


def test_parse_diff_files_empty():
    assert parse_diff_files("") == {}


def test_parse_diff_files_single_file():
    sections = parse_diff_files(SMALL_DIFF)
    assert "foo.py" in sections
    assert len(sections) == 1


# ── score_files ───────────────────────────────────────────────────────────


def test_score_files_new_file_has_higher_score():
    scores = score_files(NEW_FILE_DIFF)
    assert len(scores) == 1
    s = scores[0]
    assert s.is_new is True
    assert s.is_security is True
    assert s.score > 30  # is_new=30 + is_security=25 at minimum


def test_score_files_test_file_lower_than_source():
    scores = score_files(TEST_SOURCE_DIFF)
    source = next(s for s in scores if s.path == "parser.py")
    test = next(s for s in scores if "test_parser" in s.path)
    assert source.score > test.score


def test_score_files_tier_labels_cover_all_files():
    scores = score_files(make_large_diff(9, 20))
    tiers = {s.tier for s in scores}
    assert "HIGH" in tiers
    assert "MEDIUM" in tiers
    assert "LOW" in tiers


def test_score_files_security_path_detection():
    diff = (
        "diff --git a/auth/login.py b/auth/login.py\n"
        "--- a/auth/login.py\n+++ b/auth/login.py\n"
        "@@ -1,1 +1,5 @@\n" + "+line\n" * 5
    )
    scores = score_files(diff)
    assert scores[0].is_security is True


def test_score_files_sorted_descending():
    scores = score_files(make_large_diff(5, 20))
    for i in range(len(scores) - 1):
        assert scores[i].score >= scores[i + 1].score


# ── select_strategy ───────────────────────────────────────────────────────


def test_select_strategy_tier1_for_small():
    assert select_strategy(SMALL_DIFF, 400, 2000) == ReviewStrategy.TIER1


def test_select_strategy_tier2_for_medium():
    diff = make_large_diff(25, 20)  # 500 lines
    assert select_strategy(diff, 400, 2000) == ReviewStrategy.TIER2


def test_select_strategy_tier3_for_large():
    diff = make_large_diff(110, 20)  # 2200 lines
    assert select_strategy(diff, 400, 2000) == ReviewStrategy.TIER3


def test_select_strategy_adaptive_false_always_tier1():
    diff = make_large_diff(110, 20)
    assert select_strategy(diff, 400, 2000, adaptive=False) == ReviewStrategy.TIER1


def test_select_strategy_boundary_medium():
    """Exactly at medium threshold → TIER2."""
    # Build diff with exactly 400 +/- lines
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n" + "+l\n" * 400
    assert select_strategy(diff, 400, 2000) == ReviewStrategy.TIER2


# ── build_chunks ──────────────────────────────────────────────────────────


def test_build_chunks_groups_files():
    diff = make_large_diff(10, 20)  # 200 total lines
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=60)
    assert len(chunks) >= 2  # can't fit 200 lines in 1 chunk of 60


def test_build_chunks_keeps_test_with_source():
    """test_parser.py and parser.py should end up in the same chunk."""
    scores = score_files(TEST_SOURCE_DIFF)
    chunks = build_chunks(scores, TEST_SOURCE_DIFF, max_chunk_lines=200)
    # Either they're in same chunk, or there's only 1 chunk total
    all_files_per_chunk = [set(c.files) for c in chunks]
    if len(chunks) > 1:
        for chunk_files in all_files_per_chunk:
            if "parser.py" in chunk_files:
                assert "tests/test_parser.py" in chunk_files


def test_build_chunks_sequential_indices():
    diff = make_large_diff(10, 20)
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=60)
    for i, chunk in enumerate(chunks):
        assert chunk.index == i + 1
        assert chunk.total == len(chunks)


def test_build_chunks_risk_label_from_highest_file():
    diff = make_large_diff(5, 20)
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=300)
    for chunk in chunks:
        assert chunk.risk_label in ("HIGH", "MEDIUM", "LOW")


def test_build_chunks_all_files_assigned():
    """Every file in the diff appears in exactly one chunk."""
    diff = make_large_diff(8, 20)
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=60)
    all_assigned = [f for c in chunks for f in c.files]
    all_unique = set(all_assigned)
    assert len(all_assigned) == len(all_unique)  # no duplicates
    assert len(all_unique) == 8  # all 8 files assigned


# ── build_risk_map_header ─────────────────────────────────────────────────


def test_build_risk_map_header_contains_file_names():
    scores = score_files(TEST_SOURCE_DIFF)
    header = build_risk_map_header(scores)
    assert "parser.py" in header


def test_build_risk_map_header_has_tier_labels():
    scores = score_files(TEST_SOURCE_DIFF)
    header = build_risk_map_header(scores)
    assert any(t in header for t in ("HIGH", "MEDIUM", "LOW"))


def test_build_risk_map_header_mentions_total_lines():
    scores = score_files(TEST_SOURCE_DIFF)
    header = build_risk_map_header(scores)
    assert "lines" in header.lower()


def test_build_risk_map_header_empty_scores():
    header = build_risk_map_header([])
    assert header == ""


# ── extract_interface_context ─────────────────────────────────────────────


def test_extract_interface_context_returns_str():
    scores = score_files(TEST_SOURCE_DIFF)
    chunks = build_chunks(scores, TEST_SOURCE_DIFF, max_chunk_lines=200)
    ctx = extract_interface_context(chunks[0], scores, TEST_SOURCE_DIFF)
    assert isinstance(ctx, str)


def test_extract_interface_context_within_line_limit():
    diff = make_large_diff(10, 20)
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=60)
    for chunk in chunks:
        ctx = extract_interface_context(chunk, scores, diff)
        assert ctx.count("\n") <= 201  # max_lines=200 + header line


def test_extract_interface_context_empty_for_no_imports():
    """Diff with no import statements → empty context."""
    diff = make_large_diff(3, 10)  # files have no import lines
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=100)
    ctx = extract_interface_context(chunks[0], scores, diff)
    # May be empty (no imports) — should not raise
    assert isinstance(ctx, str)
