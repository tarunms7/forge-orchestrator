"""Diff chunking and review strategy selection for the LLM review pipeline.

This module decides HOW to review a diff:
- TIER1: single-pass review (small diffs)
- TIER2: per-file review (medium diffs)
- TIER3: chunked review (large diffs)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath


class ReviewStrategy(Enum):
    """How to split a diff across review calls."""

    TIER1 = "tier1"  # Single pass — diff fits comfortably in one prompt
    TIER2 = "tier2"  # Per-file — send each file as its own review call
    TIER3 = "tier3"  # Chunked — group related files into review chunks


# Default thresholds (changed lines, not total lines)
DEFAULT_MEDIUM_THRESHOLD = 400  # above this → TIER2
DEFAULT_LARGE_THRESHOLD = 2000  # above this → TIER3
DEFAULT_DEEP_REVIEW_RISK_THRESHOLD = 8.0
DEFAULT_DEEP_REVIEW_LINES_THRESHOLD = 120


def count_diff_lines(diff: str) -> int:
    """Count the number of changed lines (+/-) in a diff, excluding headers."""
    if not diff:
        return 0
    count = 0
    for line in diff.splitlines():
        if (line.startswith("+") and not line.startswith("+++")) or (
            line.startswith("-") and not line.startswith("---")
        ):
            count += 1
    return count


def parse_diff_files(diff: str) -> dict[str, str]:
    """Split a unified diff into per-file diffs.

    Returns a mapping of file path → the portion of the diff for that file.
    """
    if not diff or not diff.strip():
        return {}

    result: dict[str, str] = {}
    current_path: str | None = None
    current_lines: list[str] = []

    for line in diff.splitlines(keepends=True):
        # Detect start of a new file section
        if line.startswith("diff --git "):
            if current_path is not None:
                result[current_path] = "".join(current_lines)
            current_path = _extract_path_from_diff_header(line)
            current_lines = [line]
        else:
            if current_path is not None:
                current_lines.append(line)

    if current_path is not None:
        result[current_path] = "".join(current_lines)

    return result


def _extract_path_from_diff_header(line: str) -> str:
    """Extract the b/ path from a 'diff --git a/... b/...' header line."""
    match = re.search(r" b/(.+)$", line.rstrip())
    if match:
        return match.group(1)
    # Fallback: use the a/ path
    match = re.search(r" a/(.+) b/", line)
    if match:
        return match.group(1)
    return line.strip()


def select_strategy(
    diff: str,
    medium_threshold: int = DEFAULT_MEDIUM_THRESHOLD,
    large_threshold: int = DEFAULT_LARGE_THRESHOLD,
    adaptive: bool = True,
) -> ReviewStrategy:
    """Select a review strategy based on diff size.

    Args:
        diff: The full unified diff text.
        medium_threshold: Changed-line count above which TIER2 is used.
        large_threshold: Changed-line count above which TIER3 is used.
        adaptive: If False, always return TIER1 (disable adaptive review).

    Returns:
        The appropriate ReviewStrategy.
    """
    if not adaptive:
        return ReviewStrategy.TIER1
    n = count_diff_lines(diff)
    if n >= large_threshold:
        return ReviewStrategy.TIER3
    if n >= medium_threshold:
        return ReviewStrategy.TIER2
    return ReviewStrategy.TIER1


def should_deepen_small_diff_review(
    diff: str,
    *,
    file_scores: list[FileRiskScore] | None = None,
    risk_threshold: float = DEFAULT_DEEP_REVIEW_RISK_THRESHOLD,
    line_threshold: int = DEFAULT_DEEP_REVIEW_LINES_THRESHOLD,
) -> bool:
    """Return True when a nominally-small diff deserves chunked Tier 2 review.

    Small diffs can still hide subtle bugs when they touch high-risk code or make
    substantial changes in a few files. This helper promotes those cases out of a
    single-pass review without forcing every tiny diff through synthesis.
    """
    if not diff or not diff.strip():
        return False

    file_scores = file_scores if file_scores is not None else score_files(diff)
    if not file_scores:
        return False

    if any(fs.score >= risk_threshold for fs in file_scores):
        return True

    changed_source_files = [fs for fs in file_scores if not _is_test_file(fs.path)]
    if len(changed_source_files) >= 2 and count_diff_lines(diff) >= line_threshold:
        return True

    return False


@dataclass
class FileScore:
    """A file path with an associated importance score for chunking."""

    path: str
    score: float = 1.0
    line_count: int = 0


@dataclass
class ReviewChunk:
    """A group of files to review together."""

    paths: list[str]
    diff_text: str
    chunk_index: int
    total_chunks: int
    label: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class FileRiskScore:
    """A file path with an associated risk score (0–100) for Tier 3 chunking."""

    path: str
    score: float = 0.0
    line_count: int = 0


@dataclass
class DiffChunk:
    """A risk-annotated chunk of files for Tier 3 per-chunk review."""

    index: int  # 1-based chunk index
    total: int  # total number of chunks
    files: list[str]  # file paths in this chunk
    diff_text: str  # combined diff for this chunk
    line_count: int  # changed-line count
    risk_label: str  # "HIGH", "MEDIUM", "LOW"
    risk_scores: dict[str, float] = field(default_factory=dict)  # file → score


def extract_interface_context(
    chunk: DiffChunk,
    all_file_scores: list[FileRiskScore],
    full_diff: str,
) -> str:
    """Return cross-chunk context for files referenced by this chunk.

    Tier 3 reviewers often need lightweight type and structure information
    from sibling files to judge tests, registries, and shared base modules
    without escalating to UNCERTAIN. We therefore provide:
    - a compact sibling-file list
    - structural snippets from imported sibling files that also changed
    """
    per_file_sections = parse_diff_files(full_diff)
    chunk_file_set = set(chunk.files)
    sibling_files = [fs.path for fs in all_file_scores if fs.path not in chunk_file_set]
    if not sibling_files:
        return ""

    import_tokens = _collect_import_tokens(chunk.files, per_file_sections)
    relevant_paths = _find_relevant_sibling_paths(sibling_files, import_tokens)

    lines = ["## Sibling Files (reviewed in other chunks — do not flag missing integration here)"]
    shown = sibling_files[:30]
    extra = len(sibling_files) - len(shown)
    for path in shown:
        lines.append(f"  - {path}")
    if extra > 0:
        lines.append(f"  … and {extra} more")

    if relevant_paths:
        lines.append("")
        lines.append("## Interface Context (structural lines from referenced sibling files)")
        max_lines = 120
        emitted = 0
        for path in relevant_paths:
            structural = _extract_structural_lines(per_file_sections.get(path, ""))
            if not structural:
                continue
            lines.append(f"# {path}")
            for entry in structural:
                lines.append(f"  {entry}")
                emitted += 1
                if emitted >= max_lines:
                    lines.append("  … truncated")
                    return "\n".join(lines)

    return "\n".join(lines)


_IMPORT_RE = re.compile(r"^(?:from\s+([.\w]+)\s+import|import\s+([.\w]+))")
_STRUCTURAL_RE = re.compile(
    r"^(?:@dataclass(?:\([^)]*\))?|class |def |async def |[A-Za-z_][A-Za-z0-9_]*\s*:|[A-Z][A-Z0-9_]*\s*=)"
)


def _strip_diff_prefix(line: str) -> str | None:
    """Strip unified-diff prefixes while skipping headers and hunk markers."""
    if not line:
        return None
    if line.startswith(("diff --git ", "index ", "--- ", "+++ ", "@@")):
        return None
    if line[0] in "+- ":
        return line[1:]
    return line


def _collect_import_tokens(
    chunk_files: list[str],
    per_file_sections: dict[str, str],
) -> set[str]:
    """Collect module tokens imported by files in this chunk."""
    tokens: set[str] = set()
    for path in chunk_files:
        section = per_file_sections.get(path, "")
        for raw_line in section.splitlines():
            line = _strip_diff_prefix(raw_line)
            if line is None:
                continue
            match = _IMPORT_RE.match(line.strip())
            if not match:
                continue
            module = (match.group(1) or match.group(2) or "").strip(".")
            if not module:
                continue
            for token in module.split("."):
                lowered = token.lower()
                if lowered and lowered not in {"forge", "tests", "test"}:
                    tokens.add(lowered)
    return tokens


def _find_relevant_sibling_paths(sibling_files: list[str], import_tokens: set[str]) -> list[str]:
    """Find changed sibling files whose path components match imported modules."""
    if not import_tokens:
        return []

    relevant: list[str] = []
    for path in sibling_files:
        pure = PurePosixPath(path)
        path_tokens = {part.lower() for part in pure.parts}
        path_tokens.add(pure.stem.lower())
        if path_tokens & import_tokens:
            relevant.append(path)
    return relevant


def _extract_structural_lines(section: str) -> list[str]:
    """Extract structural lines that help reviewers reason across chunks."""
    structural: list[str] = []
    for raw_line in section.splitlines():
        line = _strip_diff_prefix(raw_line)
        if line is None:
            continue
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        if _STRUCTURAL_RE.match(stripped.lstrip()):
            structural.append(stripped)
    return structural


def _is_test_file(path: str) -> bool:
    """Return True if the path looks like a test file."""
    p = path.lower()
    return (
        p.endswith("_test.py")
        or p.endswith("_test.ts")
        or p.endswith("_test.js")
        or p.endswith("test_.py")
        or "/test_" in p
        or "/tests/" in p
        or p.startswith("tests/")
        or p.endswith(".test.py")
        or p.endswith(".spec.py")
        or p.endswith(".test.ts")
        or p.endswith(".spec.ts")
        or p.endswith(".test.tsx")
        or p.endswith(".spec.tsx")
        or p.endswith(".test.jsx")
        or p.endswith(".spec.jsx")
        or "/__tests__/" in p
        or p.startswith("__tests__/")
    )


def _stem(path: str) -> str:
    """Return a simplified stem for co-location matching (no extension, no test suffixes)."""
    import os

    base = os.path.basename(path)
    # Strip extension
    name, _, _ = base.rpartition(".")
    if not name:
        name = base
    # Strip common test suffixes
    for suffix in ("_test", ".test", ".spec", "test_"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
        if name.startswith(suffix):
            name = name[len(suffix) :]
    return name.lower()


def build_chunks(
    file_scores: list[FileScore],
    full_diff: str,
    max_chunk_lines: int = 300,
) -> list[ReviewChunk]:
    """Group file_scores into ReviewChunks, keeping test files with their source.

    Strategy:
    1. Co-locate test files with their corresponding source file.
    2. Fill chunks up to max_chunk_lines (measured by diff line count).
    3. Any file that overflows a chunk gets its own chunk.

    Args:
        file_scores: Scored files to group (highest score first is recommended).
        full_diff: The complete diff text, used to extract per-file diffs.
        max_chunk_lines: Soft maximum changed-line count per chunk.

    Returns:
        A list of ReviewChunk objects ready for review.
    """
    per_file_diffs = parse_diff_files(full_diff)

    # Map stem → source file path for co-location
    source_stems: dict[str, str] = {}
    for fs in file_scores:
        if not _is_test_file(fs.path):
            source_stems[_stem(fs.path)] = fs.path

    assigned: set[str] = set()
    raw_chunks: list[list[str]] = []  # list of path-lists

    for fs in file_scores:
        if fs.path in assigned:
            continue
        if _is_test_file(fs.path):
            # Try to find the corresponding source file
            stem = _stem(fs.path)
            source = source_stems.get(stem)
            if source and source not in assigned:
                # Pair test + source together
                assigned.add(fs.path)
                assigned.add(source)
                raw_chunks.append([source, fs.path])
                continue
        # Solo file
        assigned.add(fs.path)
        raw_chunks.append([fs.path])

    # Defensive fallback: files in file_scores that had no diff text in full_diff
    # (can happen if full_diff was truncated or file_scores came from a different diff).
    # These are rare in practice; dump them together as a final chunk.
    remaining = [fs.path for fs in file_scores if fs.path not in assigned]
    if remaining:
        raw_chunks.append(remaining)

    # Now pack raw_chunks into size-bounded ReviewChunks
    chunks: list[ReviewChunk] = []
    current_paths: list[str] = []
    current_lines = 0

    def _flush(paths: list[str]) -> None:
        if not paths:
            return
        diff_parts = [per_file_diffs.get(p, "") for p in paths]
        diff_text = "".join(diff_parts)
        idx = len(chunks) + 1
        chunks.append(
            ReviewChunk(
                paths=list(paths),
                diff_text=diff_text,
                chunk_index=idx,
                total_chunks=0,  # filled in after all chunks built
                label=f"chunk-{idx}",
            )
        )

    for group in raw_chunks:
        group_diff = "".join(per_file_diffs.get(p, "") for p in group)
        group_lines = count_diff_lines(group_diff)

        if current_paths and current_lines + group_lines > max_chunk_lines:
            _flush(current_paths)
            current_paths = []
            current_lines = 0

        current_paths.extend(group)
        current_lines += group_lines

    _flush(current_paths)

    # Back-fill total_chunks now that we know the final count
    total = len(chunks)
    for chunk in chunks:
        chunk.total_chunks = total

    return chunks


# ── Risk scoring helpers (for Tier 2 and Tier 3) ──────────────────────────

# Patterns that increase a file's risk score
_HIGH_RISK_PATTERNS = re.compile(
    r"(auth|security|payment|crypt|secret|password|token|permission|sql|database|migration)",
    re.IGNORECASE,
)
_MEDIUM_RISK_PATTERNS = re.compile(
    r"(api|route|endpoint|handler|model|schema|config|settings|deploy|infra)",
    re.IGNORECASE,
)


def _score_file(path: str, line_count: int) -> float:
    """Compute a numeric risk score for a single file.

    Factors:
    - Base score from changed-line count (bigger changes = more risk)
    - Multiplier for high-risk path keywords (auth, crypto, payments, …)
    - Multiplier for medium-risk path keywords (API, models, config, …)
    """
    base = min(line_count / 10.0, 10.0)  # cap raw line contribution at 10
    if _HIGH_RISK_PATTERNS.search(path):
        return base * 3.0
    if _MEDIUM_RISK_PATTERNS.search(path):
        return base * 1.5
    return base


def score_files(diff: str) -> list[FileRiskScore]:
    """Score each file in *diff* by risk, returning a sorted list (highest first).

    Uses lightweight heuristics (path keywords + change size) — no LLM cost.

    Args:
        diff: Full unified diff text.

    Returns:
        List of FileRiskScore objects, sorted descending by score.
    """
    per_file = parse_diff_files(diff)
    scores: list[FileRiskScore] = []
    for path, file_diff in per_file.items():
        line_count = count_diff_lines(file_diff)
        score = _score_file(path, line_count)
        scores.append(FileRiskScore(path=path, score=score, line_count=line_count))
    scores.sort(key=lambda fs: fs.score, reverse=True)
    return scores


_RISK_LABEL_THRESHOLDS = (
    (15.0, "HIGH"),
    (5.0, "MEDIUM"),
    (0.0, "LOW"),
)


def _risk_label(score: float) -> str:
    """Convert a numeric risk score to a HIGH/MEDIUM/LOW label."""
    for threshold, label in _RISK_LABEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "LOW"


def build_risk_map_header(file_scores: list[FileRiskScore]) -> str:
    """Build a concise risk-map block to prepend to the reviewer prompt.

    For Tier 2 reviews, this tells the reviewer which files to prioritise
    without adding extra LLM calls.

    Args:
        file_scores: Scored files (from score_files()), sorted by score.

    Returns:
        A markdown-style header string, or "" if no files.
    """
    if not file_scores:
        return ""
    lines = ["## Review Priority Map", ""]
    for fs in file_scores:
        label = _risk_label(fs.score)
        lines.append(f"- [{label}] {fs.path} ({fs.line_count} changed lines)")
    lines.append("")
    lines.append(
        "Focus review effort on HIGH-risk files first, then MEDIUM. "
        "LOW-risk files still require review but are lower priority."
    )
    return "\n".join(lines)


def build_diff_chunks(
    file_scores: list[FileRiskScore],
    full_diff: str,
    max_chunk_lines: int = 600,
) -> list[DiffChunk]:
    """Group FileRiskScore entries into DiffChunks for Tier 3 map-reduce review.

    Co-locates test files with their source, respects max_chunk_lines, and
    assigns risk labels to each chunk based on its highest-scored file.

    Args:
        file_scores: Risk-scored files from score_files() (highest first).
        full_diff: Complete diff text used to extract per-file diffs.
        max_chunk_lines: Soft maximum changed-line count per chunk.

    Returns:
        A list of DiffChunk objects ready for run_chunked_review().
    """
    per_file_diffs = parse_diff_files(full_diff)

    # Map stem → source FileRiskScore for test co-location
    source_stems: dict[str, FileRiskScore] = {}
    for fs in file_scores:
        if not _is_test_file(fs.path):
            source_stems[_stem(fs.path)] = fs

    assigned: set[str] = set()
    raw_groups: list[list[FileRiskScore]] = []

    for fs in file_scores:
        if fs.path in assigned:
            continue
        if _is_test_file(fs.path):
            stem = _stem(fs.path)
            source = source_stems.get(stem)
            if source and source.path not in assigned:
                assigned.add(fs.path)
                assigned.add(source.path)
                raw_groups.append([source, fs])
                continue
        assigned.add(fs.path)
        raw_groups.append([fs])

    remaining = [fs for fs in file_scores if fs.path not in assigned]
    if remaining:
        raw_groups.append(remaining)

    # Pack raw_groups into size-bounded DiffChunks
    raw_chunks: list[list[FileRiskScore]] = []
    current_group: list[FileRiskScore] = []
    current_lines = 0

    for group in raw_groups:
        group_diff = "".join(per_file_diffs.get(fs.path, "") for fs in group)
        group_lines = count_diff_lines(group_diff)

        if current_group and current_lines + group_lines > max_chunk_lines:
            raw_chunks.append(list(current_group))
            current_group = []
            current_lines = 0

        current_group.extend(group)
        current_lines += group_lines

    if current_group:
        raw_chunks.append(current_group)

    # Build DiffChunk list
    total = len(raw_chunks)
    result: list[DiffChunk] = []
    for idx, group in enumerate(raw_chunks, start=1):
        paths = [fs.path for fs in group]
        chunk_diff = "".join(per_file_diffs.get(p, "") for p in paths)
        line_count = count_diff_lines(chunk_diff)
        # Risk label from the highest-scored file in the chunk
        max_score = max((fs.score for fs in group), default=0.0)
        risk = _risk_label(max_score)
        risk_scores = {fs.path: fs.score for fs in group}
        result.append(
            DiffChunk(
                index=idx,
                total=total,
                files=paths,
                diff_text=chunk_diff,
                line_count=line_count,
                risk_label=risk,
                risk_scores=risk_scores,
            )
        )
    return result
