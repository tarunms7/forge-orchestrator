"""Adaptive review strategy: diff analysis, risk scoring, and chunking.

No LLM calls — all pure Python. Provides:
- ReviewStrategy enum: TIER1/TIER2/TIER3
- FileRiskScore, DiffChunk dataclasses
- score_files(): risk-score every changed file in a diff
- select_strategy(): pick a tier based on diff size
- build_chunks(): group files into DiffChunks for Tier 3
- build_risk_map_header(): Tier 2 prompt prefix
- extract_interface_context(): function sigs for chunk reviewer context
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

# ── Enums & dataclasses ────────────────────────────────────────────────────


class ReviewStrategy(str, Enum):
    TIER1 = "tier1"  # < medium_threshold: single pass, full diff (unchanged behavior)
    TIER2 = "tier2"  # medium–large threshold: risk-enhanced single pass
    TIER3 = "tier3"  # > large threshold: multi-chunk map-reduce with synthesis


@dataclass
class FileRiskScore:
    """Risk assessment for a single changed file."""

    path: str
    score: float
    tier: str  # "HIGH", "MEDIUM", "LOW"
    is_new: bool
    is_test: bool
    is_security: bool
    lines_changed: int
    language: str


@dataclass
class DiffChunk:
    """A subset of the full diff assigned for independent review."""

    index: int  # 1-based
    total: int  # total chunk count
    files: list[str]
    diff_text: str  # combined diff text for these files only
    line_count: int  # total +/- lines in this chunk
    risk_label: str  # "HIGH", "MEDIUM", "LOW"
    risk_scores: dict[str, float]  # file → score


# ── Constants ──────────────────────────────────────────────────────────────

_SECURITY_SEGMENTS = frozenset(
    {
        "auth",
        "crypto",
        "token",
        "password",
        "secret",
        "key",
        "perm",
        "acl",
        "role",
        "jwt",
        "session",
        "login",
        "oauth",
        "cred",
    }
)

_LANGUAGE_WEIGHT: dict[str, float] = {
    ".py": 10,
    ".go": 10,
    ".rs": 10,
    ".ts": 8,
    ".tsx": 8,
    ".js": 8,
    ".jsx": 8,
    ".java": 8,
    ".kt": 8,
    ".swift": 8,
    ".rb": 6,
    ".cpp": 10,
    ".c": 10,
    ".h": 8,
    ".yaml": 2,
    ".yml": 2,
    ".json": 2,
    ".toml": 2,
    ".md": 0,
}


# ── Diff parsing ───────────────────────────────────────────────────────────


def parse_diff_files(diff: str) -> dict[str, str]:
    """Split a git diff string into per-file sections.

    Returns {file_path: diff_text_for_that_file}.
    File path is the b/ (post-change) path.
    """
    if not diff.strip():
        return {}

    sections: dict[str, str] = {}
    current_file: str | None = None
    current_lines: list[str] = []

    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_file is not None:
                sections[current_file] = "".join(current_lines)
            m = re.match(r"diff --git a/.+ b/(.+)", line.rstrip("\n"))
            current_file = m.group(1) if m else None
            current_lines = [line]
        elif current_file is not None:
            current_lines.append(line)

    if current_file is not None:
        sections[current_file] = "".join(current_lines)

    return sections


def count_diff_lines(diff: str) -> int:
    """Count total added + removed lines (excludes context lines and headers)."""
    count = 0
    for line in diff.splitlines():
        if (
            line.startswith("+")
            and not line.startswith("+++")
            or line.startswith("-")
            and not line.startswith("---")
        ):
            count += 1
    return count


# ── File attribute helpers ─────────────────────────────────────────────────


def _count_file_lines(section: str) -> int:
    return count_diff_lines(section)


def _is_new_file(section: str) -> bool:
    return "new file mode" in section or "--- /dev/null" in section


def _is_test_file(path: str) -> bool:
    p = path.lower()
    stem = PurePosixPath(path).stem.lower()
    return (
        "/test" in p
        or p.startswith("test")
        or stem.endswith("_test")
        or stem.startswith("test_")
        or "/tests/" in p
        or "/spec/" in p
        or p.endswith(".test.ts")
        or p.endswith(".spec.ts")
        or p.endswith(".test.js")
        or p.endswith(".spec.js")
    )


def _is_security_path(path: str) -> bool:
    parts = set(PurePosixPath(path).parts)
    parts_lower = {p.lower() for p in parts}
    stem = PurePosixPath(path).stem.lower()
    return bool(parts_lower & _SECURITY_SEGMENTS) or any(kw in stem for kw in _SECURITY_SEGMENTS)


def _avg_hunk_size(section: str) -> float:
    """Average lines per hunk as a complexity proxy. Clamped to [0, 20]."""
    hunks = [ln for ln in section.splitlines() if ln.startswith("@@")]
    if not hunks:
        return 0.0
    total = _count_file_lines(section)
    return min(total / len(hunks), 20.0)


# ── Risk scoring ──────────────────────────────────────────────────────────


def score_files(diff: str) -> list[FileRiskScore]:
    """Compute FileRiskScore for every changed file.

    Returns list sorted descending by score.
    Tier labels: HIGH=top 30%, MEDIUM=next 40%, LOW=bottom 30%.
    """
    sections = parse_diff_files(diff)
    if not sections:
        return []

    raw: list[tuple[str, float, dict]] = []
    for path, section in sections.items():
        lines = _count_file_lines(section)
        is_new = _is_new_file(section)
        is_test = _is_test_file(path)
        is_sec = _is_security_path(path)
        lang = PurePosixPath(path).suffix.lower()
        lang_w = _LANGUAGE_WEIGHT.get(lang, 5)
        avg_hunk = _avg_hunk_size(section)

        score = (
            min(lines, 500) * 0.4
            + (30.0 if is_new and not is_test else 0.0)
            + (25.0 if is_sec else 0.0)
            + avg_hunk * 0.5
            + (10.0 if not is_test else -10.0)
            + lang_w
        )
        raw.append(
            (
                path,
                score,
                {
                    "is_new": is_new,
                    "is_test": is_test,
                    "is_sec": is_sec,
                    "lang": lang,
                    "lines": lines,
                },
            )
        )

    raw.sort(key=lambda x: x[1], reverse=True)

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
        results.append(
            FileRiskScore(
                path=path,
                score=score,
                tier=tier,
                is_new=meta["is_new"],
                is_test=meta["is_test"],
                is_security=meta["is_sec"],
                lines_changed=meta["lines"],
                language=meta["lang"],
            )
        )

    return results


# ── Strategy selection ────────────────────────────────────────────────────


def select_strategy(
    diff: str,
    medium_threshold: int = 400,
    large_threshold: int = 2000,
    *,
    adaptive: bool = True,
) -> ReviewStrategy:
    """Select review tier based on diff size."""
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
    """Return the source file for a test file if it exists in the diff."""
    stem = PurePosixPath(test_path).stem
    # Strip common test prefixes/suffixes
    for prefix in ("test_",):
        if stem.startswith(prefix):
            source_stem = stem[len(prefix) :]
            for p in all_paths:
                if PurePosixPath(p).stem == source_stem and not _is_test_file(p):
                    return p
    for suffix in ("_test",):
        if stem.endswith(suffix):
            source_stem = stem[: -len(suffix)]
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

    Files are sorted by risk score (descending) so high-risk files
    appear in early chunks and are reviewed first (primacy effect).
    Test files are co-located with their corresponding source file.
    """
    if not file_scores:
        return []

    sections = parse_diff_files(full_diff)
    all_paths = set(sections.keys())
    score_map = {fs.path: fs.score for fs in file_scores}
    tier_map = {fs.path: fs.tier for fs in file_scores}

    # Map test → source for co-location
    test_to_source: dict[str, str] = {}
    for fs in file_scores:
        if fs.is_test:
            src = _source_for_test(fs.path, all_paths)
            if src:
                test_to_source[fs.path] = src

    assigned: set[str] = set()
    raw_chunks: list[list[str]] = []
    current: list[str] = []
    current_lines = 0
    overflow_limit = max_chunk_lines * 1.2

    def _flush() -> None:
        if current:
            raw_chunks.append(list(current))
            current.clear()

    for fs in file_scores:
        path = fs.path
        if path in assigned:
            continue

        file_lines = _count_file_lines(sections.get(path, ""))

        if current_lines + file_lines > max_chunk_lines and current:
            _flush()
            current_lines = 0

        current.append(path)
        current_lines += file_lines
        assigned.add(path)

        # Co-locate the matching test file
        if not fs.is_test:
            for test_p, src_p in test_to_source.items():
                if src_p == path and test_p not in assigned:
                    test_lines = _count_file_lines(sections.get(test_p, ""))
                    if current_lines + test_lines <= overflow_limit:
                        current.append(test_p)
                        current_lines += test_lines
                        assigned.add(test_p)

    _flush()

    # Any remaining unassigned files (e.g. test files whose source was already assigned)
    remaining = [fs.path for fs in file_scores if fs.path not in assigned]
    if remaining:
        raw_chunks.append(remaining)

    total = len(raw_chunks)
    chunks: list[DiffChunk] = []
    for idx, files in enumerate(raw_chunks):
        chunk_diff = "\n".join(sections[p] for p in files if p in sections)
        line_count = count_diff_lines(chunk_diff)
        chunk_scores = {p: score_map.get(p, 0.0) for p in files}
        best = max(chunk_scores, key=lambda p: chunk_scores[p])
        risk_label = tier_map.get(best, "LOW")
        chunks.append(
            DiffChunk(
                index=idx + 1,
                total=total,
                files=files,
                diff_text=chunk_diff,
                line_count=line_count,
                risk_label=risk_label,
                risk_scores=chunk_scores,
            )
        )

    return chunks


