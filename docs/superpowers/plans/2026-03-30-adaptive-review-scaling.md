# Adaptive Review Scaling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scale Forge's L2 reviewer to handle large diffs (400–15 000+ lines) without quality degradation by adding risk-scored file prioritisation (Tier 2) and multi-chunk map-reduce review with synthesis (Tier 3).

**Architecture:** A pure-Python risk scorer ranks every changed file by lines changed, novelty, security sensitivity, and complexity. For medium diffs (400–2000 lines) the score injects a priority map into the existing single-pass prompt. For large diffs (>2000 lines) the diff is split into ≤600-line chunks, each reviewed independently by a Claude agent producing structured JSON, and a final synthesis agent aggregates all findings into a PASS/FAIL/UNCERTAIN verdict.

**Tech Stack:** Python 3.12+, asyncio, claude-code-sdk, existing `forge.review.*` + `forge.core.daemon_review`, Textual TUI (existing event bus).

**Spec:** `docs/superpowers/specs/2026-03-30-adaptive-review-scaling-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `forge/review/pipeline.py` | Modify | Move `ReviewCostInfo` here; extend `GateResult` with 3 new optional fields |
| `forge/review/strategy.py` | **Create** | `ReviewStrategy` enum, `FileRiskScore`, `DiffChunk`, risk scorer, chunker, strategy selector, risk-map builder, interface-context extractor |
| `forge/review/synthesizer.py` | **Create** | `ChunkReviewResult`, chunk reviewer prompt, per-chunk SDK call, synthesis aggregation |
| `forge/review/llm_review.py` | Modify | Add `on_review_event` callback param; `ReviewCostInfo` re-export; strategy dispatcher; Tier 2 risk-map injection; Tier 3 orchestration |
| `forge/config/project_config.py` | Modify | Add `adaptive_review`, `medium_diff_threshold`, `large_diff_threshold`, `max_chunk_lines` to `ReviewConfig` |
| `forge/core/daemon_review.py` | Modify | Build `on_review_event` callback in `_run_review()`; emit 4 new WebSocket events; pass it to `gate2_llm_review()` |
| `forge/tui/app.py` | Modify | Register 4 new event types in `TUI_EVENT_TYPES`; handle `review:strategy_selected` and `review:chunk_complete` in TUI state |
| `forge/review/strategy_test.py` | **Create** | Unit tests for risk scorer, chunker, strategy selection, risk-map format, interface-context extraction |
| `forge/review/synthesizer_test.py` | **Create** | Unit tests for synthesis verdict logic (all-pass, any-fail, uncertain, confidence weighting) |
| `forge/review/llm_review_test.py` | Modify | Add Tier 2 prompt structure test (risk map present); strategy dispatcher routing tests |
| `forge/config/project_config_test.py` | Modify | Test new `ReviewConfig` fields with defaults and custom values |

---

## Task 1: Extend `pipeline.py` — move ReviewCostInfo, extend GateResult

**Files:**
- Modify: `forge/review/pipeline.py`
- Modify: `forge/review/llm_review.py` (add re-export only — no logic change)

- [ ] **Step 1.1: Read current pipeline.py**

```bash
cat -n forge/review/pipeline.py
```

- [ ] **Step 1.2: Add `ReviewCostInfo` and extend `GateResult` in pipeline.py**

Replace the entire `forge/review/pipeline.py` with:

```python
"""Review gate data classes."""

from dataclasses import dataclass, field


@dataclass
class ReviewCostInfo:
    """Accumulated cost from one or more LLM review calls."""

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "ReviewCostInfo") -> None:
        """Accumulate cost from another ReviewCostInfo in-place."""
        self.cost_usd += other.cost_usd
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class GateResult:
    """Outcome of a single review gate."""

    passed: bool
    gate: str
    details: str
    retriable: bool = (
        False  # True = transient failure (empty response, SDK error) — re-review, don't re-agent
    )
    infra_error: bool = (
        False  # True = environment/infra failure (missing module, wrong Python, cmd not found)
    )
    # — skip this gate instead of consuming a retry
    needs_human: bool = False  # True = escalate to awaiting_input for human decision
    # Adaptive review metadata (all optional, backward-compatible)
    review_strategy: str | None = None      # "tier1", "tier2", "tier3"
    chunk_count: int | None = None          # Tier 3 only: total number of chunks
    chunk_verdicts: list[str] | None = None  # Tier 3 only: e.g. ["PASS","FAIL","PASS"]


@dataclass
class ReviewOutcome:
    """Final outcome of the full review pipeline."""

    approved: bool
    gate_results: list[GateResult] = field(default_factory=list)
    failed_gate: str | None = None
```

- [ ] **Step 1.3: Add ReviewCostInfo re-export to llm_review.py**

At the top of `forge/review/llm_review.py`, find the existing `ReviewCostInfo` dataclass definition and REPLACE it with a re-export:

Find this block (around lines 21–27):
```python
@dataclass
class ReviewCostInfo:
    """Cost information from an LLM review call."""

    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
```

Replace with:
```python
# ReviewCostInfo lives in pipeline.py to avoid circular imports.
# Re-exported here for backward compatibility with existing callers.
from forge.review.pipeline import ReviewCostInfo  # noqa: F401
```

- [ ] **Step 1.4: Run existing tests to verify no breakage**

```bash
cd /path/to/forge-repo
python -m pytest forge/review/ -x -q 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 1.5: Commit**

```bash
git add forge/review/pipeline.py forge/review/llm_review.py
git commit -m "refactor(review): move ReviewCostInfo to pipeline.py, extend GateResult with adaptive fields"
```

---

## Task 2: Extend `ReviewConfig` in `project_config.py`

**Files:**
- Modify: `forge/config/project_config.py` (ReviewConfig class, lines ~172–181)
- Modify: `forge/config/project_config_test.py`

- [ ] **Step 2.1: Find ReviewConfig in project_config.py**

```bash
grep -n "class ReviewConfig" forge/config/project_config.py
```

Note the line number.

- [ ] **Step 2.2: Replace the ReviewConfig dataclass**

Find:
```python
@dataclass
class ReviewConfig:
    """Configuration for LLM code review."""

    enabled: bool = True
    max_retries: int = 3

    def __post_init__(self):
        if self.max_retries < 0:
            self.max_retries = 0
```

Replace with:
```python
@dataclass
class ReviewConfig:
    """Configuration for LLM code review."""

    enabled: bool = True
    max_retries: int = 3
    # Adaptive review scaling (new in this PR)
    adaptive_review: bool = True        # False = always use single-pass (Tier 1)
    medium_diff_threshold: int = 400    # Lines; ≥ this → Tier 2 (risk-enhanced single pass)
    large_diff_threshold: int = 2000    # Lines; ≥ this → Tier 3 (multi-chunk map-reduce)
    max_chunk_lines: int = 600          # Max lines per chunk in Tier 3

    def __post_init__(self):
        if self.max_retries < 0:
            self.max_retries = 0
        if self.medium_diff_threshold < 1:
            self.medium_diff_threshold = 1
        if self.large_diff_threshold <= self.medium_diff_threshold:
            self.large_diff_threshold = self.medium_diff_threshold + 1
        if self.max_chunk_lines < 50:
            self.max_chunk_lines = 50
```

- [ ] **Step 2.3: Find where ReviewConfig is parsed from TOML in project_config.py**

```bash
grep -n "ReviewConfig\|review_config\|\"review\"\|'review'" forge/config/project_config.py | head -30
```

Locate the section that builds a `ReviewConfig` from the TOML dict and add the new fields. It will look something like:
```python
review_data = data.get("review", {})
review = ReviewConfig(
    enabled=review_data.get("enabled", True),
    max_retries=review_data.get("max_retries", 3),
)
```

Add the new fields:
```python
review_data = data.get("review", {})
review = ReviewConfig(
    enabled=review_data.get("enabled", True),
    max_retries=review_data.get("max_retries", 3),
    adaptive_review=review_data.get("adaptive_review", True),
    medium_diff_threshold=review_data.get("medium_diff_threshold", 400),
    large_diff_threshold=review_data.get("large_diff_threshold", 2000),
    max_chunk_lines=review_data.get("max_chunk_lines", 600),
)
```

- [ ] **Step 2.4: Write tests for new ReviewConfig fields**

