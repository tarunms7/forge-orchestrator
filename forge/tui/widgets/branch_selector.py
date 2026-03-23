"""Branch selector dropdown widgets for the TUI HomeScreen.

BranchSelector — Textual Select-based dropdown for picking a git branch.
BranchInput    — Textual Select with an "Auto-generate" option for pipeline branch.

Uses Textual's native Select widget for proper click/focus/overlay behavior.
"""

from __future__ import annotations

import logging

from rich.text import Text
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Select

logger = logging.getLogger("forge.tui.widgets.branch_selector")

_MAX_BRANCH_DISPLAY = 50
_MARKER_CURRENT = "●"
_AUTO_GENERATE_LABEL = "✦ Auto-generate from task"


def _truncate(name: str, max_len: int = _MAX_BRANCH_DISPLAY) -> str:
    """Truncate a branch name with ellipsis if too long."""
    return name[: max_len - 1] + "…" if len(name) > max_len else name


def _branch_label(name: str, current: str = "") -> Text:
    """Create a Rich Text label for a branch in the dropdown."""
    text = Text()
    if name == current:
        text.append(f"{_MARKER_CURRENT} ", style="#3fb950")
    else:
        text.append("  ")
    display = _truncate(name)
    if name.startswith("origin/"):
        text.append(display, style="#8b949e")
        text.append(" (remote)", style="#6e7681 dim")
    else:
        text.append(display, style="#e6edf3")
    if name == current:
        text.append(" (current)", style="#3fb950 dim")
    return text


class BranchSelector(Vertical):
    """Dropdown selector for git branches using Textual's native Select.

    Click or press Enter to expand the dropdown overlay. Type to search.
    Supports async branch loading from a git repo.

    Usage::

        selector = BranchSelector(id="base-branch")
        await selector.load_branches("/path/to/repo")
        # Later: selector.selected_value → "main"
    """

    DEFAULT_CSS = """
    BranchSelector {
        width: 1fr;
        height: auto;
    }
    BranchSelector Select {
        width: 100%;
    }
    """

    class BranchSelected(Message):
        """Posted when a branch is selected."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(
        self,
        default: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._default: str = default
        self._current_branch: str = ""
        self._repo_path: str = ""
        self._branches: list[str] = []
        self._initial_value: str = default or "main"

    @property
    def selected_value(self) -> str:
        """The currently selected branch name."""
        try:
            sel = self.query_one(Select)
            val = sel.value
            if val is Select.BLANK or val is None:
                return self._initial_value
            result = str(val)
            if result.startswith("origin/"):
                result = result[len("origin/") :]
            return result
        except Exception:
            return self._initial_value

    def compose(self):
        options = [(_truncate(self._initial_value), self._initial_value)]
        yield Select(
            options,
            value=self._initial_value,
            allow_blank=False,
            prompt="Select branch",
            id="branch-select",
        )

    async def load_branches(self, repo_path: str) -> None:
        """Load branches from a git repo and populate the Select."""
        from forge.core.daemon_helpers import list_local_branches

        self._repo_path = repo_path

        try:
            branches, current = await list_local_branches(
                repo_path, include_remote=True, return_current=True
            )
        except Exception:
            branches = ["main"]
            current = "main"

        self._branches = branches
        self._current_branch = current

        options: list[tuple[Text, str]] = []
        for b in branches:
            options.append((_branch_label(b, current), b))

        if not options:
            options = [(Text("main"), "main")]

        value = self._initial_value
        if self._default and self._default in branches:
            value = self._default
        elif current in branches:
            value = current
        elif branches:
            value = branches[0]

        try:
            sel = self.query_one(Select)
            sel.set_options(options)
            sel.value = value
        except Exception:
            logger.debug("Failed to update branch selector", exc_info=True)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Forward Select.Changed as BranchSelected."""
        if event.value is not Select.BLANK and event.value is not None:
            val = str(event.value)
            if val.startswith("origin/"):
                val = val[len("origin/") :]
            self.post_message(self.BranchSelected(val))

    async def action_refresh(self) -> None:
        """Fetch remote branches and reload."""
        if not self._repo_path:
            return
        from forge.core.daemon_helpers import fetch_remote_branches

        try:
            await fetch_remote_branches(self._repo_path)
            await self.load_branches(self._repo_path)
        except Exception:
            logger.debug("Failed to fetch remote branches", exc_info=True)


class BranchInput(Vertical):
    """Branch name input with dropdown of existing branches.

    First option is "Auto-generate from task" (value = "").
    User can also type a custom branch name in the text input.

    Usage::

        inp = BranchInput(id="branch-name")
        await inp.load_branches("/path/to/repo")
        # Later: inp.value → "" (auto) or "feat/my-branch"
    """

    DEFAULT_CSS = """
    BranchInput {
        width: 1fr;
        height: auto;
    }
    BranchInput Input {
        width: 100%;
        height: 3;
        border: tall #30363d;
        background: #161b22;
        color: #e6edf3;
        padding: 0 1;
    }
    BranchInput Input:focus {
        border: tall #58a6ff;
    }
    BranchInput Select {
        width: 100%;
        margin-top: 0;
    }
    """

    class BranchChosen(Message):
        """Posted when user confirms a branch."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(
        self,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._branches: list[str] = []

    @property
    def value(self) -> str:
        """Current value: typed text, selected branch, or empty for auto."""
        try:
            inp = self.query_one(Input)
            text = inp.value.strip()
            if text:
                # Strip origin/ prefix for consistency
                if text.startswith("origin/"):
                    text = text[len("origin/") :]
                return text
        except Exception:
            pass
        try:
            sel = self.query_one(Select)
            val = sel.value
            if val is Select.BLANK or val is None:
                return ""
            result = str(val)
            if result.startswith("origin/"):
                result = result[len("origin/") :]
            return result
        except Exception:
            return ""

    def compose(self):
        yield Input(
            placeholder="Type branch name or pick below",
            id="branch-text-input",
        )
        options: list[tuple[str | Text, str]] = [
            (Text(_AUTO_GENERATE_LABEL, style="#a371f7"), ""),
        ]
        yield Select(
            options,
            value="",
            allow_blank=False,
            prompt="Or select existing",
            id="branch-pick-select",
        )

    async def load_branches(self, repo_path: str) -> None:
        """Load branches for the dropdown."""
        from forge.core.daemon_helpers import list_local_branches

        try:
            branches, _ = await list_local_branches(repo_path, return_current=True)
            self._branches = branches
        except Exception:
            self._branches = []

        options: list[tuple[str | Text, str]] = [
            (Text(_AUTO_GENERATE_LABEL, style="#a371f7"), ""),
        ]
        for b in self._branches:
            options.append((_truncate(b), b))

        try:
            sel = self.query_one("#branch-pick-select", Select)
            sel.set_options(options)
            sel.value = ""
        except Exception:
            logger.debug("Failed to update branch input", exc_info=True)

    def on_select_changed(self, event: Select.Changed) -> None:
        """When user picks from dropdown, populate or clear the text input."""
        if event.value is not Select.BLANK and event.value is not None:
            val = str(event.value)
            # Strip origin/ prefix
            if val.startswith("origin/"):
                val = val[len("origin/") :]
            try:
                inp = self.query_one(Input)
                inp.value = val  # Empty string for auto-generate clears the input
            except Exception:
                pass
