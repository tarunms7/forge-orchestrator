"""Tests for the ForgeLogo widget."""

from __future__ import annotations


from forge.tui.widgets.logo import FORGE_LOGO, ForgeLogo


EXPECTED_LOGO_PLAIN = """\
██████    █████    ██████    ██████  ██████
██      ██   ██  ██   ██  ██      ██
█████   ██   ██  ██████   ██  ███  █████
██      ██   ██  ██  ██   ██   ██  ██
██       █████   ██   ██   █████   ██████

          O R C H E S T R A T O R"""


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


def test_forge_logo_matches_expected_ascii_art() -> None:
    """Logo should match the expected block-style FORGE art exactly."""
    plain = _strip_markup(FORGE_LOGO).rstrip()
    assert plain == EXPECTED_LOGO_PLAIN


def test_forge_logo_is_reasonable_height() -> None:
    """Logo content should stay at its expected line count."""
    lines = FORGE_LOGO.strip().split('\n')
    assert len(lines) == 7, (
        f"Expected 7 lines, got {len(lines)}"
    )


def test_forge_logo_has_correct_color() -> None:
    """Logo should use #f2e2c8 color."""
    assert '#f2e2c8' in FORGE_LOGO


def test_forge_logo_uses_ascii_lettering() -> None:
    """Logo should use explicit monospaced block lettering."""
    plain = _strip_markup(FORGE_LOGO)
    assert '██████' in plain
    assert '██   ██' in plain


def test_forge_logo_widget_instantiates() -> None:
    """ForgeLogo widget should instantiate without error."""
    logo = ForgeLogo()
    assert logo is not None


def test_forge_logo_widget_has_correct_css_properties() -> None:
    """ForgeLogo DEFAULT_CSS should include centering properties."""
    css = ForgeLogo.DEFAULT_CSS
    assert 'text-align: center' in css
    assert 'content-align: center middle' in css
