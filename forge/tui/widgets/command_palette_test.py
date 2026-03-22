"""Tests for CommandPalette widget."""

from __future__ import annotations

from forge.tui.widgets.command_palette import (
    COMMAND_PALETTE_BINDING_DESCRIPTION,
    COMMAND_PALETTE_BINDING_KEY,
    CommandPalette,
    CommandPaletteAction,
    format_palette,
    format_result_line,
    fuzzy_match,
    fuzzy_score,
    get_all_actions,
)

# ---------------------------------------------------------------------------
# fuzzy_score tests
# ---------------------------------------------------------------------------


class TestFuzzyScore:
    def test_empty_query_matches_everything(self):
        assert fuzzy_score("", "anything") > 0

    def test_exact_match_scores_high(self):
        score = fuzzy_score("home", "Home")
        assert score >= 100

    def test_prefix_match_scores_higher(self):
        prefix = fuzzy_score("home", "Home Screen")
        substring = fuzzy_score("home", "Go Home")
        assert prefix > substring

    def test_no_match_returns_zero(self):
        assert fuzzy_score("xyz", "Home") == 0

    def test_fuzzy_chars_in_order(self):
        score = fuzzy_score("hm", "Home")
        assert score > 0

    def test_fuzzy_chars_not_in_order(self):
        assert fuzzy_score("mh", "Home") == 0

    def test_longer_query_scores_higher(self):
        short = fuzzy_score("h", "Home")
        long = fuzzy_score("home", "Home")
        assert long > short

    def test_consecutive_chars_bonus(self):
        consecutive = fuzzy_score("ho", "Home")
        spread = fuzzy_score("he", "Home")
        assert consecutive >= spread


# ---------------------------------------------------------------------------
# fuzzy_match tests
# ---------------------------------------------------------------------------


class TestFuzzyMatch:
    def _make_actions(self):
        return [
            CommandPaletteAction(
                name="Home", description="Go to home screen", category="Navigation"
            ),
            CommandPaletteAction(
                name="Pipeline", description="Go to pipeline screen", category="Navigation"
            ),
            CommandPaletteAction(
                name="Toggle DAG", description="Toggle dependency graph", category="View"
            ),
            CommandPaletteAction(
                name="Settings", description="Open settings", category="Navigation"
            ),
            CommandPaletteAction(name="Help", description="Show help", category="Tools"),
        ]

    def test_empty_query_returns_all(self):
        actions = self._make_actions()
        results = fuzzy_match("", actions)
        assert len(results) == len(actions)

    def test_exact_name_match(self):
        actions = self._make_actions()
        results = fuzzy_match("Home", actions)
        assert len(results) > 0
        assert results[0].name == "Home"

    def test_partial_match(self):
        actions = self._make_actions()
        results = fuzzy_match("pipe", actions)
        assert any(a.name == "Pipeline" for a in results)

    def test_description_match(self):
        actions = self._make_actions()
        results = fuzzy_match("dependency", actions)
        assert any(a.name == "Toggle DAG" for a in results)

    def test_no_match_returns_empty(self):
        actions = self._make_actions()
        results = fuzzy_match("zzzzz", actions)
        assert len(results) == 0

    def test_case_insensitive(self):
        actions = self._make_actions()
        results = fuzzy_match("home", actions)
        assert results[0].name == "Home"

    def test_results_sorted_by_relevance(self):
        actions = self._make_actions()
        results = fuzzy_match("set", actions)
        # "Settings" should rank first (exact prefix on name)
        assert results[0].name == "Settings"


# ---------------------------------------------------------------------------
# format tests
# ---------------------------------------------------------------------------


class TestFormatResultLine:
    def test_selected_line_has_bold(self):
        action = CommandPaletteAction(name="Test", description="desc", category="Tools")
        line = format_result_line(action, selected=True)
        assert "bold" in line
        assert "Test" in line

    def test_unselected_line(self):
        action = CommandPaletteAction(name="Test", description="desc", category="Tools")
        line = format_result_line(action, selected=False)
        assert "○" in line
        assert "Test" in line

    def test_shortcut_displayed(self):
        action = CommandPaletteAction(
            name="Home", description="Go home", shortcut="1", category="Navigation"
        )
        line = format_result_line(action, selected=False)
        assert "1" in line

    def test_no_shortcut(self):
        action = CommandPaletteAction(
            name="Test", description="desc", shortcut="", category="Tools"
        )
        line = format_result_line(action, selected=False)
        # Should not crash, no shortcut section
        assert "Test" in line