Find `forge/config/project_config_test.py` and add:

```python
def test_review_config_defaults():
    """New adaptive review fields have correct defaults."""
    cfg = ReviewConfig()
    assert cfg.adaptive_review is True
    assert cfg.medium_diff_threshold == 400
    assert cfg.large_diff_threshold == 2000
    assert cfg.max_chunk_lines == 600


def test_review_config_clamps_thresholds():
    """large_diff_threshold is always > medium_diff_threshold."""
    cfg = ReviewConfig(medium_diff_threshold=1000, large_diff_threshold=500)
    assert cfg.large_diff_threshold > cfg.medium_diff_threshold


def test_review_config_clamps_chunk_lines():
    """max_chunk_lines is at least 50."""
    cfg = ReviewConfig(max_chunk_lines=5)
    assert cfg.max_chunk_lines == 50


def test_review_config_from_toml_new_fields(tmp_path):
    """New fields parsed correctly from forge.toml."""
    toml_content = """
[review]
enabled = true
max_retries = 2
adaptive_review = false
medium_diff_threshold = 300
large_diff_threshold = 1500
max_chunk_lines = 400
"""
    toml_file = tmp_path / "forge.toml"
    toml_file.write_text(toml_content)
    # Load via ProjectConfig (adjust import path to match the actual loader)
    from forge.config.project_config import ProjectConfig
    cfg = ProjectConfig.from_file(str(toml_file))
    assert cfg.review.adaptive_review is False
    assert cfg.review.medium_diff_threshold == 300
    assert cfg.review.large_diff_threshold == 1500
    assert cfg.review.max_chunk_lines == 400
```

NOTE: Check how `ProjectConfig.from_file` is named in the actual codebase — run `grep -n "def from_file\|def load\|def from_path" forge/config/project_config.py` and use the real method name.

- [ ] **Step 2.5: Run config tests**

```bash
python -m pytest forge/config/project_config_test.py -x -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 2.6: Commit**

```bash
git add forge/config/project_config.py forge/config/project_config_test.py
git commit -m "feat(config): add adaptive review scaling options to ReviewConfig"
```

---

## Task 3: Create `forge/review/strategy.py`

**Files:**
- Create: `forge/review/strategy.py`
- Create: `forge/review/strategy_test.py`

This module is pure Python with no LLM calls. It provides:
- `ReviewStrategy` enum
- `FileRiskScore` + `DiffChunk` dataclasses
- `count_diff_lines(diff)` — total +/- lines
- `parse_diff_files(diff)` — extract per-file sections from a git diff string
- `score_files(diff)` — compute `FileRiskScore` for every changed file
- `select_strategy(diff, medium_threshold, large_threshold)` — pick Tier
- `build_chunks(file_scores, diff, max_chunk_lines)` — group into `DiffChunk`s
- `build_risk_map_header(file_scores)` — formatted text for Tier 2 prompt
- `extract_interface_context(chunk, all_file_scores, full_diff)` — function sigs from other chunks

- [ ] **Step 3.1: Write failing tests first**

Create `forge/review/strategy_test.py`:

```python
"""Tests for forge.review.strategy — diff analysis and chunking."""

from __future__ import annotations

import pytest
from forge.review.strategy import (
    DiffChunk,
    FileRiskScore,
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

MEDIUM_DIFF_TEMPLATE = "diff --git a/file{n}.py b/file{n}.py\n" \
    "--- a/file{n}.py\n+++ b/file{n}.py\n" \
    "@@ -1,1 +1,20 @@\n" + ("+    line\n" * 20)

def make_medium_diff(n_files: int = 25) -> str:
    """Generate a diff with ~500 total lines across n_files."""
    parts = []
    for i in range(n_files):
        parts.append(
            f"diff --git a/module{i}.py b/module{i}.py\n"
            f"new file mode 100644\n"
            f"--- /dev/null\n"
            f"+++ b/module{i}.py\n"
            f"@@ -0,0 +1,20 @@\n"
            + "".join(f"+line{j}\n" for j in range(20))
        )
    return "\n".join(parts)


NEW_FILE_DIFF = """\
diff --git a/auth/token.py b/auth/token.py
new file mode 100644
--- /dev/null
+++ b/auth/token.py
@@ -0,0 +1,50 @@
""" + "".join(f"+line{i}\n" for i in range(50))

TEST_SOURCE_DIFF = """\
diff --git a/parser.py b/parser.py
--- a/parser.py
+++ b/parser.py
@@ -1,5 +1,10 @@
""" + "".join(f"+line{i}\n" for i in range(50)) + """\
diff --git a/tests/test_parser.py b/tests/test_parser.py
new file mode 100644
--- /dev/null
+++ b/tests/test_parser.py
@@ -0,0 +1,40 @@
""" + "".join(f"+test_line{i}\n" for i in range(40))


# ── count_diff_lines ──────────────────────────────────────────────────────

def test_count_diff_lines_small():
    assert count_diff_lines(SMALL_DIFF) == 3  # 2 added + 1 removed


def test_count_diff_lines_empty():
    assert count_diff_lines("") == 0


def test_count_diff_lines_ignores_context():
    """Lines without + or - prefix are not counted."""
    diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n context\n+added\n"
    assert count_diff_lines(diff) == 1


# ── parse_diff_files ──────────────────────────────────────────────────────

def test_parse_diff_files_returns_file_paths():
    sections = parse_diff_files(TEST_SOURCE_DIFF)
    paths = list(sections.keys())
    assert "parser.py" in paths
    assert "tests/test_parser.py" in paths


def test_parse_diff_files_empty():
    assert parse_diff_files("") == {}


# ── score_files ───────────────────────────────────────────────────────────

def test_score_files_new_file_has_higher_score():
    sections = parse_diff_files(NEW_FILE_DIFF)
    scores = score_files(NEW_FILE_DIFF)
    auth_score = next(s for s in scores if "token" in s.path)
    assert auth_score.is_new is True
    assert auth_score.is_security is True
    assert auth_score.score > 30  # is_new=30 + is_security=25 minimum


def test_score_files_test_file_lower_than_source():
    scores = score_files(TEST_SOURCE_DIFF)
    source = next(s for s in scores if s.path == "parser.py")
    test = next(s for s in scores if "test_parser" in s.path)
    assert source.score > test.score


def test_score_files_tier_labels_cover_all_files():
    scores = score_files(make_medium_diff(9))
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
    s = scores[0]
    assert s.is_security is True


# ── select_strategy ───────────────────────────────────────────────────────

def test_select_strategy_tier1_for_small():
    assert select_strategy(SMALL_DIFF, 400, 2000) == ReviewStrategy.TIER1


def test_select_strategy_tier2_for_medium():
    diff = make_medium_diff(25)  # ~500 lines
    assert select_strategy(diff, 400, 2000) == ReviewStrategy.TIER2


def test_select_strategy_tier3_for_large():
    diff = make_medium_diff(110)  # ~2200 lines
    assert select_strategy(diff, 400, 2000) == ReviewStrategy.TIER3


def test_select_strategy_adaptive_false_always_tier1():
    """When adaptive_review=False, always returns TIER1."""
    diff = make_medium_diff(110)
    assert select_strategy(diff, 400, 2000, adaptive=False) == ReviewStrategy.TIER1


# ── build_chunks ──────────────────────────────────────────────────────────

def test_build_chunks_groups_files():
    diff = make_medium_diff(10)  # 10 files × 20 lines = 200 lines total
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=60)
    # With 10 files × 20 lines each and max 60 lines per chunk, expect ≥ 3 chunks
    assert len(chunks) >= 3


def test_build_chunks_keeps_test_with_source():
    """test_parser.py and parser.py end up in the same chunk."""
    scores = score_files(TEST_SOURCE_DIFF)
    chunks = build_chunks(scores, TEST_SOURCE_DIFF, max_chunk_lines=200)
    for chunk in chunks:
        if "parser.py" in chunk.files:
            assert "tests/test_parser.py" in chunk.files or len(chunks) == 1


def test_build_chunks_chunk_indices_are_sequential():
    diff = make_medium_diff(10)
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=60)
    for i, chunk in enumerate(chunks):
        assert chunk.index == i + 1
        assert chunk.total == len(chunks)


