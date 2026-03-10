"""Forge flame logo widget."""

from textual.widgets import Static


FORGE_LOGO = """\
[#f0883e]  ╭╮[/]
[#f0883e] ╔██╗╮[/]   [bold #58a6ff]F O R G E[/]
[#f0883e] ╔████╗[/]
[#f0883e]  ╔█╗[/]    [#8b949e]multi-agent orchestration[/]
[#f0883e]   ╗[/]\
"""


class ForgeLogo(Static):
    """Renders the Forge flame logo with Rich markup."""

    DEFAULT_CSS = """
    ForgeLogo {
        width: auto;
        height: 5;
        content-align: center middle;
        text-align: center;
        padding: 1 0;
    }
    """

    def __init__(self) -> None:
        super().__init__(FORGE_LOGO)
