"""Tests for forge.tui.widgets.charts — sparkline and donut-chart renderers."""

from __future__ import annotations

import re

from forge.tui.theme import ACCENT_BLUE, ACCENT_GREEN, ACCENT_RED, TEXT_SECONDARY
from forge.tui.widgets.charts import (
    format_stats_line,
    render_donut_chart,
    render_sparkline,
)

# ── Helpers ──────────────────────────────────────────────────────────

_RICH_TAG = re.compile(r"\[[^\]]*\]")


def _strip_markup(s: str) -> str:
    """Remove Rich markup tags to get raw text."""
    return _RICH_TAG.sub("", s)


# ── render_sparkline ─────────────────────────────────────────────────


def test_render_sparkline_empty() -> None:
    result = render_sparkline([])
    assert "No data" in result
    assert TEXT_SECONDARY in result


def test_render_sparkline_single_value() -> None:
    result = render_sparkline([5.0], width=10)
    lines = result.split("\n")
    bar_raw = _strip_markup(lines[0])
    # Single value → all-same → mid-height bars, width chars.
    assert len(bar_raw) == 10


def test_render_sparkline_ascending() -> None:
    values = [float(i) for i in range(8)]
    result = render_sparkline(values, width=8)
    bar_raw = _strip_markup(result.split("\n")[0])
    # Each character should be ≤ the next (ascending).
    for a, b in zip(bar_raw, bar_raw[1:], strict=False):
        assert a <= b, f"Expected ascending: {bar_raw}"


def test_render_sparkline_all_same() -> None:
    result = render_sparkline([3.0, 3.0, 3.0, 3.0], width=4)
    bar_raw = _strip_markup(result.split("\n")[0])
    # All same → uniform height.
    assert len(set(bar_raw)) == 1


def test_render_sparkline_width_respected() -> None:
    for w in (10, 20, 60):
        result = render_sparkline([1.0, 2.0, 3.0], width=w)
        bar_raw = _strip_markup(result.split("\n")[0])
        assert len(bar_raw) == w, f"Expected width {w}, got {len(bar_raw)}"


def test_render_sparkline_default_fmt() -> None:
    result = render_sparkline([0.5, 1.0, 1.5])
    assert "$0.50" in result
    assert "$1.50" in result


def test_render_sparkline_custom_fmt() -> None:
    result = render_sparkline([60.0, 120.0, 180.0], fmt=lambda x: f"{x:.0f}s")
    assert "60s" in result
    assert "180s" in result


def test_render_sparkline_color() -> None:
    result = render_sparkline([1.0, 2.0], color="#ff0000")
    assert "[#ff0000]" in result


# ── render_donut_chart ───────────────────────────────────────────────


def test_render_donut_chart_empty() -> None:
    result = render_donut_chart([])
    assert "No data" in result


def test_render_donut_chart_zero_total() -> None:
    result = render_donut_chart([("A", 0, ACCENT_GREEN)])
    assert "No data" in result


def test_render_donut_chart_proportions() -> None:
    segs = [
        ("Pass", 15, ACCENT_GREEN),
        ("Fail", 3, ACCENT_RED),
        ("Partial", 2, "#f0883e"),
    ]
    result = render_donut_chart(segs)
    bar_raw = _strip_markup(result.split("\n")[0])
    # Total bar width is 40.
    assert len(bar_raw) == 40
    # Legend must include all labels.
    assert "Pass: 15" in result
    assert "Fail: 3" in result
    assert "Partial: 2" in result
    assert "75%" in result
    assert "15%" in result
    assert "10%" in result


def test_render_donut_chart_single_segment() -> None:
    result = render_donut_chart([("All", 10, ACCENT_BLUE)])
    bar_raw = _strip_markup(result.split("\n")[0])
    assert len(bar_raw) == 40
    assert bar_raw == "█" * 40


def test_render_donut_chart_colors_in_markup() -> None:
    segs = [("A", 5, ACCENT_GREEN), ("B", 5, ACCENT_RED)]
    result = render_donut_chart(segs)
    assert f"[{ACCENT_GREEN}]" in result
    assert f"[{ACCENT_RED}]" in result


# ── format_stats_line ────────────────────────────────────────────────


def test_format_stats_line_default_fmt() -> None:
    result = format_stats_line("cost", 0.12, 0.45, 1.02)
    assert result == "min: $0.12  avg: $0.45  max: $1.02"


def test_format_stats_line_custom_fmt() -> None:
    fmt = lambda x: f"{x:.0f}s"  # noqa: E731
    result = format_stats_line("duration", 10, 30, 90, fmt_fn=fmt)
    assert result == "min: 10s  avg: 30s  max: 90s"
