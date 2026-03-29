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


class ReviewStrategy(Enum):
    """How to split a diff across review calls."""

    TIER1 = "tier1"  # Single pass — diff fits comfortably in one prompt
    TIER2 = "tier2"  # Per-file — send each file as its own review call
    TIER3 = "tier3"  # Chunked — group related files into review chunks


# Default thresholds (changed lines, not total lines)
DEFAULT_MEDIUM_THRESHOLD = 400  # above this → TIER2
DEFAULT_LARGE_THRESHOLD = 2000  # above this → TIER3


def count_diff_lines(diff: str) -> int:
    """Count the number of changed lines (+/-) in a diff, excluding headers."""
    if not diff:
        return 0
    count = 0
    for line in diff.splitlines():
        if (
            (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
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
) -> ReviewStrategy:
    """Select a review strategy based on diff size.

    Args:
        diff: The full unified diff text.
        medium_threshold: Changed-line count above which TIER2 is used.
        large_threshold: Changed-line count above which TIER3 is used.

    Returns:
        The appropriate ReviewStrategy.
    """
    n = count_diff_lines(diff)
    if n >= large_threshold:
        return ReviewStrategy.TIER3
    if n >= medium_threshold:
        return ReviewStrategy.TIER2
    return ReviewStrategy.TIER1


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
            name = name[len(suffix):]
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
