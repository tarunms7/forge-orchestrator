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


class ReviewGates(Widget):
    DEFAULT_CSS = "ReviewGates { height: auto; padding: 1; }"

    def __init__(self) -> None:
        super().__init__()
        self._gates: dict[str, dict] = {}

    def update_gates(self, gates: dict[str, dict]) -> None:
        self._gates = gates
        self.refresh()

    def render(self) -> str:
        return format_gates(self._gates)