def test_build_chunks_risk_label_from_highest_file():
    """Chunk risk label is the tier of the highest-scoring file."""
    diff = make_medium_diff(5)
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=300)
    for chunk in chunks:
        assert chunk.risk_label in ("HIGH", "MEDIUM", "LOW")


# ── build_risk_map_header ─────────────────────────────────────────────────

def test_build_risk_map_header_contains_file_names():
    scores = score_files(TEST_SOURCE_DIFF)
    header = build_risk_map_header(scores)
    assert "parser.py" in header
    assert "HIGH" in header or "MEDIUM" in header or "LOW" in header


def test_build_risk_map_header_mentions_total_lines():
    scores = score_files(TEST_SOURCE_DIFF)
    header = build_risk_map_header(scores)
    assert "lines" in header.lower()


# ── extract_interface_context ─────────────────────────────────────────────

def test_extract_interface_context_returns_str():
    scores = score_files(TEST_SOURCE_DIFF)
    chunks = build_chunks(scores, TEST_SOURCE_DIFF, max_chunk_lines=200)
    if len(chunks) > 1:
        ctx = extract_interface_context(chunks[0], scores, TEST_SOURCE_DIFF)
        assert isinstance(ctx, str)


def test_extract_interface_context_within_limit():
    """Interface context is at most 200 lines."""
    diff = make_medium_diff(10)
    scores = score_files(diff)
    chunks = build_chunks(scores, diff, max_chunk_lines=60)
    for chunk in chunks:
        ctx = extract_interface_context(chunk, scores, diff)
        assert ctx.count("\n") <= 200
```

- [ ] **Step 3.2: Run tests to confirm they all fail**

```bash
python -m pytest forge/review/strategy_test.py -x -q 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'forge.review.strategy'`

- [ ] **Step 3.3: Implement `forge/review/strategy.py`**

```python
"""Adaptive review strategy: diff analysis, risk scoring, and chunking.

No LLM calls in this module — all pure Python. Provides the data structures
and algorithms for Tier 2 (risk-enhanced single pass) and Tier 3 (multi-chunk
map-reduce) review strategies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath


# ── Enums and dataclasses ──────────────────────────────────────────────────

class ReviewStrategy(str, Enum):
    TIER1 = "tier1"   # < medium_threshold lines: single pass, full diff (unchanged)
    TIER2 = "tier2"   # medium_threshold–large_threshold: risk-enhanced single pass
    TIER3 = "tier3"   # > large_threshold: multi-chunk map-reduce with synthesis


@dataclass
class FileRiskScore:
    """Risk assessment for a single changed file."""

    path: str
    score: float
    tier: str        # "HIGH", "MEDIUM", "LOW"
    is_new: bool
    is_test: bool
    is_security: bool
    lines_changed: int
    language: str


@dataclass
class DiffChunk:
    """A subset of the full diff assigned for independent review."""

    index: int                     # 1-based chunk index
    total: int                     # total number of chunks
    files: list[str]               # file paths in this chunk
    diff_text: str                 # combined diff text for these files only
    line_count: int                # total +/- lines in this chunk
    risk_label: str                # "HIGH", "MEDIUM", "LOW" — tier of highest-scoring file
    risk_scores: dict[str, float]  # file path → risk score


# ── Constants ──────────────────────────────────────────────────────────────

_SECURITY_SEGMENTS = frozenset(
    {
        "auth", "crypto", "token", "password", "secret", "key",
        "perm", "acl", "role", "jwt", "session", "login", "oauth", "cred",
    }
)

_LANGUAGE_WEIGHT: dict[str, float] = {
    ".py": 10, ".go": 10, ".rs": 10, ".ts": 8, ".tsx": 8,
    ".js": 8, ".jsx": 8, ".java": 8, ".kt": 8, ".swift": 8,
    ".rb": 6, ".cpp": 10, ".c": 10, ".h": 8,
    ".yaml": 2, ".yml": 2, ".json": 2, ".toml": 2, ".md": 0,
}


# ── Diff parsing ───────────────────────────────────────────────────────────

def parse_diff_files(diff: str) -> dict[str, str]:
    """Split a git diff into per-file sections.

    Returns a dict mapping file path → diff text for that file.
    The file path is the b/ path (post-change).
    """
    if not diff.strip():
        return {}

    file_sections: dict[str, str] = {}
    current_file: str | None = None
    current_lines: list[str] = []

    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            # Save previous section
            if current_file is not None:
                file_sections[current_file] = "".join(current_lines)
            # Parse new file path from "diff --git a/X b/X"
            m = re.match(r"diff --git a/.+ b/(.+)", line.rstrip("\n"))
            current_file = m.group(1) if m else None
            current_lines = [line]
        else:
            if current_file is not None:
                current_lines.append(line)

    if current_file is not None:
        file_sections[current_file] = "".join(current_lines)

    return file_sections


def count_diff_lines(diff: str) -> int:
    """Count total added + removed lines in a diff (excludes context lines)."""
    count = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            count += 1
        elif line.startswith("-") and not line.startswith("---"):
            count += 1
    return count


def _count_file_lines(file_diff: str) -> int:
    """Count +/- lines for a single file's diff section."""
    return count_diff_lines(file_diff)


def _is_new_file(file_diff: str) -> bool:
    return "new file mode" in file_diff or "--- /dev/null" in file_diff


def _is_test_file(path: str) -> bool:
    p = path.lower()
    return (
        "/test" in p
        or p.startswith("test")
        or p.endswith("_test.py")
        or "_test." in p
        or "test_" in p.split("/")[-1]
        or "/tests/" in p
        or "/spec/" in p
        or p.endswith(".test.ts")
        or p.endswith(".spec.ts")
    )


def _is_security_path(path: str) -> bool:
    segments = {s.lower() for s in PurePosixPath(path).parts}
    # Also check the stem (filename without extension)
    stem = PurePosixPath(path).stem.lower()
    return bool(segments & _SECURITY_SEGMENTS) or any(
        kw in stem for kw in _SECURITY_SEGMENTS
    )


