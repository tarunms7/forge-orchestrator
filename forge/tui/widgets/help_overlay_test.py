"""Tests for HelpOverlay widget."""

from __future__ import annotations

from forge.tui.widgets.help_overlay import (
    GLOBAL_HELP,
    HOME_HELP,
    HOME_TIPS,
    PIPELINE_HELP,
    REVIEW_HELP,
    SCREEN_HELP,
    SETTINGS_HELP,
    HelpEntry,
    HelpOverlay,
    format_help_entry,
    format_help_overlay,
    get_help_for_screen,
    get_tips_for_screen,
)


class TestHelpEntry:
    """Tests for the HelpEntry dataclass."""

    def test_create_entry(self):
        entry = HelpEntry(
            key="Ctrl+P",
            action="Command Palette",
            description="Open command palette",
            category="Tools",
        )
        assert entry.key == "Ctrl+P"
        assert entry.action == "Command Palette"
        assert entry.description == "Open command palette"
        assert entry.category == "Tools"

    def test_entry_fields(self):
        entry = HelpEntry("j/k", "Navigate", "Move cursor", "Navigation")
        assert entry.key == "j/k"
        assert entry.category == "Navigation"


class TestHelpData:
    """Tests for per-screen help data definitions."""

    def test_global_help_has_entries(self):
        assert len(GLOBAL_HELP) > 0
        categories = {e.category for e in GLOBAL_HELP}
        assert "Navigation" in categories
        assert "Tools" in categories

    def test_home_help_has_entries(self):
        assert len(HOME_HELP) > 0

    def test_home_tips_has_tips(self):
        assert len(HOME_TIPS) > 0
        assert any("Ctrl+S" in tip for tip in HOME_TIPS)

    def test_pipeline_help_covers_key_bindings(self):
        keys = {e.key for e in PIPELINE_HELP}
        assert "j/k" in keys
        assert "o" in keys
        assert "d" in keys
        assert "g" in keys

    def test_review_help_has_approve_reject(self):
        actions = {e.action for e in REVIEW_HELP}
        assert "Approve" in actions
        assert "Reject" in actions

    def test_settings_help_has_entries(self):
        assert len(SETTINGS_HELP) > 0

    def test_screen_help_map_has_all_screens(self):
        assert "HomeScreen" in SCREEN_HELP
        assert "PipelineScreen" in SCREEN_HELP
        assert "ReviewScreen" in SCREEN_HELP
        assert "SettingsScreen" in SCREEN_HELP

    def test_all_entries_have_valid_categories(self):
        valid = {"Navigation", "Actions", "Views", "Tools"}
        all_entries = GLOBAL_HELP + HOME_HELP + PIPELINE_HELP + REVIEW_HELP + SETTINGS_HELP
        for entry in all_entries:
            assert entry.category in valid, f"{entry.action} has invalid category {entry.category}"


class TestGetHelpForScreen:
    """Tests for get_help_for_screen function."""

    def test_home_screen_includes_global(self):
        entries = get_help_for_screen("HomeScreen")
        actions = {e.action for e in entries}
        # Should have home-specific entries
        assert "Submit task" in actions
        # Should also have global entries
        assert "Quit" in actions
        assert "Command Palette" in actions

    def test_pipeline_screen_includes_global(self):
        entries = get_help_for_screen("PipelineScreen")
        actions = {e.action for e in entries}
        assert "Navigate tasks" in actions
        assert "Quit" in actions

    def test_unknown_screen_returns_global_only(self):
        entries = get_help_for_screen("UnknownScreen")
        assert entries == GLOBAL_HELP

    def test_get_tips_for_home(self):
        tips = get_tips_for_screen("HomeScreen")
        assert len(tips) > 0

    def test_get_tips_for_unknown_screen(self):
        tips = get_tips_for_screen("PipelineScreen")
        assert tips == []


