from forge.tui.widgets.suggestion_chips import SuggestionChips, format_chips


def test_format_chips_renders_all():
    result = format_chips(["Option A", "Option B", "Let agent decide"], selected=0)
    assert "Option A" in result
    assert "Option B" in result


def test_format_chips_highlights_selected():
    result = format_chips(["A", "B"], selected=0)
    assert "bold" in result or "reverse" in result


def test_format_chips_empty():
    result = format_chips([], selected=0)
    assert result == ""
