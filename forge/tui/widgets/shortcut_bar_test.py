from forge.tui.widgets.shortcut_bar import ShortcutBar


def test_shortcut_bar_renders_keys():
    bar = ShortcutBar([("Enter", "Create PR"), ("r", "Retry Failed")])
    rendered = bar.render()
    text = str(rendered)
    assert "Enter" in text
    assert "Create PR" in text
    assert "r" in text
    assert "Retry Failed" in text


def test_shortcut_bar_empty():
    bar = ShortcutBar([])
    rendered = bar.render()
    assert str(rendered) == ""