class TestFormatHelpEntry:
    """Tests for format_help_entry function."""

    def test_format_entry_contains_key(self):
        entry = HelpEntry("Ctrl+P", "Palette", "Open palette", "Tools")
        result = format_help_entry(entry)
        assert "Ctrl+P" in result
        assert "Palette" in result
        assert "Open palette" in result

    def test_format_entry_uses_category_color(self):
        entry = HelpEntry("j", "Down", "Move down", "Navigation")
        result = format_help_entry(entry)
        assert "#58a6ff" in result  # Navigation color

    def test_format_entry_tools_color(self):
        entry = HelpEntry("?", "Help", "Show help", "Tools")
        result = format_help_entry(entry)
        assert "#a371f7" in result  # Tools color


class TestFormatHelpOverlay:
    """Tests for format_help_overlay function."""

    def test_renders_header(self):
        entries = [HelpEntry("q", "Quit", "Quit app", "Navigation")]
        result = format_help_overlay("HomeScreen", entries, [])
        assert "HELP" in result

    def test_renders_tips(self):
        entries = [HelpEntry("q", "Quit", "Quit app", "Navigation")]
        tips = ["Try pressing q to quit"]
        result = format_help_overlay("HomeScreen", entries, tips)
        assert "Quick Start" in result
        assert "Try pressing q" in result

    def test_no_tips_section_when_empty(self):
        entries = [HelpEntry("q", "Quit", "Quit app", "Navigation")]
        result = format_help_overlay("PipelineScreen", entries, [])
        assert "Quick Start" not in result

    def test_renders_categories(self):
        entries = [
            HelpEntry("j", "Down", "Move down", "Navigation"),
            HelpEntry("a", "Approve", "Approve item", "Actions"),
        ]
        result = format_help_overlay("ReviewScreen", entries, [])
        assert "Navigation" in result
        assert "Actions" in result

    def test_renders_footer(self):
        entries = [HelpEntry("q", "Quit", "Quit app", "Navigation")]
        result = format_help_overlay("HomeScreen", entries, [])
        assert "Esc: dismiss" in result
        assert "Ctrl+P for command palette" in result

    def test_scroll_indicator_shown_when_overflow(self):
        entries = [
            HelpEntry(f"k{i}", f"Action{i}", f"Description {i}", "Navigation")
            for i in range(40)
        ]
        result = format_help_overlay("Test", entries, [], scroll_offset=0, max_visible=10)
        assert "more lines" in result

    def test_scroll_up_indicator(self):
        entries = [
            HelpEntry(f"k{i}", f"Action{i}", f"Description {i}", "Navigation")
            for i in range(40)
        ]
        result = format_help_overlay("Test", entries, [], scroll_offset=5, max_visible=10)
        assert "lines above" in result

    def test_no_scroll_indicator_when_fits(self):
        entries = [HelpEntry("q", "Quit", "Quit", "Navigation")]
        result = format_help_overlay("Test", entries, [], max_visible=50)
        assert "more lines" not in result


