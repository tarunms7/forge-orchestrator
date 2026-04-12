"""Helpers for deriving effective task file scope."""

from __future__ import annotations

import posixpath
import re

_FILE_TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+")


def _normalize_file_token(token: str) -> str | None:
    candidate = token.strip().strip("`'\"()[]{}<>.,:;")
    if not candidate:
        return None
    if "://" in candidate:
        return None

    candidate = candidate.replace("\\", "/")
    normalized = posixpath.normpath(candidate)
    if normalized in {"", "."}:
        return None
    if normalized.startswith("/"):
        return None
    if normalized.startswith("../"):
        return None

    basename = posixpath.basename(normalized)
    if "." not in basename:
        return None
    if not re.search(r"[A-Za-z]", basename):
        return None
    return normalized


def extract_explicit_file_paths(text: str | None) -> list[str]:
    """Extract explicit file paths mentioned in free-form task text."""
    if not text or not isinstance(text, str):
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for raw in _FILE_TOKEN_RE.findall(text):
        normalized = _normalize_file_token(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def effective_task_files(files: list[str] | None, description: str | None = None) -> list[str]:
    """Return task scope files plus explicit file deliverables named in the description."""
    seen: set[str] = set()
    ordered: list[str] = []

    for path in files or []:
        if path not in seen:
            seen.add(path)
            ordered.append(path)

    for path in extract_explicit_file_paths(description):
        if path not in seen:
            seen.add(path)
            ordered.append(path)

    return ordered