# ── Risk map header (Tier 2 prompt) ──────────────────────────────────────


def build_risk_map_header(file_scores: list[FileRiskScore]) -> str:
    """Format the '## Review Priority Map' section for Tier 2 prompts."""
    if not file_scores:
        return ""

    total_lines = sum(fs.lines_changed for fs in file_scores)
    n = len(file_scores)

    lines = [
        "## Review Priority Map",
        "Files ordered by estimated risk. High-risk files deserve deepest attention.",
        "",
    ]

    for tier_label, directive in (
        ("HIGH", "(review thoroughly)"),
        ("MEDIUM", "(review carefully)"),
        ("LOW", "(spot check)"),
    ):
        tier_files = [fs for fs in file_scores if fs.tier == tier_label]
        if not tier_files:
            continue
        lines.append(f"{tier_label} {directive}:")
        for fs in tier_files:
            tags: list[str] = []
            if fs.is_new:
                tags.append("new file")
            if fs.is_security:
                tags.append("security-adjacent")
            tag_str = f", {', '.join(tags)}" if tags else ""
            lang = fs.language.lstrip(".").upper() if fs.language else "?"
            lines.append(f"  \u25cf {fs.path:<50s}({fs.lines_changed} lines{tag_str}, {lang})")
        lines.append("")

    lines.append(f"Total: {total_lines} lines across {n} file{'s' if n != 1 else ''}.")
    return "\n".join(lines)


