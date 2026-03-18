"""Forge logo widget."""

from __future__ import annotations

from textual.widgets import Static


FORGE_LOGO = """\
[#f2e2c8]███████   █████   ██████   ██████   ███████
██       ██   ██  ██   ██  ██       ██
█████    ██   ██  ██████   ██  ███  █████
██       ██   ██  ██  ██   ██   ██  ██
██        █████   ██   ██   █████   ███████[/]

[#f2e2c8]           O R C H E S T R A T O R[/]\
"""


class ForgeLogo(Static):
    """Renders the Forge logo with Rich markup."""

    DEFAULT_CSS = """
    ForgeLogo {
        width: auto;
        height: auto;
        max-height: 12;
        margin-top: 6;
        content-align: center middle;
        text-align: center;
    }
    """

    def __init__(self) -> None:
        super().__init__(FORGE_LOGO)
