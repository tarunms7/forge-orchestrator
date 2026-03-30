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


def test_shortcut_bar_reactive_update():
    bar = ShortcutBar([("a", "Action A")])
    bar.shortcuts = [("b", "Action B"), ("c", "Action C")]
    assert len(bar.shortcuts) == 2


def test_shortcut_bar_update_shortcuts_method():
    """update_shortcuts() replaces internal list and is equivalent to setting reactive."""
    bar = ShortcutBar([("a", "Action A")])
    bar.update_shortcuts([("x", "New X"), ("y", "New Y")])
    assert bar.shortcuts == [("x", "New X"), ("y", "New Y")]
    rendered = bar.render()
    text = str(rendered)
    assert "New X" in text
    assert "New Y" in text
    # Old shortcuts should be gone
    assert "Action A" not in text


def test_shortcut_bar_update_shortcuts_empty():
    """update_shortcuts([]) clears the bar."""
    bar = ShortcutBar([("a", "Action A")])
    bar.update_shortcuts([])
    assert bar.shortcuts == []
    assert str(bar.render()) == ""
