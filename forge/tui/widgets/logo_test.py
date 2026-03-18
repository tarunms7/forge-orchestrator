"""Tests for the ForgeLogo widget."""

from __future__ import annotations

import re

from forge.tui.widgets.logo import FORGE_LOGO, ForgeLogo


EXPECTED_LOGO_PLAIN = """\
███████╗ ██████╗ ██████╗  ██████╗ ███████╗
██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
█████╗  ██║   ██║██████╔╝██║  ███╗█████╗
██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝
██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝

              ORCHESTRATOR"""


def _strip_markup(text: str) -> str:
    """Remove Rich markup tags for plain-text checks."""
    return re.sub(r"\[/?[^\]]*\]", "", text)


def test_forge_logo_constant_is_string() -> None:
    """FORGE_LOGO should be a non-empty string."""
    assert isinstance(FORGE_LOGO, str)
    assert FORGE_LOGO.strip()


def test_forge_logo_contains_orchestrator_text() -> None:
    """Logo should contain ORCHESTRATOR as subtitle."""
    plain = _strip_markup(FORGE_LOGO)
    assert "ORCHESTRATOR" in plain


def test_forge_logo_matches_expected_ascii_art() -> None:
    """Logo should match the expected FORGE art exactly."""
    plain = _strip_markup(FORGE_LOGO).rstrip()
    assert plain == EXPECTED_LOGO_PLAIN


def test_forge_logo_is_reasonable_height() -> None:
    """Logo content should stay at its expected line count."""
    plain = _strip_markup(FORGE_LOGO).strip("\n")
    lines = plain.split("\n")
    assert len(lines) == 8, f"Expected 8 lines, got {len(lines)}"


def test_forge_logo_has_correct_colors() -> None:
    """Logo should use the expected accent colors."""
    assert "#d6a85f" in FORGE_LOGO
    assert "#8aa9ff" in FORGE_LOGO


def test_forge_logo_uses_block_lettering() -> None:
    """Logo should use explicit block logo characters."""
    plain = _strip_markup(FORGE_LOGO)
    assert "█" in plain
    assert "╗" in plain


def test_forge_logo_widget_instantiates() -> None:
    """ForgeLogo widget should instantiate without error."""
    logo = ForgeLogo()
    assert logo is not None


def test_forge_logo_widget_has_correct_css_properties() -> None:
    """ForgeLogo DEFAULT_CSS should include centering properties."""
    css = ForgeLogo.DEFAULT_CSS
    assert "width: 100%" in css
    assert "text-align: center" in css
    assert "content-align: center middle" in css