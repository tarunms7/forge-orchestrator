"""Forge logo widget."""

from __future__ import annotations

from textual.widgets import Static


FORGE_LOGO = """\
[bold #f2e2c8]FFFFF   OOO   RRRR   GGGG  EEEEE
F      O   O  R   R G      E
FFF    O   O  RRRR  G GGG  EEE
F      O   O  R  R  G   G  E
F       OOO   R   R  GGG   EEEEE[/]

[#f2e2c8]          O R C H E S T R A T O R[/]\
"""


class ForgeLogo(Static):
    """Renders the Forge logo with Rich markup."""

    DEFAULT_CSS = """
    ForgeLogo {
        width: auto;
        height: auto;
        max-height: 12;
        margin-top: 4;
        content-align: center middle;
        text-align: center;
    }
    """

    def __init__(self) -> None:
        super().__init__(FORGE_LOGO)