# ── Interface context (Tier 3 chunk reviewer) ────────────────────────────


def extract_interface_context(
    chunk: DiffChunk,
    all_file_scores: list[FileRiskScore],
    full_diff: str,
    max_lines: int = 200,
) -> str:
    """Extract function/class signatures from files outside this chunk
    that are imported by files inside this chunk.

    Gives chunk reviewers minimal cross-chunk type info without flooding them.
    """
    sections = parse_diff_files(full_diff)
    chunk_files = set(chunk.files)

    # Collect top-level module names imported by chunk files
    imported_mods: set[str] = set()
    for path in chunk.files:
        section = sections.get(path, "")
        for line in section.splitlines():
            if not line.startswith("+"):
                continue
            stripped = line[1:].strip()
            m = re.match(r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", stripped)
            if m:
                mod = (m.group(1) or m.group(2) or "").lstrip(".")
                if mod:
                    imported_mods.add(mod.split(".")[0])

    if not imported_mods:
        return ""

    # Find external files (not in chunk) whose stem matches an imported module
    external: list[str] = [
        p for p in sections if p not in chunk_files and PurePosixPath(p).stem in imported_mods
    ]

    if not external:
        return ""

    sig_lines: list[str] = []
    for path in external:
        file_sigs: list[str] = []
        for line in sections[path].splitlines():
            stripped = line.lstrip("+ ")
            if re.match(r"^(?:async\s+)?def |^class ", stripped):
                file_sigs.append(f"  {stripped.rstrip()}")
        if file_sigs:
            sig_lines.append(f"# {path}")
            sig_lines.extend(file_sigs[:50])

    if not sig_lines:
        return ""

    sig_lines = sig_lines[:max_lines]
    return "## Interface Context (signatures from files outside this chunk)\n" + "\n".join(
        sig_lines
    )
