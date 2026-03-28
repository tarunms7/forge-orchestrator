"""Forge TUI Design System — centralized colors, styles, and visual constants.

Every color, border style, and visual constant lives here. Widgets and screens
import from this file instead of hardcoding hex values. Change the palette here
and the entire TUI updates.

Inspired by: GitHub Dark, Vercel terminal, Linear app.
"""

from __future__ import annotations

# ── Background layers ────────────────────────────────────────────────
BG_BASE = "#0d1117"  # Deepest background (screen)
BG_SURFACE = "#161b22"  # Cards, panels, elevated surfaces
BG_RAISED = "#1c2128"  # Hover states, selected items
BG_OVERLAY = "#21262d"  # Modals, overlays, dropdowns
BG_SELECTED = "#1f2937"  # Unified selection highlight for all lists

# ── Border colors ────────────────────────────────────────────────────
BORDER_DEFAULT = "#30363d"  # Default borders
BORDER_FOCUS = "#58a6ff"  # Focused input borders
BORDER_SUBTLE = "#21262d"  # Very subtle separators

# ── Text colors ──────────────────────────────────────────────────────
TEXT_PRIMARY = "#e6edf3"  # Primary text (bright)
TEXT_SECONDARY = "#8b949e"  # Secondary text (muted)
TEXT_MUTED = "#484f58"  # Disabled, placeholder text
TEXT_LINK = "#58a6ff"  # Clickable text, links

# ── Accent colors ────────────────────────────────────────────────────
ACCENT_BLUE = "#58a6ff"  # Primary accent (focus, links, info)
ACCENT_GREEN = "#3fb950"  # Success, done, passed
ACCENT_ORANGE = "#f0883e"  # Active, in-progress, warning
ACCENT_RED = "#f85149"  # Error, failed, destructive
ACCENT_PURPLE = "#a371f7"  # Review, contracts, special
ACCENT_YELLOW = "#d29922"  # Caution, awaiting, paused
ACCENT_CYAN = "#79c0ff"  # Merge, code, technical
ACCENT_GOLD = "#d6a85f"  # Logo, branding

# ── Semantic aliases ─────────────────────────────────────────────────
SUCCESS = ACCENT_GREEN
ERROR = ACCENT_RED
WARNING = ACCENT_ORANGE
INFO = ACCENT_BLUE
ACTIVE = ACCENT_ORANGE
DONE = ACCENT_GREEN
PLANNING = ACCENT_BLUE
REVIEW = ACCENT_PURPLE

# ── Task state colors ────────────────────────────────────────────────
STATE_COLORS: dict[str, str] = {
    "todo": TEXT_SECONDARY,
    "blocked": TEXT_MUTED,
    "in_progress": ACCENT_ORANGE,
    "in_review": ACCENT_PURPLE,
    "awaiting_approval": ACCENT_YELLOW,
    "awaiting_input": ACCENT_ORANGE,
    "merging": ACCENT_CYAN,
    "done": ACCENT_GREEN,
    "cancelled": TEXT_MUTED,
    "error": ACCENT_RED,
}

# ── Task state icons ─────────────────────────────────────────────────
STATE_ICONS: dict[str, str] = {
    "todo": "○",
    "blocked": "⊘",
    "in_progress": "●",
    "in_review": "◉",
    "awaiting_approval": "⊙",
    "awaiting_input": "◆",
    "merging": "◈",
    "done": "✔",
    "cancelled": "✘",
    "error": "✖",
}

# ── Pipeline status icons ────────────────────────────────────────────
PIPELINE_STATUS_ICONS: dict[str, tuple[str, str]] = {
    "complete": ("✔", ACCENT_GREEN),
    "executing": ("●", ACCENT_ORANGE),
    "planned": ("◉", ACCENT_PURPLE),
    "planning": ("◌", ACCENT_BLUE),
    "error": ("✖", ACCENT_RED),
    "cancelled": ("✘", TEXT_MUTED),
}

# ── Phase banner config ──────────────────────────────────────────────
# Labels use plain text — the PhaseBanner.render() method handles wide-spacing.
PHASE_DISPLAY: dict[str, tuple[str, str]] = {
    "idle": ("Idle", TEXT_SECONDARY),
    "planning": ("◌ Planning", ACCENT_BLUE),
    "planned": ("◉ Plan Ready", ACCENT_PURPLE),
    "contracts": ("⚙ Preparing", ACCENT_PURPLE),
    "countdown": ("⚡ Launching", ACCENT_ORANGE),
    "executing": ("⚡ Execution", ACCENT_ORANGE),
    "in_progress": ("⚡ Execution", ACCENT_ORANGE),
    "review": ("🔍 Review", ACCENT_CYAN),
    "in_review": ("🔍 Review", ACCENT_CYAN),
    "merging": ("◈ Merging", ACCENT_CYAN),
    "final_approval": ("◎ Final Approval", ACCENT_ORANGE),
    "pr_creating": ("⚙ Creating PR", ACCENT_PURPLE),
    "pr_created": ("✔ PR Created", ACCENT_GREEN),
    "complete": ("✔ Complete", ACCENT_GREEN),
    "error": ("✖ Error", ACCENT_RED),
    "cancelled": ("✘ Cancelled", TEXT_MUTED),
    "paused": ("⏸ Paused", ACCENT_YELLOW),
    "partial_success": ("⚠ Partial Success", ACCENT_YELLOW),
    "retrying": ("⟳ Retrying", ACCENT_ORANGE),
    "interrupted": ("⏸ Interrupted", ACCENT_YELLOW),
}

# ── Global CSS for the Forge TUI ─────────────────────────────────────
# Applied at the App level. Sets the base look for all screens.
APP_CSS = f"""
Screen {{
    background: {BG_BASE};
    color: {TEXT_PRIMARY};
}}

/* ── Scrollbars — minimal and subtle ── */
Scrollbar {{
    background: {BG_SURFACE};
    color: {BORDER_DEFAULT};
}}
ScrollbarCorner {{
    background: {BG_BASE};
}}

/* ── Input styling ── */
Input {{
    background: {BG_SURFACE};
    border: tall {BORDER_DEFAULT};
    color: {TEXT_PRIMARY};
}}
Input:focus {{
    border: tall {BORDER_FOCUS};
}}

TextArea {{
    background: {BG_SURFACE};
    border: tall {BORDER_DEFAULT};
}}
TextArea:focus {{
    border: tall {BORDER_FOCUS};
}}

/* ── Select widget ── */
Select {{
    background: {BG_SURFACE};
    border: tall {BORDER_DEFAULT};
}}
Select:focus {{
    border: tall {BORDER_FOCUS};
}}
SelectOverlay {{
    background: {BG_OVERLAY};
    border: tall {BORDER_DEFAULT};
}}

/* ── DataTable ── */
DataTable {{
    background: {BG_SURFACE};
}}
DataTable > .datatable--header {{
    background: {BG_RAISED};
    color: {TEXT_SECONDARY};
}}
DataTable > .datatable--cursor {{
    background: {BG_RAISED};
}}
"""
