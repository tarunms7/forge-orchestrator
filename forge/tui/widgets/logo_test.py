"""Tests for the ForgeLogo widget."""

from __future__ import annotations


from forge.tui.widgets.logo import FORGE_LOGO, ForgeLogo


def _strip_markup(text: str) -> str:
    """Remove Rich markup tags for plain-text checks."""
    import re
    return re.sub(r'\[/?[^\]]*\]', '', text)


def test_forge_logo_constant_is_string() -> None:
    """FORGE_LOGO should be a non-empty string."""
    assert isinstance(FORGE_LOGO, str)
    assert len(FORGE_LOGO) > 0


def test_forge_logo_contains_orchestrator_text() -> None:
    """Logo should contain ORCHESTRATOR as subtitle."""
    plain = _strip_markup(FORGE_LOGO)
    assert 'O R C H E S T R A T O R' in plain


def test_forge_logo_is_approximately_10_to_15_lines_tall() -> None:
    """Logo content should be roughly 8-16 lines tall."""
    lines = FORGE_LOGO.strip().split('\n')
    assert 8 <= len(lines) <= 16, (
        f"Expected 8-16 lines, got {len(lines)}"
    )


def test_forge_logo_has_warm_color_markup() -> None:
    """Logo should use #f2e2c8 color."""
    assert '#f2e2c8' in FORGE_LOGO


def test_forge_logo_uses_box_drawing_characters() -> None:
    """Logo should use box-drawing characters for circuit-board style."""
    plain = _strip_markup(FORGE_LOGO)
    assert '┌' in plain
    assert '┐' in plain
    assert '│' in plain
    assert '└' in plain


def test_forge_logo_widget_instantiates() -> None:
    """ForgeLogo widget should instantiate without error."""
    logo = ForgeLogo()
    assert logo is not None


def test_forge_logo_widget_has_correct_css_properties() -> None:
    """ForgeLogo DEFAULT_CSS should include centering properties."""
    css = ForgeLogo.DEFAULT_CSS
    assert 'text-align: center' in css
    assert 'content-align: center middle' in css
