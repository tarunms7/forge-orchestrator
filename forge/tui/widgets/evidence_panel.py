"""Toggleable overlay showing per-task retrieval evidence details."""

from __future__ import annotations

import logging

from textual.widget import Widget

logger = logging.getLogger("forge.tui.widgets.evidence_panel")


def _rank_color(rank: int | float | None) -> str:
    """Return Rich color string based on rank position."""
    if rank is None:
        return "#8b949e"
    rank = int(rank)
    if rank == 1:
        return "#22c55e"
    if rank <= 3:
        return "#58a6ff"
    return "#8b949e"


def format_evidence_panel(
    diagnostics: dict,
    task_title: str,
    *,
    header: str = "WHY THESE FILES?",
) -> str:
    """Render evidence panel content as Rich markup (pure function)."""
    parts: list[str] = []

    # Header
    parts.append(f"[bold #58a6ff]── {header} ──[/] [#8b949e](w to close)[/]")
    parts.append(f"[bold #c9d1d9]{task_title}[/]")

    # Rationale (if present in derived diagnostics)
    rationale = (diagnostics or {}).get("rationale", "")
    if rationale:
        parts.append(f"[#8b949e]{rationale}[/]")

    parts.append("")

    if not diagnostics or not diagnostics.get("used_retrieval"):
        parts.append("[#8b949e]No retrieval data — agent used snapshot fallback[/]")
        return "\n".join(parts)

    # Confidence
    confidence = diagnostics.get("confidence")
    if confidence is not None:
        parts.append(f"[bold]Confidence:[/] [#22c55e]{confidence:.0%}[/]")

    # Matched / missed terms
    matched = diagnostics.get("matched_terms", [])
    missed = diagnostics.get("missed_terms", [])
    if matched:
        parts.append(f"[#22c55e]\u2713 {' '.join(matched)}[/]")
    if missed:
        parts.append(f"[#d29922]\u2717 {' '.join(missed)}[/]")
    if matched or missed or confidence is not None:
        parts.append("")

    # Evidence files (detailed)
    evidence_files = diagnostics.get("evidence_files", [])
    if evidence_files:
        for ef in evidence_files:
            rank = ef.get("rank")
            path = ef.get("path", "")
            focus = ef.get("focus_range")
            rank_color = _rank_color(rank)

            # Main line: rank + path + focus range
            rank_str = f"#{int(rank)}" if rank is not None else "?"
            line = f"  [{rank_color}]{rank_str}[/] [bold]{path}[/]"
            if focus and len(focus) == 2:
                line += f" [#8b949e]L{focus[0]}-L{focus[1]}[/]"
            parts.append(line)

            # Reasons
            reasons = ef.get("reasons", [])
            if reasons:
                parts.append(f"    [#8b949e]{', '.join(reasons)}[/]")

            # Symbols
            symbols = ef.get("symbols", [])
            if symbols:
                sym_strs = []
                for s in symbols[:4]:
                    name = s.get("name", "")
                    sym_line = s.get("line")
                    if sym_line is not None:
                        sym_strs.append(f"{name} L{sym_line}")
                    else:
                        sym_strs.append(name)
                parts.append(f"    [#a371f7]symbols:[/] {', '.join(sym_strs)}")

            # Neighbors
            neighbors = ef.get("neighbors", [])
            if neighbors:
                nb_strs = []
                for n in neighbors[:2]:
                    kind = n.get("kind", "")
                    nb_path = n.get("path", "")
                    nb_strs.append(f"{kind} {nb_path}")
                parts.append(f"    [#79c0ff]nearby:[/] {', '.join(nb_strs)}")

    elif diagnostics.get("top_files"):
        # Fallback: simple file list
        for path in diagnostics["top_files"]:
            parts.append(f"  [bold]{path}[/]")

    return "\n".join(parts)


class EvidencePanel(Widget):
    """Toggleable panel showing retrieval evidence for the selected task."""

    DEFAULT_CSS = """
    EvidencePanel {
        width: 100%;
        height: auto;
        max-height: 20;
        padding: 1;
        background: #0d1117;
        border: solid #30363d;
        display: none;
    }
    EvidencePanel.visible {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._diagnostics: dict = {}
        self._task_title: str = ""

    def update_evidence(self, diagnostics: dict, task_title: str) -> None:
        """Update stored diagnostics and refresh rendering."""
        self._diagnostics = diagnostics
        self._task_title = task_title
        self.refresh()

    def toggle(self) -> None:
        """Toggle panel visibility."""
        self.toggle_class("visible")

    @property
    def is_open(self) -> bool:
        """Whether the panel is currently visible."""
        return self.has_class("visible")

    def render(self) -> str:
        return format_evidence_panel(self._diagnostics, self._task_title)
