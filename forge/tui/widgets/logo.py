"""Forge anvil logo widget."""

from __future__ import annotations

from textual.widgets import Static


FORGE_LOGO = """\
[#f0883e]           _______________
          /               \\_____
         /    ___________        \\
        /    /           \\        \\
       /    /   ◆  ◆  ◆   \\        \\
      /    /_______________\\        \\
     /____/_________________\\_______\\
    |_________________________________|
              |           |
        ______|___________|______
       |_______________________|[/]

[bold #58a6ff] ███████  ██████  ██████   ██████  ███████
 ██       ██  ██  ██  ██  ██       ██
 █████    ██  ██  ██████  ██  ███  █████
 ██       ██  ██  ██ ██   ██   ██  ██
 ██        ██████ ██  ██   ██████  ███████
   F    O    R    G    E[/]
[#8b949e]      multi-agent code orchestration[/]\
"""


class ForgeLogo(Static):
    """Renders the Forge anvil logo with Rich markup."""

    DEFAULT_CSS = """
    ForgeLogo {
        width: auto;
        height: 20;
        content-align: center middle;
        text-align: center;
        padding: 1 0;
    }
    """

    def __init__(self) -> None:
        super().__init__(FORGE_LOGO)