def _language(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def _avg_hunk_size(file_diff: str) -> float:
    """Compute average lines-per-hunk as a complexity proxy. Clamped to [0, 20]."""
    hunks = [l for l in file_diff.splitlines() if l.startswith("@@")]
    if not hunks:
        return 0.0
    total_change = _count_file_lines(file_diff)
    avg = total_change / len(hunks)
    return min(avg, 20.0)


# ── Risk scoring ──────────────────────────────────────────────────────────

def score_files(diff: str) -> list[FileRiskScore]:
    """Compute a `FileRiskScore` for every changed file in the diff.

    Returns the list sorted descending by score (highest risk first).
    Tier labels are assigned after all scores are computed:
      HIGH = top 30% by count, MEDIUM = next 40%, LOW = bottom 30%.
    """
    file_sections = parse_diff_files(diff)
    if not file_sections:
        return []

    raw: list[tuple[str, float, dict]] = []
    for path, section in file_sections.items():
        lines_changed = _count_file_lines(section)
        is_new = _is_new_file(section)
        is_test = _is_test_file(path)
        is_sec = _is_security_path(path)
        lang = _language(path)
        lang_w = _LANGUAGE_WEIGHT.get(lang, 5)
        avg_hunk = _avg_hunk_size(section)

        score = (
            min(lines_changed, 500) * 0.4
            + (30 if is_new else 0)
            + (25 if is_sec else 0)
            + avg_hunk * 0.5
            + (10 if not is_test else 0)
            + lang_w
        )
        raw.append((path, score, {
            "is_new": is_new, "is_test": is_test, "is_sec": is_sec,
            "lang": lang, "lines": lines_changed,
        }))

    # Sort descending
    raw.sort(key=lambda x: x[1], reverse=True)

    # Assign tier labels: HIGH top 30%, MEDIUM next 40%, LOW bottom 30%
    n = len(raw)
    high_count = max(1, round(n * 0.30))
    med_count = max(1, round(n * 0.40))

    results: list[FileRiskScore] = []
    for i, (path, score, meta) in enumerate(raw):
        if i < high_count:
            tier = "HIGH"
        elif i < high_count + med_count:
            tier = "MEDIUM"
        else:
            tier = "LOW"
        results.append(FileRiskScore(
            path=path,
            score=score,
            tier=tier,
            is_new=meta["is_new"],
            is_test=meta["is_test"],
            is_security=meta["is_sec"],
            lines_changed=meta["lines"],
            language=meta["lang"],
        ))

    return results


# ── Strategy selection ────────────────────────────────────────────────────

def select_strategy(
    diff: str,
    medium_threshold: int = 400,
    large_threshold: int = 2000,
    *,
    adaptive: bool = True,
) -> ReviewStrategy:
    """Select the review tier based on diff size.

    If adaptive=False always returns TIER1 (current single-pass behaviour).
    """
    if not adaptive:
        return ReviewStrategy.TIER1
    n = count_diff_lines(diff)
    if n >= large_threshold:
        return ReviewStrategy.TIER3
    if n >= medium_threshold:
        return ReviewStrategy.TIER2
    return ReviewStrategy.TIER1


# ── Chunking ──────────────────────────────────────────────────────────────

def _source_for_test(test_path: str, all_paths: set[str]) -> str | None:
    """Return the source file path corresponding to a test file, if present."""
    name = PurePosixPath(test_path).stem  # e.g. "test_parser" or "parser_test"
    # Strip common test prefixes/suffixes
    for prefix in ("test_",):
        if name.startswith(prefix):
            source_stem = name[len(prefix):]
            for p in all_paths:
                if PurePosixPath(p).stem == source_stem and not _is_test_file(p):
                    return p
    for suffix in ("_test",):
        if name.endswith(suffix):
            source_stem = name[: -len(suffix)]
            for p in all_paths:
                if PurePosixPath(p).stem == source_stem and not _is_test_file(p):
                    return p
    return None


def build_chunks(
    file_scores: list[FileRiskScore],
    full_diff: str,
    max_chunk_lines: int = 600,
) -> list[DiffChunk]:
    """Group files into DiffChunks using greedy packing.

    Sorting is by risk score descending so high-risk files appear first
    (primacy effect benefits review quality).

    Test files are co-located with their corresponding source file.
    """
    if not file_scores:
        return []

    file_sections = parse_diff_files(full_diff)
    all_paths = set(file_sections.keys())

    # Build a mapping: source_path → test_path for co-location
    test_to_source: dict[str, str] = {}
    for fs in file_scores:
        if fs.is_test:
            src = _source_for_test(fs.path, all_paths)
            if src:
                test_to_source[fs.path] = src

    # Scores as dict for quick lookup
    score_map: dict[str, float] = {fs.path: fs.score for fs in file_scores}

    # Files already assigned to a chunk
    assigned: set[str] = set()
    raw_chunks: list[list[str]] = []
    current_chunk: list[str] = []
    current_lines = 0

    def _flush_chunk():
        if current_chunk:
            raw_chunks.append(list(current_chunk))
            current_chunk.clear()

    for fs in file_scores:
        path = fs.path
        if path in assigned:
            continue

        file_lines = _count_file_lines(file_sections.get(path, ""))

        # Check if adding this file would overflow (allow 20% overflow for test co-location)
        overflow_limit = max_chunk_lines * 1.2

        if current_lines + file_lines > max_chunk_lines and current_chunk:
            _flush_chunk()
            current_lines = 0

        current_chunk.append(path)
        current_lines += file_lines
        assigned.add(path)

        # Co-locate test file with its source
        if not fs.is_test:
            # Find test file for this source
            for test_p, src_p in test_to_source.items():
                if src_p == path and test_p not in assigned:
                    test_lines = _count_file_lines(file_sections.get(test_p, ""))
                    if current_lines + test_lines <= overflow_limit:
                        current_chunk.append(test_p)
                        current_lines += test_lines
                        assigned.add(test_p)

    _flush_chunk()

    # Assign any remaining unassigned files (test files whose source was already assigned)
    remaining = [fs.path for fs in file_scores if fs.path not in assigned]
    if remaining:
        raw_chunks.append(remaining)

    # Build DiffChunk objects
    total = len(raw_chunks)
    chunks: list[DiffChunk] = []
    for idx, files in enumerate(raw_chunks):
        chunk_diff = "\n".join(
            file_sections[p] for p in files if p in file_sections
        )
        line_count = count_diff_lines(chunk_diff)
        # Risk label = tier of highest-scoring file in this chunk
        chunk_scores = {p: score_map.get(p, 0.0) for p in files}
        best_path = max(chunk_scores, key=lambda p: chunk_scores[p])
        best_tier = next(
            (fs.tier for fs in file_scores if fs.path == best_path), "LOW"
        )
        chunks.append(DiffChunk(
            index=idx + 1,
            total=total,
            files=files,
            diff_text=chunk_diff,
            line_count=line_count,
            risk_label=best_tier,
            risk_scores=chunk_scores,
        ))

    return chunks


# ── Risk map header (for Tier 2 prompt) ──────────────────────────────────

def build_risk_map_header(file_scores: list[FileRiskScore]) -> str:
    """Build the '## Review Priority Map' header injected into Tier 2 prompts."""
    if not file_scores:
        return ""

    total_lines = sum(fs.lines_changed for fs in file_scores)
    n = len(file_scores)

    lines = [
        "## Review Priority Map",
        "Files ordered by estimated risk. High-risk files deserve deepest attention.",
        "",
    ]

    for tier_label in ("HIGH", "MEDIUM", "LOW"):
        tier_files = [fs for fs in file_scores if fs.tier == tier_label]
        if not tier_files:
            continue
        directive = {
            "HIGH": "(review thoroughly)",
            "MEDIUM": "(review carefully)",
            "LOW": "(spot check)",
        }[tier_label]
        lines.append(f"{tier_label} {directive}:")
        for fs in tier_files:
            tags = []
            if fs.is_new:
                tags.append("new file")
            if fs.is_security:
                tags.append("security-adjacent")
            tag_str = f", {', '.join(tags)}" if tags else ""
            lang_str = fs.language.lstrip(".").upper() if fs.language else "?"
            lines.append(
                f"  ● {fs.path:<50s}({fs.lines_changed} lines{tag_str}, {lang_str})"
            )
        lines.append("")

    lines.append(f"Total: {total_lines} lines across {n} file{'s' if n != 1 else ''}.")
    return "\n".join(lines)


# ── Interface context extraction (for Tier 3 chunk reviewer) ─────────────

def extract_interface_context(
    chunk: DiffChunk,
    all_file_scores: list[FileRiskScore],
    full_diff: str,
    max_lines: int = 200,
) -> str:
    """Extract function/class signatures from files NOT in this chunk
    but imported by files in this chunk.

    Used to give chunk reviewers just enough cross-chunk type info
    without flooding them with the full diff.
    """
    file_sections = parse_diff_files(full_diff)
    chunk_file_set = set(chunk.files)

    # Collect import targets from chunk files
    imported_modules: set[str] = set()
    for path in chunk.files:
        section = file_sections.get(path, "")
        for line in section.splitlines():
            # Only look at added lines (the new code)
            if not line.startswith("+"):
                continue
            line = line[1:]  # strip leading +
            # Match: import X, from X import Y, from .X import Y
            m = re.match(r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", line.strip())
            if m:
                mod = (m.group(1) or m.group(2) or "").strip(".")
                if mod:
                    imported_modules.add(mod.split(".")[0])  # top-level module name

    # Find diff files NOT in this chunk that match imported module names
    all_paths = set(file_sections.keys())
    external_paths = all_paths - chunk_file_set
    relevant_paths: list[str] = []
    for path in external_paths:
        stem = PurePosixPath(path).stem
        if stem in imported_modules:
            relevant_paths.append(path)

    if not relevant_paths:
        return ""

    # Extract only def/class/async def lines from those files' diffs
    sig_lines: list[str] = []
    for path in relevant_paths:
        section = file_sections.get(path, "")
        file_sigs: list[str] = []
        for line in section.splitlines():
            stripped = line.lstrip("+ ")
            if re.match(r"^(?:def |class |async def )", stripped):
                file_sigs.append(f"  {stripped.rstrip()}")
        if file_sigs:
            sig_lines.append(f"# {path}")
            sig_lines.extend(file_sigs[:50])  # cap per-file to avoid one giant file dominating

    if not sig_lines:
        return ""

    # Trim to max_lines
    sig_lines = sig_lines[:max_lines]
    return (
        "## Interface Context (signatures from referenced files outside this chunk)\n"
        + "\n".join(sig_lines)
    )
```

- [ ] **Step 3.4: Run strategy tests**

```bash
python -m pytest forge/review/strategy_test.py -x -q 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 3.5: Run ruff on new file**

```bash
python -m ruff check forge/review/strategy.py forge/review/strategy_test.py
python -m ruff format forge/review/strategy.py forge/review/strategy_test.py
```

Fix any issues, then re-run tests.

- [ ] **Step 3.6: Commit**

```bash
git add forge/review/strategy.py forge/review/strategy_test.py
git commit -m "feat(review): add strategy.py — risk scoring, chunking, strategy selection"
```

---

## Task 4: Create `forge/review/synthesizer.py`

**Files:**
- Create: `forge/review/synthesizer.py`
- Create: `forge/review/synthesizer_test.py`

This module handles:
- The `ChunkReviewResult` dataclass (produced after reviewing each chunk)
- `review_chunk()` — runs one LLM call for a single `DiffChunk`, parses JSON response
- `synthesize_results()` — aggregates all `ChunkReviewResult`s into final `GateResult`

- [ ] **Step 4.1: Write failing tests first**

Create `forge/review/synthesizer_test.py`:

```python
"""Tests for forge.review.synthesizer — chunk review aggregation."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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
    raw = json.dumps({
        "verdict": "PASS",
        "confidence": 5,
        "issues": [],
        "cross_chunk_concerns": [],
        "summary": "All good.",
    })
    result = _parse_chunk_json(raw, chunk_index=1)
    assert result.verdict == "PASS"
    assert result.confidence == 5
    assert result.issues == []


def test_parse_chunk_json_fail_with_issues():
    raw = json.dumps({
        "verdict": "FAIL",
        "confidence": 4,
        "issues": [
            {"severity": "HIGH", "file": "foo.py", "line_hint": "~42", "description": "Bad thing"}
        ],
        "cross_chunk_concerns": [],
        "summary": "Found a bug.",
    })
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
    raw = json.dumps({
        "verdict": "UNCERTAIN",
        "confidence": 2,
        "issues": [],
        "cross_chunk_concerns": ["Possible issue in sibling file"],
        "summary": "Not sure.",
    })
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
    result = _make_result("FAIL", 4, issues=[
        {"severity": "HIGH", "file": "foo.py", "line_hint": "~10", "description": "Bug"}
    ])
    result.summary = "Found a bug."
    chunk = DiffChunk(index=1, total=2, files=["foo.py"], diff_text="", line_count=10,
                      risk_label="HIGH", risk_scores={"foo.py": 55.0})
    text = _format_chunks_for_synthesis([chunk], [result])
    assert "FAIL" in text
    assert "foo.py" in text
    assert "Bug" in text
```

- [ ] **Step 4.2: Run tests to confirm they fail**

```bash
python -m pytest forge/review/synthesizer_test.py -x -q 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'forge.review.synthesizer'`

- [ ] **Step 4.3: Implement `forge/review/synthesizer.py`**

```python
"""Tier 3 review: per-chunk LLM review and synthesis aggregation.

Each chunk is reviewed independently by a Claude agent producing structured
JSON. A final synthesis call aggregates findings into PASS/FAIL/UNCERTAIN.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from claude_code_sdk import ClaudeCodeOptions

from forge.core.sdk_helpers import sdk_query
from forge.review.pipeline import GateResult, ReviewCostInfo
from forge.review.strategy import DiffChunk, FileRiskScore, extract_interface_context
# _parse_review_result is imported from llm_review for the JSON fallback
from forge.review.llm_review import _parse_review_result

logger = logging.getLogger("forge.review")


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class ChunkReviewResult:
    """Result of reviewing a single DiffChunk."""

    chunk_index: int
    verdict: str                   # "PASS", "FAIL", "UNCERTAIN"
    confidence: int                # 1–5
    issues: list[dict]             # [{severity, file, line_hint, description}]
    cross_chunk_concerns: list[str]
    summary: str
    cost_info: ReviewCostInfo = field(default_factory=ReviewCostInfo)
    raw_text: str = ""
    timed_out: bool = False        # True if SDK/timeout failure (not a review verdict)


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

    Falls back to _parse_review_result() for plain PASS/FAIL/UNCERTAIN text.
    """
    text = raw_text.strip()

    # Try to parse JSON (may be wrapped in markdown code fence)
    json_text = text
    if "```" in text:
        m = __import__("re").search(r"```(?:json)?\s*([\s\S]+?)```", text)
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
        # Fallback: use existing plain-text verdict parser
        gate_result = _parse_review_result(text)
        verdict = "PASS" if gate_result.passed else (
            "UNCERTAIN" if gate_result.needs_human else "FAIL"
        )
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
    """
    if not results:
        return "UNCERTAIN", "No chunk results to aggregate."

    for r in results:
        if r.verdict == "UNCERTAIN":
            return "UNCERTAIN", f"Chunk {r.chunk_index} verdict is UNCERTAIN (confidence {r.confidence})."

    for r in results:
        if r.verdict == "FAIL":
            if r.confidence >= 3:
                return "FAIL", f"Chunk {r.chunk_index} FAIL (confidence {r.confidence}/5)."
            else:
                return "UNCERTAIN", (
                    f"Chunk {r.chunk_index} FAIL with low confidence ({r.confidence}/5) — "
                    "treating as UNCERTAIN."
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

        lines.append(
            f"**Chunk {result.chunk_index}/{results[-1].chunk_index if results else '?'}**"
            f"{risk}: {file_list}"
        )
        lines.append(f"  Verdict: {result.verdict} (confidence {result.confidence}/5)")
        lines.append(f"  Summary: {result.summary}")

        if result.issues:
            lines.append("  Issues:")
            for issue in result.issues:
                lines.append(
                    f"    - [{issue.get('severity','?')}] "
                    f"{issue.get('file','?')} {issue.get('line_hint','')}: "
                    f"{issue.get('description','')}"
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
    model: str = "sonnet",
    worktree_path: str | None = None,
    sibling_context: str | None = None,
    prior_feedback: str | None = None,
    on_message: Callable[[Any], Awaitable[None]] | None = None,
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,
) -> ChunkReviewResult:
    """Run one LLM review call for a single DiffChunk.

    Returns a ChunkReviewResult. On SDK error or timeout, retries once
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

    options = ClaudeCodeOptions(
        system_prompt=CHUNK_REVIEW_SYSTEM_PROMPT,
        max_turns=40,
        model=model,
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="acceptEdits",
    )
    if worktree_path:
        options.cwd = worktree_path

    cost_info = ReviewCostInfo()
    max_attempts = 2

    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                sdk_query(prompt=prompt, options=options, on_message=on_message),
                timeout=600,
            )
        except (TimeoutError, Exception) as exc:
            logger.warning(
                "Chunk %d/%d review failed on attempt %d/%d: %s",
                chunk.index, chunk.total, attempt, max_attempts, exc,
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

        if result:
            cost_info.add(ReviewCostInfo(
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            ))

        raw_text = result.result if result and result.result else ""
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
        chunk_index=chunk.index, verdict="UNCERTAIN", confidence=1,
        issues=[], cross_chunk_concerns=[], summary="Unreachable fallback",
        cost_info=cost_info, raw_text="", timed_out=True,
    )


# ── Synthesis ─────────────────────────────────────────────────────────────

async def synthesize_results(
    chunks: list[DiffChunk],
    chunk_results: list[ChunkReviewResult],
    task_title: str,
    task_description: str,
    *,
    model: str = "sonnet",
    worktree_path: str | None = None,
    prior_feedback: str | None = None,
    delta_diff: str | None = None,
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,
) -> tuple[GateResult, ReviewCostInfo]:
    """Aggregate chunk review results into a final GateResult.

    First applies deterministic synthesis rules (Section 6.4 of the spec).
    Then runs a synthesis LLM call for consolidated human-readable feedback.
    On synthesis failure, falls back to rule-based verdict with raw chunk summaries.
    """
    # Check for any timed-out chunks — escalate to human immediately
    timed_out = [r for r in chunk_results if r.timed_out]
    if timed_out:
        failed_files = []
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
    all_issues = _deduplicate_issues(
        [issue for r in chunk_results for issue in r.issues]
    )
    all_cross = list({c for r in chunk_results for c in r.cross_chunk_concerns})

    parts = [
        f"Task: {task_title}\nDescription: {task_description}\n\n",
        chunk_summary,
    ]
    if all_cross:
        parts.append("## Cross-Chunk Concerns\n" + "\n".join(f"- {c}" for c in all_cross) + "\n\n")
    if prior_feedback:
        parts.append(
            "=== PRIOR REVIEW FEEDBACK ===\n"
            f"{prior_feedback[:3000]}\n\n"
        )
    if delta_diff:
        parts.append(
            "=== CHANGES SINCE LAST REVIEW ===\n"
            f"```diff\n{delta_diff[:6000]}\n```\n\n"
        )
    parts.append(
        f"Pre-analysis: {pre_verdict} ({pre_reason})\n\n"
        "Produce the final PASS/FAIL/UNCERTAIN verdict with consolidated feedback."
    )

    prompt = "".join(parts)
    options = ClaudeCodeOptions(
        system_prompt=SYNTHESIS_SYSTEM_PROMPT,
        max_turns=5,           # Synthesis is a simple aggregation, not agentic exploration
        model=model,
        allowed_tools=[],      # No tools — synthesis reads chunk summaries only
        permission_mode="acceptEdits",
    )
    if worktree_path:
        options.cwd = worktree_path

    total_cost = ReviewCostInfo()
    for r in chunk_results:
        total_cost.add(r.cost_info)

    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                sdk_query(prompt=prompt, options=options, on_message=None),
                timeout=120,
            )
        except (TimeoutError, Exception) as exc:
            logger.warning("Synthesis attempt %d/%d failed: %s", attempt, max_attempts, exc)
            if attempt == max_attempts:
                # Fallback: use rule-based verdict + raw chunk summaries
                return _synthesis_fallback(
                    chunk_results, chunks, pre_verdict, total_cost
                )
            await asyncio.sleep(2**attempt)
            continue

        if result:
            total_cost.add(ReviewCostInfo(
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            ))
        raw = result.result if result and result.result else ""
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
    model: str = "sonnet",
    worktree_path: str | None = None,
    sibling_context: str | None = None,
    prior_feedback: str | None = None,
    delta_diff: str | None = None,
    on_message: Callable[[Any], Awaitable[None]] | None = None,
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,
) -> tuple[GateResult, ReviewCostInfo]:
    """Run all chunk reviews sequentially then synthesize.

    Sequential (not parallel) to avoid rate-limit floods on large diffs.
    """
    chunk_results: list[ChunkReviewResult] = []

    for chunk in chunks:
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
        )
        chunk_results.append(result)

        # Abort early if a chunk timed out — no point reviewing more chunks
        if result.timed_out:
            # Fill remaining chunks with placeholder results
            for remaining in chunks[chunk.index:]:  # chunk.index is 1-based
                chunk_results.append(ChunkReviewResult(
                    chunk_index=remaining.index,
                    verdict="UNCERTAIN",
                    confidence=1,
                    issues=[],
                    cross_chunk_concerns=[],
                    summary="Skipped due to prior chunk timeout.",
                    timed_out=True,
                ))
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
    )
