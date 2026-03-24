"""Forge logo widget."""

from __future__ import annotations

from textual.widgets import Static

from forge.tui.theme import ACCENT_BLUE, ACCENT_GOLD

FORGE_LOGO = f"""\
[{ACCENT_GOLD}]███████╗ ██████╗ ██████╗  ██████╗ ███████╗[/]
[{ACCENT_GOLD}]██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝[/]
[{ACCENT_GOLD}]█████╗  ██║   ██║██████╔╝██║  ███╗█████╗  [/]
[{ACCENT_GOLD}]██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝  [/]
[{ACCENT_GOLD}]██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗[/]
[{ACCENT_GOLD}]╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝[/]
[{ACCENT_BLUE}]              O R C H E S T R A T O R[/]\
"""


class ForgeLogo(Static):
    """Renders the Forge logo with Rich markup."""

    DEFAULT_CSS = """
    ForgeLogo {
        width: 100%;
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
        content-align: center middle;
        text-align: center;
    }
    """

    def __init__(self) -> None:
        super().__init__(FORGE_LOGO)
