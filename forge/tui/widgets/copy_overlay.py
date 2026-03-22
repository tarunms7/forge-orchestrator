"""Copy overlay widget — line selection for clipboard copy."""

from __future__ import annotations

import logging
import platform
import subprocess

from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget

logger = logging.getLogger("forge.tui.widgets.copy_overlay")


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    # Strategy 1: platform subprocess
    system = platform.system()
    try:
        if system == "Darwin":
            proc = subprocess.Popen(
                ["pbcopy"],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.communicate(input=text.encode("utf-8"), timeout=5)
            if proc.returncode == 0:
                return True
        elif system == "Linux":
            proc = subprocess.Popen(
                ["xclip", "-selection", "clipboard"],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.communicate(input=text.encode("utf-8"), timeout=5)
            if proc.returncode == 0:
                return True
        elif system == "Windows":
            proc = subprocess.Popen(
                ["clip"],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.communicate(input=text.encode("utf-8"), timeout=5)
            if proc.returncode == 0:
                return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        logger.debug("Clipboard subprocess failed", exc_info=True)

    return False


class CopyOverlay(Widget):
    """Line-selection overlay for copying agent output to clipboard.

    Mounted on top of AgentOutput. Shows lines with ○/● markers for selection.
    j/k to navigate, space to toggle, Enter to copy, Esc to cancel.
    """

    DEFAULT_CSS = """
    CopyOverlay {
        width: 1fr;
        height: 1fr;
        background: #0d1117;
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False, priority=True),
        Binding("k", "cursor_up", "Up", show=False, priority=True),
        Binding("space", "toggle_line", "Toggle", show=False, priority=True),
        Binding("enter", "copy_selected", "Copy", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    class CopyComplete(Message):
        """Posted when copy completes or is cancelled."""

        def __init__(self, text: str, success: bool) -> None:
            self.text = text
            self.success = success
            super().__init__()

    class Cancelled(Message):
        """Posted when user presses Esc."""

        pass

    def __init__(self, lines: list[str] | None = None) -> None:
        super().__init__()
        self._lines: list[str] = list(lines or [])
        self._cursor: int = 0
        self._selected: set[int] = set()

    @property
    def selected_count(self) -> int:
        return len(self._selected)

    def render(self) -> str:
        if not self._lines:
            return "[#8b949e]No lines to copy[/]"

        parts: list[str] = []
        # Status bar
        parts.append(
            "[bold #f0883e]── COPY MODE ── [/]"
            "[#8b949e]j/k: move │ space: toggle │ Enter: copy │ Esc: cancel[/]"
        )
        parts.append(
            f"[#8b949e]{self.selected_count} line{'s' if self.selected_count != 1 else ''} selected[/]"
        )
        parts.append("")

        # Visible window around cursor
        max_visible = 30
        start = max(0, self._cursor - max_visible // 2)
        end = min(len(self._lines), start + max_visible)
        if end - start < max_visible:
            start = max(0, end - max_visible)

        for i in range(start, end):
            marker = "●" if i in self._selected else "○"
            is_cursor = i == self._cursor
            line_text = self._lines[i][:120]  # Truncate long lines
            if is_cursor:
                parts.append(f"[bold on #1f2937] {marker} {line_text} [/]")
            elif i in self._selected:
                parts.append(f" [#3fb950]{marker}[/] {line_text}")
            else:
                parts.append(f" [#484f58]{marker}[/] [#8b949e]{line_text}[/]")

        return "\n".join(parts)

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._lines) - 1:
            self._cursor += 1
            self.refresh()

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self.refresh()

    def action_toggle_line(self) -> None:
        if not self._lines:
            return
        if self._cursor in self._selected:
            self._selected.discard(self._cursor)
        else:
            self._selected.add(self._cursor)
        self.refresh()

    def action_copy_selected(self) -> None:
        if not self._selected:
            # If nothing selected, copy current line
            if self._lines and 0 <= self._cursor < len(self._lines):
                text = self._lines[self._cursor]
            else:
                self.post_message(self.CopyComplete("", False))
                return
        else:
            selected_lines = [
                self._lines[i] for i in sorted(self._selected) if 0 <= i < len(self._lines)
            ]
            text = "\n".join(selected_lines)

        success = copy_to_clipboard(text)
        # Try Textual fallback if subprocess failed
        if not success:
            try:
                self.app.copy_to_clipboard(text)
                success = True
            except Exception:
                logger.debug("Textual clipboard fallback failed", exc_info=True)

        self.post_message(self.CopyComplete(text, success))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