class TestFormatPalette:
    def test_header_present(self):
        result = format_palette("", [], 0)
        assert "COMMAND PALETTE" in result

    def test_empty_query_placeholder(self):
        result = format_palette("", [], 0)
        assert "Type to search" in result

    def test_query_displayed(self):
        actions = [CommandPaletteAction(name="Home", description="Go home", category="Navigation")]
        result = format_palette("ho", actions, 0)
        assert "ho" in result

    def test_no_results_message(self):
        result = format_palette("xyz", [], 0)
        assert "No matching" in result

    def test_category_headers(self):
        actions = [
            CommandPaletteAction(name="Home", description="Go home", category="Navigation"),
            CommandPaletteAction(name="DAG", description="Toggle", category="View"),
        ]
        result = format_palette("", actions, 0)
        assert "Navigation" in result
        assert "View" in result

    def test_footer_present(self):
        result = format_palette("", [], 0)
        assert "Enter" in result
        assert "Esc" in result


# ---------------------------------------------------------------------------
# get_all_actions tests
# ---------------------------------------------------------------------------


class TestGetAllActions:
    def test_returns_non_empty_list(self):
        actions = get_all_actions()
        assert len(actions) > 0

    def test_all_have_required_fields(self):
        for action in get_all_actions():
            assert action.name
            assert action.description
            assert action.category in ("Navigation", "Pipeline", "Tools", "View")
            assert action.callback_name

    def test_contains_home_action(self):
        actions = get_all_actions()
        names = [a.name for a in actions]
        assert "Home" in names

    def test_contains_command_palette_action(self):
        actions = get_all_actions()
        names = [a.name for a in actions]
        assert "Command Palette" in names

    def test_categories_used(self):
        actions = get_all_actions()
        categories = {a.category for a in actions}
        assert "Navigation" in categories
        assert "Pipeline" in categories
        assert "Tools" in categories
        assert "View" in categories


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_binding_key(self):
        assert COMMAND_PALETTE_BINDING_KEY == "ctrl+p"

    def test_binding_description(self):
        assert COMMAND_PALETTE_BINDING_DESCRIPTION == "Command Palette"


# ---------------------------------------------------------------------------
# CommandPalette widget tests
# ---------------------------------------------------------------------------