```

- [ ] **Step 4.4: Run synthesizer tests**

```bash
python -m pytest forge/review/synthesizer_test.py -x -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 4.5: Run ruff**

```bash
python -m ruff check forge/review/synthesizer.py forge/review/synthesizer_test.py
python -m ruff format forge/review/synthesizer.py forge/review/synthesizer_test.py
```

- [ ] **Step 4.6: Commit**

```bash
git add forge/review/synthesizer.py forge/review/synthesizer_test.py
git commit -m "feat(review): add synthesizer.py — chunk reviewer, synthesis aggregation, Tier 3 orchestration"
```

---

## Task 5: Modify `llm_review.py` — strategy dispatcher + Tier 2 + Tier 3

**Files:**
- Modify: `forge/review/llm_review.py`
- Modify: `forge/review/llm_review_test.py`

- [ ] **Step 5.1: Add `on_review_event` parameter + strategy dispatcher to `gate2_llm_review()`**

Find the `gate2_llm_review` function signature (around line 91) and replace it:

```python
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
    # NEW: callback for review progress events (strategy_selected, chunk_started, etc.)
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,
    # NEW: adaptive review config (from ReviewConfig)
    adaptive_review: bool = True,
    medium_diff_threshold: int = 400,
    large_diff_threshold: int = 2000,
    max_chunk_lines: int = 600,
) -> tuple[GateResult, ReviewCostInfo]:
```

