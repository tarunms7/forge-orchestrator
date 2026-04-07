"""Shared helpers for turning token-ish provider text deltas into readable log lines."""

from __future__ import annotations

import re


def drain_stream_text(buffer: str, *, force: bool = False) -> tuple[list[str], str]:
    """Split buffered provider text into readable commentary fragments.

    Providers often emit tiny text chunks. This helper buffers them until we have
    either a newline-terminated line, a complete sentence, or a forced flush.
    """
    fragments: list[str] = []
    remaining = buffer

    while "\n" in remaining:
        line, remaining = remaining.split("\n", 1)
        line = line.strip()
        if line:
            fragments.append(line)

    sentence_pattern = re.compile(r"^(.*?[.!?])(?=\s+|$)", re.DOTALL)
    while True:
        match = sentence_pattern.match(remaining.lstrip())
        if match is None:
            break
        fragment = match.group(1).strip()
        if fragment:
            fragments.append(fragment)
        remaining = remaining.lstrip()[match.end() :]

    if force:
        tail = remaining.strip()
        if tail:
            fragments.append(tail)
        remaining = ""

    return fragments, remaining