class TestCommandPaletteWidget:
    def test_init_defaults(self):
        palette = CommandPalette()
        assert palette.query == ""
        assert len(palette.results) > 0
        assert palette.selected_index == 0

    def test_init_custom_actions(self):
        actions = [
            CommandPaletteAction(
                name="Foo", description="Do foo", category="Tools", callback_name="foo"
            ),
        ]
        palette = CommandPalette(actions=actions)
        assert len(palette.results) == 1
        assert palette.results[0].name == "Foo"

    def test_set_query_filters_results(self):
        actions = [
            CommandPaletteAction(
                name="Home", description="Go home", category="Navigation", callback_name="home"
            ),
            CommandPaletteAction(
                name="Pipeline", description="Go pipe", category="Navigation", callback_name="pipe"
            ),
        ]
        palette = CommandPalette(actions=actions)
        palette.set_query("home")
        assert len(palette.results) == 1
        assert palette.results[0].name == "Home"

    def test_set_query_no_match(self):
        actions = [
            CommandPaletteAction(
                name="Home", description="Go home", category="Navigation", callback_name="home"
            ),
        ]
        palette = CommandPalette(actions=actions)
        palette.set_query("zzzzz")
        assert len(palette.results) == 0

    def test_cursor_down(self):
        actions = [
            CommandPaletteAction(name="A", description="a", category="Tools", callback_name="a"),
            CommandPaletteAction(name="B", description="b", category="Tools", callback_name="b"),
            CommandPaletteAction(name="C", description="c", category="Tools", callback_name="c"),
        ]
        palette = CommandPalette(actions=actions)
        assert palette.selected_index == 0
        palette.action_cursor_down()
        assert palette.selected_index == 1
        palette.action_cursor_down()
        assert palette.selected_index == 2
        palette.action_cursor_down()
        assert palette.selected_index == 2  # Stays at end

    def test_cursor_up(self):
        actions = [
            CommandPaletteAction(name="A", description="a", category="Tools", callback_name="a"),
            CommandPaletteAction(name="B", description="b", category="Tools", callback_name="b"),
        ]
        palette = CommandPalette(actions=actions)
        palette._selected_index = 1
        palette.action_cursor_up()
        assert palette.selected_index == 0
        palette.action_cursor_up()
        assert palette.selected_index == 0  # Stays at start

    def test_delete_char(self):
        palette = CommandPalette()
        palette._query = "hom"
        palette.action_delete_char()
        assert palette.query == "ho"
        palette.action_delete_char()
        assert palette.query == "h"
        palette.action_delete_char()
        assert palette.query == ""
        palette.action_delete_char()  # No crash on empty
        assert palette.query == ""

    def test_selected_action(self):
        actions = [
            CommandPaletteAction(name="A", description="a", category="Tools", callback_name="a"),
            CommandPaletteAction(name="B", description="b", category="Tools", callback_name="b"),
        ]
        palette = CommandPalette(actions=actions)
        assert palette.selected_action is not None
        assert palette.selected_action.name == "A"
        palette.action_cursor_down()
        assert palette.selected_action.name == "B"

    def test_selected_action_empty_results(self):
        actions = [
            CommandPaletteAction(name="A", description="a", category="Tools", callback_name="a"),
        ]
        palette = CommandPalette(actions=actions)
        palette.set_query("zzzzz")  # Filter to no results
        assert palette.selected_action is None

    def test_execute_posts_message(self):
        actions = [
            CommandPaletteAction(
                name="Test", description="test", category="Tools", callback_name="test_action"
            ),
        ]
        palette = CommandPalette(actions=actions)
        messages = []
        palette.post_message = lambda m: messages.append(m)
        palette.action_execute()
        assert len(messages) == 1
        assert isinstance(messages[0], CommandPalette.ActionSelected)
        assert messages[0].action.callback_name == "test_action"

    def test_execute_no_results_does_nothing(self):
        actions = [
            CommandPaletteAction(name="A", description="a", category="Tools", callback_name="a"),
        ]
        palette = CommandPalette(actions=actions)
        palette.set_query("zzzzz")  # Filter to no results
        messages = []
        palette.post_message = lambda m: messages.append(m)
        palette.action_execute()
        assert len(messages) == 0

    def test_dismiss_posts_message(self):
        palette = CommandPalette()
        messages = []
        palette.post_message = lambda m: messages.append(m)
        palette.action_dismiss()
        assert len(messages) == 1
        assert isinstance(messages[0], CommandPalette.Dismissed)

    def test_render_returns_string(self):
        palette = CommandPalette()
        result = palette.render()
        assert isinstance(result, str)
        assert "COMMAND PALETTE" in result

    def test_set_query_resets_selected_index(self):
        actions = [
            CommandPaletteAction(name="A", description="a", category="Tools", callback_name="a"),
            CommandPaletteAction(name="B", description="b", category="Tools", callback_name="b"),
        ]
        palette = CommandPalette(actions=actions)
        palette._selected_index = 1
        palette.set_query("A")
        assert palette.selected_index == 0

    def test_selected_index_clamped_on_filter(self):
        actions = [
            CommandPaletteAction(
                name="Alpha", description="a", category="Tools", callback_name="a"
            ),
            CommandPaletteAction(name="Beta", description="b", category="Tools", callback_name="b"),
            CommandPaletteAction(
                name="Gamma", description="g", category="Tools", callback_name="g"
            ),
        ]
        palette = CommandPalette(actions=actions)
        palette._selected_index = 2
        # Filter to just "Alpha"
        palette.set_query("alpha")
        assert palette.selected_index == 0

    def test_open_and_close_state(self):
        """Test open/close toggle class-based visibility."""
        palette = CommandPalette()
        # Initially not open (no 'visible' class)
        assert not palette.has_class("visible")

        palette.open()
        assert palette.has_class("visible")
        assert palette.query == ""

        palette.close()
        assert not palette.has_class("visible")


class TestClearInputAction:
    """Tests that CommandPalette includes a 'Clear Input' action."""

    def test_get_all_actions_contains_clear_input(self):
        """get_all_actions() should include a 'Clear Input' action."""
        actions = get_all_actions()
        names = [a.name for a in actions]
        assert "Clear Input" in names

    def test_clear_input_action_has_required_fields(self):
        """'Clear Input' action should have name, description, callback_name, and valid category."""
        actions = get_all_actions()
        clear_actions = [a for a in actions if a.name == "Clear Input"]
        assert len(clear_actions) == 1
        action = clear_actions[0]
        assert action.description
        assert action.callback_name
        assert action.category in ("Navigation", "Pipeline", "Tools", "View")

    def test_fuzzy_match_clear_returns_clear_input(self):
        """Fuzzy matching 'clear' should return the 'Clear Input' action."""
        actions = get_all_actions()
        results = fuzzy_match("clear", actions)
        assert any(a.name == "Clear Input" for a in results)