- [ ] **Step 5.2: Add strategy dispatch at the start of `gate2_llm_review()` body**

Right after the `if not diff.strip():` early-return guard (around line 126), add:

```python
    # ── Strategy selection ────────────────────────────────────────────────
    from forge.review.strategy import (
        ReviewStrategy,
        build_chunks,
        build_risk_map_header,
        score_files,
        select_strategy,
    )
    from forge.review.synthesizer import run_chunked_review

    strategy = select_strategy(
        diff,
        medium_diff_threshold,
        large_diff_threshold,
        adaptive=adaptive_review,
    )

    if on_review_event:
        from forge.review.strategy import count_diff_lines
        payload: dict = {
            "strategy": strategy.value,
            "diff_lines": count_diff_lines(diff),
        }
        if strategy == ReviewStrategy.TIER3:
            # Pre-compute chunk count for TUI
            _scores = score_files(diff)
            _chunks = build_chunks(_scores, diff, max_chunk_lines)
            payload["chunk_count"] = len(_chunks)
        await on_review_event("review:strategy_selected", payload)

    # ── Tier 3: multi-chunk map-reduce ────────────────────────────────────
    if strategy == ReviewStrategy.TIER3:
        file_scores = score_files(diff)
        chunks = build_chunks(file_scores, diff, max_chunk_lines)
        return await run_chunked_review(
            chunks,
            file_scores,
            diff,
            task_title,
            task_description,
            model=model,
            worktree_path=worktree_path,
            sibling_context=sibling_context,
            prior_feedback=prior_feedback,
            delta_diff=delta_diff,
            on_message=on_message,
            on_review_event=on_review_event,
        )
```

- [ ] **Step 5.3: Add Tier 2 risk map injection to `_build_review_prompt()`**

Add `risk_map_header: str = ""` parameter to `_build_review_prompt()` and inject it just before the diff:

Find the function signature:
```python
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
) -> str:
```

Replace with:
```python
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
    risk_map_header: str = "",  # NEW: Tier 2 risk prioritisation header
) -> str:
```

Then inside the function body, find:
```python
    parts.append(
        f"Git diff of changes:\n```diff\n{diff}\n```\n\n",
    )
```

Replace with:
```python
    if risk_map_header:
        parts.append(f"{risk_map_header}\n\n")
    parts.append(
        f"Git diff of changes:\n```diff\n{diff}\n```\n\n",
    )
```

- [ ] **Step 5.4: Wire Tier 2 in the `gate2_llm_review()` function body**

In `gate2_llm_review()`, find where `_build_review_prompt()` is called (around line 132):
```python
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
    )
```

Replace with:
```python
    # Tier 2: build risk map header (pure Python, no LLM cost)
    risk_map = ""
    if strategy == ReviewStrategy.TIER2:
        file_scores = score_files(diff)
        risk_map = build_risk_map_header(file_scores)

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
        risk_map_header=risk_map,
    )
```

Also add `review_strategy=strategy.value` to the `GateResult` returned at line ~216:
```python
        result_gate = _parse_review_result(result_text)
        result_gate.review_strategy = strategy.value
        return result_gate, cost_info
```

And similarly for all other `GateResult` return paths in the function (timeout, SDK error, empty response) — add `review_strategy=strategy.value` to each.

- [ ] **Step 5.5: Add Tier 2 and dispatcher tests to `llm_review_test.py`**

Open `forge/review/llm_review_test.py` and add:

