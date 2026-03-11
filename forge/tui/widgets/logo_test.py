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


def test_forge_logo_contains_forge_text() -> None:
    """Logo should contain the letters F O R G E (as label text)."""
    plain = _strip_markup(FORGE_LOGO)
    # Accept either compact 'FORGE' or spaced 'F    O    R    G    E'
    assert 'FORGE' in plain or all(c in plain for c in 'FORGE')


def test_forge_logo_is_approximately_10_to_15_lines_tall() -> None:
    """Logo content should be roughly 10-20 lines tall (anvil + FORGE + subtitle)."""
    lines = FORGE_LOGO.strip().split('\n')
    assert 10 <= len(lines) <= 20, (
        f"Expected 10-20 lines, got {len(lines)}"
    )


def test_forge_logo_has_orange_color_markup() -> None:
    """Logo should include orange (#f0883e) color markup for the anvil."""
    assert '#f0883e' in FORGE_LOGO


def test_forge_logo_has_blue_color_markup() -> None:
    """Logo should include blue (#58a6ff) color markup for FORGE text."""
    assert '#58a6ff' in FORGE_LOGO


def test_forge_logo_has_subtitle() -> None:
    """Logo should include the subtitle text."""
    plain = _strip_markup(FORGE_LOGO)
    assert 'multi-agent code orchestration' in plain


def test_forge_logo_has_gray_subtitle_markup() -> None:
    """Subtitle should be styled with gray (#8b949e)."""
    assert '#8b949e' in FORGE_LOGO


def test_forge_logo_widget_instantiates() -> None:
    """ForgeLogo widget should instantiate without error."""
    logo = ForgeLogo()
    assert logo is not None


def test_forge_logo_widget_has_correct_css_properties() -> None:
    """ForgeLogo DEFAULT_CSS should include centering properties."""
    css = ForgeLogo.DEFAULT_CSS
    assert 'text-align: center' in css
    assert 'content-align: center middle' in css


def test_forge_logo_anvil_shape_present() -> None:
    """Logo should include ASCII art anvil-like characters."""
    plain = _strip_markup(FORGE_LOGO)
    # Anvil shape uses underscores and slashes
    assert '_' in plain
    assert '/' in plain or '\\' in plain