class TestHelpOverlayWidget:
    """Tests for the HelpOverlay widget logic."""

    def test_init_default(self):
        overlay = HelpOverlay()
        assert overlay.screen_name == "HomeScreen"
        assert len(overlay.entries) > 0
        assert overlay.scroll_offset == 0
        assert overlay.is_open is False

    def test_init_with_screen(self):
        overlay = HelpOverlay(screen_name="PipelineScreen")
        assert overlay.screen_name == "PipelineScreen"
        actions = {e.action for e in overlay.entries}
        assert "Navigate tasks" in actions

    def test_open_sets_visible(self):
        overlay = HelpOverlay()
        overlay.open()
        assert overlay.is_open is True
        assert overlay.scroll_offset == 0

    def test_open_with_different_screen(self):
        overlay = HelpOverlay(screen_name="HomeScreen")
        overlay.open(screen_name="ReviewScreen")
        assert overlay.screen_name == "ReviewScreen"
        actions = {e.action for e in overlay.entries}
        assert "Approve" in actions

    def test_close_hides(self):
        overlay = HelpOverlay()
        overlay.open()
        overlay.close()
        assert overlay.is_open is False
        assert overlay.scroll_offset == 0

    def test_scroll_down(self):
        overlay = HelpOverlay(screen_name="PipelineScreen")
        overlay._max_visible = 5  # Force small window to enable scrolling
        overlay.action_scroll_down()
        assert overlay.scroll_offset == 1

    def test_scroll_up(self):
        overlay = HelpOverlay()
        overlay._scroll_offset = 3
        overlay.action_scroll_up()
        assert overlay.scroll_offset == 2

    def test_scroll_up_stops_at_zero(self):
        overlay = HelpOverlay()
        overlay._scroll_offset = 0
        overlay.action_scroll_up()
        assert overlay.scroll_offset == 0

    def test_scroll_down_stops_at_max(self):
        overlay = HelpOverlay()
        overlay._max_visible = 1000  # Very large window
        overlay.action_scroll_down()
        assert overlay.scroll_offset == 0  # No overflow, stays at 0

    def test_dismiss_posts_message(self):
        overlay = HelpOverlay()
        overlay.open()
        messages: list = []
        overlay.post_message = lambda m: messages.append(m)
        overlay.action_dismiss()
        assert len(messages) == 1
        assert isinstance(messages[0], HelpOverlay.Dismissed)
        assert overlay.is_open is False

    def test_render_contains_help_content(self):
        overlay = HelpOverlay(screen_name="HomeScreen")
        rendered = overlay.render()
        assert "HELP" in rendered
        assert "Ctrl+P for command palette" in rendered

    def test_render_home_shows_tips(self):
        overlay = HelpOverlay(screen_name="HomeScreen")
        rendered = overlay.render()
        assert "Quick Start" in rendered

    def test_render_pipeline_no_tips(self):
        overlay = HelpOverlay(screen_name="PipelineScreen")
        rendered = overlay.render()
        assert "Quick Start" not in rendered

    def test_entries_returns_copy(self):
        overlay = HelpOverlay()
        entries1 = overlay.entries
        entries2 = overlay.entries
        assert entries1 is not entries2
        assert entries1 == entries2

    def test_tips_returns_copy(self):
        overlay = HelpOverlay(screen_name="HomeScreen")
        tips1 = overlay.tips
        tips2 = overlay.tips
        assert tips1 is not tips2

    def test_home_tips_property(self):
        overlay = HelpOverlay(screen_name="HomeScreen")
        assert len(overlay.tips) > 0

    def test_pipeline_no_tips(self):
        overlay = HelpOverlay(screen_name="PipelineScreen")
        assert overlay.tips == []

    def test_bindings_defined(self):
        """HelpOverlay should have escape, j, k bindings."""
        keys = [b.key for b in HelpOverlay.BINDINGS]
        assert "escape" in keys
        assert "j" in keys
        assert "k" in keys

    def test_bindings_have_priority(self):
        """All bindings should have priority=True."""
        for binding in HelpOverlay.BINDINGS:
            assert binding.priority is True, f"Binding {binding.key} lacks priority"

    def test_total_content_lines(self):
        overlay = HelpOverlay(screen_name="HomeScreen")
        total = overlay._total_content_lines()
        assert total > 0


class TestClearInputHelpEntries:
    """Tests that help data includes the 'Clear Input' keybinding entry."""

    def test_home_help_contains_clear_input(self):
        """HOME_HELP should include a 'Clear Input' action entry."""
        assert any(e.action == "Clear Input" for e in HOME_HELP)

    def test_pipeline_help_contains_clear_input(self):
        """PIPELINE_HELP should include a 'Clear Input' action entry."""
        assert any(e.action == "Clear Input" for e in PIPELINE_HELP)