```python
def test_tier2_prompt_contains_risk_map(monkeypatch):
    """When diff is in Tier 2 range, _build_review_prompt includes risk map header."""
    from forge.review.llm_review import _build_review_prompt
    from forge.review.strategy import build_risk_map_header, score_files

    # Build a ~600-line diff (Tier 2 range)
    diff = "\n".join(
        [
            f"diff --git a/module{i}.py b/module{i}.py",
            "new file mode 100644",
            "--- /dev/null",
            f"+++ b/module{i}.py",
            "@@ -0,0 +1,30 @@",
        ]
        + [f"+line{j}" for j in range(30)]
        for i in range(20)
    )
    file_scores = score_files(diff)
    risk_map = build_risk_map_header(file_scores)
    prompt = _build_review_prompt("title", "desc", diff, risk_map_header=risk_map)
    assert "Review Priority Map" in prompt
    assert "HIGH" in prompt or "MEDIUM" in prompt


def test_tier2_prompt_without_risk_map():
    """Without risk_map_header, prompt behaves exactly as before."""
    from forge.review.llm_review import _build_review_prompt
    prompt = _build_review_prompt("title", "desc", "diff --git a/x b/x\n+line\n")
    assert "Review Priority Map" not in prompt


def test_strategy_selection_tier1_no_on_review_event():
    """For Tier 1 diff, no extra code paths are taken."""
    from forge.review.strategy import select_strategy, ReviewStrategy
    diff = "diff --git a/x b/x\n+line\n"
    assert select_strategy(diff, 400, 2000) == ReviewStrategy.TIER1
```

- [ ] **Step 5.6: Run all review tests**

```bash
python -m pytest forge/review/ -x -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 5.7: Run ruff**

```bash
python -m ruff check forge/review/llm_review.py
python -m ruff format forge/review/llm_review.py
```

- [ ] **Step 5.8: Commit**

```bash
git add forge/review/llm_review.py forge/review/llm_review_test.py
git commit -m "feat(review): add strategy dispatcher, Tier 2 risk-map injection, Tier 3 orchestration to gate2_llm_review"
```

---

## Task 6: Modify `daemon_review.py` — emit new events, pass `on_review_event`

**Files:**
- Modify: `forge/core/daemon_review.py`

- [ ] **Step 6.1: Find the `_make_review_on_message` method and add `_make_review_event_callback`**

In `daemon_review.py`, right after `_make_review_on_message` (around line 688), add a new method to `ReviewMixin` (or wherever `_run_review` lives):

```python
def _make_review_event_callback(self, task_id: str, db, pipeline_id: str):
    """Build an on_review_event callback that translates review progress events
    into WebSocket events for the TUI.

    This decouples llm_review.py from the WebSocket/DB layer.
    """
    async def _on_review_event(event_name: str, payload: dict) -> None:
        # Inject task_id into every event payload
        full_payload = {"task_id": task_id, **payload}
        await self._emit(event_name, full_payload, db=db, pipeline_id=pipeline_id)

    return _on_review_event
```

- [ ] **Step 6.2: Wire `on_review_event` into the `gate2_llm_review` call**

Find the `gate2_llm_review` call in `_run_review()` (around line 1083):

```python
            gate2_result, review_cost_info = await gate2_llm_review(
                task.title,
                task.description,
                diff,
                worktree_path,
                model=reviewer_model,
                prior_feedback=prior_feedback,
                prior_diff=prior_diff,
                project_context=self._snapshot.format_for_reviewer() if self._snapshot else "",
                allowed_files=task.files,
                delta_diff=delta_diff,
                sibling_context=sibling_context,
                custom_review_focus=custom_review_focus,
                on_message=on_message,
            )
```

Replace with:

```python
            # Build on_review_event callback for progress events (strategy, chunk progress)
            on_review_event = self._make_review_event_callback(task.id, db, pipeline_id)

            # Load adaptive review settings from project config
            _review_cfg = self._project_config.review if hasattr(self._project_config, "review") else None

            gate2_result, review_cost_info = await gate2_llm_review(
                task.title,
                task.description,
                diff,
                worktree_path,
                model=reviewer_model,
                prior_feedback=prior_feedback,
                prior_diff=prior_diff,
                project_context=self._snapshot.format_for_reviewer() if self._snapshot else "",
                allowed_files=task.files,
                delta_diff=delta_diff,
                sibling_context=sibling_context,
                custom_review_focus=custom_review_focus,
                on_message=on_message,
                on_review_event=on_review_event,
                adaptive_review=_review_cfg.adaptive_review if _review_cfg else True,
                medium_diff_threshold=_review_cfg.medium_diff_threshold if _review_cfg else 400,
                large_diff_threshold=_review_cfg.large_diff_threshold if _review_cfg else 2000,
                max_chunk_lines=_review_cfg.max_chunk_lines if _review_cfg else 600,
            )
```

NOTE: Check what `self._project_config` is actually called in `daemon_review.py` — run `grep -n "_project_config\|self\._settings\|self\._config" forge/core/daemon_review.py | head -20` and use the correct attribute name.

- [ ] **Step 6.3: Register new events in TUI_EVENT_TYPES**

Find where `TUI_EVENT_TYPES` is defined (run `grep -rn "TUI_EVENT_TYPES" forge/`). Add the 4 new event names to the list:

```python
"review:strategy_selected",
"review:chunk_started",
"review:chunk_complete",
"review:synthesis_started",
```

- [ ] **Step 6.4: Run daemon_review tests**

```bash
python -m pytest forge/core/daemon_review_test.py -x -q 2>&1 | tail -20
```

Expected: all pass. If `_make_review_event_callback` is not accessible to tests, it's a method — just verify existing tests don't break.

- [ ] **Step 6.5: Commit**

```bash
git add forge/core/daemon_review.py
git commit -m "feat(daemon): wire on_review_event callback + adaptive review config into _run_review"
```

---

## Task 7: TUI — display chunk progress and strategy in the review card

**Files:**
- Modify: `forge/tui/app.py` (event type registration + state handling)
- Modify: `forge/tui/widgets/chat_thread.py` (add `format_review_progress()`)

NOTE: Before implementing, run:
```bash
grep -n "review:llm_output\|review:gate\|review:strategy\|apply_event\|TUI_EVENT_TYPES" forge/tui/app.py | head -30
grep -n "class.*State\|apply_event\|review" forge/tui/app.py | head -30
```
to understand exactly how the TUI state machine handles events. The patterns below assume the existing event-bus architecture described in the exploration.

- [ ] **Step 7.1: Add new event type registrations**

Find where `TUI_EVENT_TYPES` is defined and add the 4 new events if not already done in Task 6.

- [ ] **Step 7.2: Add review progress state to the TUI state**

Find the TUI state class (likely in `forge/tui/state.py` or similar — run `grep -rn "class.*State\|review_lines\|review_output" forge/tui/ | head -20`).

Add review progress tracking to the task state dict or wherever review state is kept:

```python
# In the task state structure (wherever per-task TUI state is stored):
"review_strategy": None,      # "tier1", "tier2", "tier3"
"review_chunk_count": None,   # int, Tier 3 only
"review_chunks": {},          # {chunk_index: {"files": [...], "verdict": None, "risk_label": "?"}}
"review_current_chunk": None, # int, currently-being-reviewed chunk index
```

Then in `apply_event` (or equivalent), handle the new events:

```python
elif event_type == "review:strategy_selected":
    task_state["review_strategy"] = data.get("strategy")
    task_state["review_chunk_count"] = data.get("chunk_count")

elif event_type == "review:chunk_started":
    idx = data.get("chunk_index")
    task_state["review_current_chunk"] = idx
    task_state["review_chunks"][idx] = {
        "files": data.get("files", []),
        "verdict": None,
        "risk_label": data.get("risk_label", "?"),
    }

elif event_type == "review:chunk_complete":
    idx = data.get("chunk_index")
    if idx in task_state["review_chunks"]:
        task_state["review_chunks"][idx]["verdict"] = data.get("verdict")
    task_state["review_current_chunk"] = None

elif event_type == "review:synthesis_started":
    task_state["review_current_chunk"] = "synthesis"
