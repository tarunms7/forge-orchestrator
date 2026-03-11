"""Review gate result cards for task review status display."""

from __future__ import annotations
from textual.widget import Widget

_GATE_NAMES = {
    "gate0_build": ("Build", "🔨"),
    "gate1_lint": ("Lint", "📏"),
    "gate1_5_test": ("Tests", "🧪"),
    "gate2_llm_review": ("LLM Review", "🤖"),
}

_STATUS_ICONS = {"passed": "[#3fb950]✓[/]", "failed": "[#f85149]✗[/]", "running": "[#d2a8ff]◎[/]"}


def format_gates(gates: dict[str, dict]) -> str:
    if not gates:
        return "[#484f58]No review data yet[/]"
    lines = []
    for gate_key, (name, icon) in _GATE_NAMES.items():
        gate = gates.get(gate_key)
        if not gate:
            lines.append(f"  [#484f58]○ {icon} {name}[/]")
            continue
        status = gate.get("status", "unknown")
        status_icon = _STATUS_ICONS.get(status, "[#8b949e]?[/]")
        details = gate.get("details", "")
        detail_str = f" [#8b949e]— {details}[/]" if details else ""
        lines.append(f"  {status_icon} {icon} {name}{detail_str}")
    return "\n".join(lines)


_TYPING_FRAMES = ["▍", "▌", "▍", " "]


def format_streaming_output(lines: list[str], streaming: bool = False, typing_frame: int = 0) -> str:
    """Format streaming LLM review output lines with optional typing indicator."""
    if not lines:
        return ""
    parts = list(lines)
    if streaming:
        cursor = _TYPING_FRAMES[typing_frame % len(_TYPING_FRAMES)]
        parts.append(f"[#58a6ff]● Typing{cursor}[/]")
    return "\n".join(parts)


class ReviewGates(Widget):
    DEFAULT_CSS = "ReviewGates { height: auto; padding: 1; }"

    def __init__(self) -> None:
        super().__init__()
        self._gates: dict[str, dict] = {}
        self._streaming_lines: list[str] = []
        self._streaming: bool = False
        self._typing_frame: int = 0
        self._typing_timer = None

    def update_gates(self, gates: dict[str, dict]) -> None:
        self._gates = gates
        self.refresh()

    def update_streaming_output(self, lines: list[str]) -> None:
        """Display streaming LLM review text below the gate status cards."""
        self._streaming_lines = lines
        self.refresh()

    def set_streaming(self, active: bool) -> None:
        """Show/hide a typing indicator below the streaming review output."""
        if active == self._streaming:
            return
        self._streaming = active
        if active:
            self._typing_frame = 0
            try:
                self._typing_timer = self.set_interval(0.3, self._tick_typing)
            except Exception:
                pass  # Not yet composed
        else:
            if self._typing_timer is not None:
                self._typing_timer.stop()
                self._typing_timer = None
            self._typing_frame = 0
        self.refresh()

    def _tick_typing(self) -> None:
        """Animate the typing indicator cursor."""
        if not self._streaming:
            return
        self._typing_frame += 1
        self.refresh()

    def render(self) -> str:
        parts = [format_gates(self._gates)]
        streaming_text = format_streaming_output(
            self._streaming_lines,
            streaming=self._streaming,
            typing_frame=self._typing_frame,
        )
        if streaming_text:
            parts.append("")  # blank line separator
            parts.append("[bold #58a6ff]LLM Review Output[/]")
            parts.append(streaming_text)
        return "\n".join(parts)
