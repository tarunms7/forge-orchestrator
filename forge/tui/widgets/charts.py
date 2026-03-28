"""Sparkline and donut-chart renderers that output Rich-markup strings.

Pure functions — no Textual widgets. Callers embed the returned strings
in Rich ``Text`` / ``Static`` / ``Label`` widgets as needed.
"""

from __future__ import annotations

from collections.abc import Callable

from forge.tui.theme import ACCENT_BLUE, TEXT_SECONDARY

# Unicode block elements, 8 levels (index 0 = lowest).
_BLOCKS = "▁▂▃▄▅▆▇█"


def render_sparkline(
    values: list[float],
    width: int = 40,
    color: str = ACCENT_BLUE,
    fmt: Callable[[float], str] | None = None,
) -> str:
    """Return a Rich-markup sparkline bar with a stats line underneath.

    Parameters
    ----------
    values:
        Data points to plot.
    width:
        Character width of the sparkline bar (values are down/up-sampled).
    color:
        Rich color tag for the bar characters.
    fmt:
        Formatter for min/avg/max values. Defaults to ``${x:.2f}``.

    Returns
    -------
    str
        Multi-line string: sparkline bar + stats line.
    """
    if not values:
        return f"[{TEXT_SECONDARY}]No data[/]"

    # Resample to *width* buckets via nearest-neighbour.
    n = len(values)
    if n == 1:
        sampled = [values[0]] * width
    elif n == width:
        sampled = list(values)
    else:
        sampled = [values[int(i * n / width)] for i in range(width)]

    lo, hi = min(sampled), max(sampled)
    span = hi - lo

    if span == 0:
        # All values equal → mid-height bar.
        bar = _BLOCKS[3] * width
    else:
        bar = "".join(_BLOCKS[int((v - lo) / span * 7)] for v in sampled)

    sparkline_bar = f"[{color}]{bar}[/]"

    fmt_fn = fmt or _default_cost_fmt
    stats = format_stats_line(
        label="",
        min_val=min(values),
        avg_val=sum(values) / len(values),
        max_val=max(values),
        fmt_fn=fmt_fn,
    )

    return f"{sparkline_bar}\n{stats}"


def render_donut_chart(
    segments: list[tuple[str, int, str]],
    radius: int = 4,
) -> str:
    """Return a Rich-markup proportional segmented bar with a legend.

    Parameters
    ----------
    segments:
        List of ``(label, count, color)`` tuples.
    radius:
        Unused (kept for API compat). Bar width is fixed at 40 chars.

    Returns
    -------
    str
        Multi-line string: colored bar + legend line(s).
    """
    total = sum(count for _, count, _ in segments)
    if total == 0:
        return f"[{TEXT_SECONDARY}]No data[/]"

    bar_width = 40
    parts: list[str] = []
    legend_parts: list[str] = []
    allocated = 0

    for idx, (label, count, seg_color) in enumerate(segments):
        pct = count / total
        if idx == len(segments) - 1:
            # Last segment gets remaining chars to avoid rounding gaps.
            chars = bar_width - allocated
        else:
            chars = max(1, round(pct * bar_width)) if count > 0 else 0
            allocated += chars

        if chars > 0:
            parts.append(f"[{seg_color}]{'█' * chars}[/]")

        pct_display = round(pct * 100)
        legend_parts.append(f"[{seg_color}]●[/] {label}: {count} ({pct_display}%)")

    bar = "".join(parts)
    legend = "  ".join(legend_parts)
    return f"{bar}\n{legend}"


def format_stats_line(
    label: str,
    min_val: float,
    avg_val: float,
    max_val: float,
    fmt_fn: Callable[[float], str] | None = None,
) -> str:
    """Format a ``min / avg / max`` stats line.

    Parameters
    ----------
    label:
        Currently unused — reserved for future prefix.
    min_val, avg_val, max_val:
        The statistics to display.
    fmt_fn:
        Value formatter. Defaults to ``${x:.2f}``.

    Returns
    -------
    str
        e.g. ``"min: $0.12  avg: $0.45  max: $1.02"``
    """
    fn = fmt_fn or _default_cost_fmt
    return f"min: {fn(min_val)}  avg: {fn(avg_val)}  max: {fn(max_val)}"


# ── Private helpers ──────────────────────────────────────────────────


def _default_cost_fmt(x: float) -> str:
    return f"${x:.2f}"