```

- [ ] **Step 7.3: Add `format_review_progress()` to `chat_thread.py`**

Add this function to `forge/tui/widgets/chat_thread.py` (after `format_question_card`):

```python
def format_review_progress(
    strategy: str | None,
    diff_lines: int | None,
    chunks: dict,          # {chunk_index: {"files": [...], "verdict": str|None, "risk_label": str}}
    current_chunk: int | str | None,
    chunk_count: int | None,
) -> str:
    """Format review progress header for Tier 2/3 reviews.

    Returns empty string for Tier 1 (no special display needed).
    """
    from forge.tui.theme import ACCENT_BLUE, ACCENT_ORANGE, TEXT_MUTED, TEXT_SECONDARY

    if not strategy or strategy == "tier1":
        return ""

    lines_str = f"{diff_lines} lines · " if diff_lines else ""

    if strategy == "tier2":
        # Just show the tier label — the risk map is already in the review text
        return f"[{TEXT_SECONDARY}]  ({lines_str}Risk-Enhanced)[/]"

    if strategy != "tier3":
        return ""

    # Tier 3: show chunk grid
    header = f"[{TEXT_SECONDARY}]  ({lines_str}Chunked · {chunk_count or len(chunks)} chunks)[/]"
    parts = [header]

    for idx in sorted(chunks.keys()):
        chunk = chunks[idx]
        files = chunk.get("files", [])
        file_preview = ", ".join(str(f).split("/")[-1] for f in files[:3])
        if len(files) > 3:
            file_preview += f" +{len(files) - 3}"

        verdict = chunk.get("verdict")
        risk = chunk.get("risk_label", "?")
        total = chunk_count or len(chunks)

        if verdict == "PASS":
            icon = "[green]✓[/]"
            verdict_str = f"[green]{verdict}[/]"
        elif verdict == "FAIL":
            icon = "[red]✗[/]"
            verdict_str = f"[red]{verdict}[/]"
        elif verdict in ("UNCERTAIN", "TIMEOUT"):
            icon = "[yellow]?[/]"
            verdict_str = f"[yellow]{verdict}[/]"
        elif current_chunk == idx:
            icon = f"[{ACCENT_BLUE}]⟳[/]"
            verdict_str = f"[{ACCENT_BLUE}]reviewing...[/]"
        else:
            icon = f"[{TEXT_MUTED}]○[/]"
            verdict_str = ""

        risk_badge = f"[{TEXT_MUTED}][{risk}][/]" if risk else ""
        chunk_line = (
            f"  {icon} Chunk {idx}/{total} {risk_badge} · "
            f"[{TEXT_SECONDARY}]{_escape(file_preview)}[/]"
        )
        if verdict_str:
            chunk_line += f"  {verdict_str}"
        parts.append(chunk_line)

    if current_chunk == "synthesis":
        parts.append(f"  [{ACCENT_BLUE}]⟳ Synthesizing results...[/]")

    return "\n".join(parts)
```

- [ ] **Step 7.4: Update wherever the review card header is displayed in the TUI**

Run:
```bash
grep -rn "format_review\|review:llm_output\|Reviewing\|review_strategy" forge/tui/ | head -20
```

Find where the "━━━ Reviewing ━━━" header is rendered and update it to call `format_review_progress()` with the task's review state when strategy is tier2 or tier3.

The exact change depends on the TUI architecture. The pattern is:
1. The TUI state stores `review_strategy`, `review_chunks`, etc. per task
2. When displaying the review card for a task, call `format_review_progress(...)` to generate the header
3. Append streaming review text below the header

- [ ] **Step 7.5: Run all TUI tests**

```bash
python -m pytest forge/tui/ -x -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 7.6: Commit**

```bash
git add forge/tui/app.py forge/tui/widgets/chat_thread.py forge/tui/
git commit -m "feat(tui): display review strategy and chunk progress in review card"
```

---

## Task 8: Final integration — run full test suite, fix ruff, verify no regressions

**Files:**
- All modified files (ruff check)

- [ ] **Step 8.1: Run all tests**

```bash
python -m pytest forge/ -x -q 2>&1 | tail -30
```

Expected: all tests pass. Fix any failures.

- [ ] **Step 8.2: Run ruff on all modified files**

```bash
python -m ruff check forge/review/ forge/config/project_config.py forge/core/daemon_review.py forge/tui/
python -m ruff format forge/review/ forge/config/project_config.py forge/core/daemon_review.py forge/tui/
```

Fix any lint issues, re-run tests.

- [ ] **Step 8.3: Verify Tier 1 path is unchanged**

Check that a diff with < 400 lines goes through the exact same code path as before:

```python
# Verify in llm_review.py: strategy == TIER1 means the function body below the
# dispatch block is identical to the old behavior.
# Manually trace: TIER1 → no on_review_event dispatch → old _build_review_prompt call → same result.
```

- [ ] **Step 8.4: Run test suite one more time clean**

```bash
python -m pytest forge/ -q 2>&1 | tail -10
```

Expected: all pass, 0 errors.

- [ ] **Step 8.5: Final commit**

```bash
git add -A -- ':(exclude).venv' ':(exclude)venv' ':(exclude)node_modules' ':(exclude)__pycache__' ':(exclude).ruff_cache'
git commit -m "feat(review): adaptive review scaling — Tier 2 risk-enhanced + Tier 3 multi-chunk map-reduce

- Tier 1 (<400 lines): unchanged single-pass behavior
- Tier 2 (400-2000 lines): pure-Python risk scorer injects priority map into reviewer prompt
- Tier 3 (>2000 lines): greedy chunker groups files by risk + co-locates test/source pairs;
  sequential chunk reviews produce structured JSON; synthesis aggregates into final verdict
- 4 new WebSocket events: review:strategy_selected, review:chunk_started,
  review:chunk_complete, review:synthesis_started
- TUI: chunk progress grid with PASS/FAIL/reviewing indicators
- All thresholds configurable via forge.toml [review] section
- Tier 1 path: zero behavioral change (verified)"
```

---

## Self-Review

**Spec coverage check:**

| Spec Section | Task |
|---|---|
| §3 Adaptive Tier Strategy — enum, thresholds | T3 (strategy.py) |
| §4 Tier 1 unchanged | T5 (dispatcher falls through to old code) |
| §5.1 Risk scoring formula (6 signals) | T3 (score_files) |
| §5.2 Risk map injection into Tier 2 prompt | T5 (_build_review_prompt + risk_map_header) |
| §6.1 Sequential chunks, no contamination | T4 (run_chunked_review loop) |
| §6.2 Chunking algorithm (greedy, test+source) | T3 (build_chunks) |
| §6.3 Structured JSON output, interface context | T4 (review_chunk + extract_interface_context) |
| §6.4 Synthesis verdict rules, confidence scale | T4 (_apply_synthesis_rules) |
| §6.5 Retry with prior_feedback in chunks | T4 (prior_feedback passed to review_chunk) |
| §7.1 on_review_event callback | T5 (gate2_llm_review signature) + T6 (_make_review_event_callback) |
| §7.2 Four new WebSocket events | T6 (emit in _make_review_event_callback) |
| §8 TUI chunk progress display | T7 (format_review_progress + app.py state) |
| §9 forge.toml configuration | T2 (ReviewConfig) |
| §10 Data structures (all 5) | T1 (GateResult), T3 (FileRiskScore, DiffChunk, ReviewStrategy), T4 (ChunkReviewResult) |
| §11 Module layout + import graph | T1-T4 (file creation) |
| §12 Fallback chain (Tier3→Tier2→Tier1) | T4 (_synthesis_fallback) + T5 (error paths) |
| §13 Tests | T3 (strategy_test), T4 (synthesizer_test), T5 (llm_review_test) |
| §14 Non-regression (Tier1 unchanged, adaptive=false) | T5 (strategy selection), T3 (select_strategy adaptive=False) |

**All spec sections covered.**

**Type consistency check:**
- `DiffChunk` defined in T3 (strategy.py), used in T4 (synthesizer.py) ✓
- `FileRiskScore` defined in T3, used in T4 ✓
- `ChunkReviewResult` defined in T4, not imported elsewhere ✓
- `ReviewCostInfo` moved to pipeline.py in T1, re-exported from llm_review.py ✓
- `GateResult.review_strategy/chunk_count/chunk_verdicts` added in T1, set in T4+T5 ✓
- `on_review_event: Callable[[str, dict], Awaitable[None]]` signature used consistently in T4, T5, T6 ✓
- `select_strategy(diff, medium, large, *, adaptive)` defined in T3, called in T5 ✓
- `build_chunks(file_scores, full_diff, max_chunk_lines)` defined in T3, called in T5 ✓
- `run_chunked_review(chunks, all_file_scores, full_diff, ...)` defined in T4, called in T5 ✓

**No placeholder scan issues found** — all code is complete and concrete.
