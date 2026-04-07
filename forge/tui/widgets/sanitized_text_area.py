"""Shared TextArea helpers for stripping terminal control noise from user input."""

from __future__ import annotations

import re

from textual.widgets import TextArea

_MOUSE_REPORT_RE = re.compile(r"(?:\x1b)?\[*<\d+;\d+;\d+[Mm]?")
_RAW_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]?|O[@-~]?|[@-_])")
_CARET_ESCAPE_RE = re.compile(
    r"(?:\^\[)+(?:\[[0-?]*[ -/]*[@-~]?|O[@-~]?|<\d+;\d+;\d+[Mm]?|[0-9;?]*[A-Za-z~]?)"
)
_PARTIAL_BRACKETED_PASTE_RE = re.compile(r"\[+20[01]~")
_BRACKET_STUTTER_RE = re.compile(r"\[{3,}(?:M\[?)?")
_PARTIAL_CSI_KEY_RE = re.compile(r"\[+(?:[0-9;?]*[ABCDHFMP~])")


def strip_terminal_input_noise(text: str) -> str:
    """Remove terminal control sequences accidentally inserted as editable text."""
    text = _MOUSE_REPORT_RE.sub("", text)
    text = _RAW_ESCAPE_RE.sub("", text)
    text = _CARET_ESCAPE_RE.sub("", text)
    text = _PARTIAL_BRACKETED_PASTE_RE.sub("", text)
    text = _BRACKET_STUTTER_RE.sub("", text)
    text = _PARTIAL_CSI_KEY_RE.sub("", text)
    return text.replace("\x1b", "")


def _location_to_offset(text: str, location: tuple[int, int]) -> int:
    """Convert a TextArea (row, column) location into a flat string offset."""
    row, column = location
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0

    row = max(0, min(row, len(lines) - 1))
    offset = sum(len(line) for line in lines[:row])
    current = lines[row]
    content = current[:-1] if current.endswith("\n") else current
    return offset + max(0, min(column, len(content)))


def _offset_to_location(text: str, offset: int) -> tuple[int, int]:
    """Convert a flat string offset back into a TextArea (row, column) location."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return (0, 0)

    remaining = max(0, min(offset, len(text)))
    for row, line in enumerate(lines):
        content = line[:-1] if line.endswith("\n") else line
        content_len = len(content)
        if remaining <= content_len:
            return (row, remaining)
        remaining -= len(line)

    last = lines[-1]
    last_content = last[:-1] if last.endswith("\n") else last
    return (len(lines) - 1, len(last_content))


class SanitizedTextArea(TextArea):
    """TextArea that strips accidental terminal mouse packets from edited text."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._sanitizing_input = False

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is not self or self._sanitizing_input:
            return

        raw_text = self.text
        clean_text = strip_terminal_input_noise(raw_text)
        if clean_text == raw_text:
            return

        cursor_offset = _location_to_offset(raw_text, self.cursor_location)
        clean_cursor_offset = len(strip_terminal_input_noise(raw_text[:cursor_offset]))

        self._sanitizing_input = True
        try:
            self.text = clean_text
            self.move_cursor(_offset_to_location(clean_text, clean_cursor_offset))
        finally:
            self._sanitizing_input = False
