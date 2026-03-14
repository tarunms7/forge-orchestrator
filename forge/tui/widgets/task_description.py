"""Task description overlay — shows original pipeline prompt and task summary."""

from __future__ import annotations

import logging

from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget

logger = logging.getLogger("forge.tui.widgets.task_description")


def format_task_description(
    description: str,
    tasks: list[dict],
    created_at: str = "",
    scroll_offset: int = 0,
    max_visible: int = 30,
) -> str:
    """Render the task description overlay content."""
    parts: list[str] = []

    # Header
    parts.append("[bold #58a6ff]── TASK INFO ──[/]")
    parts.append("")

    # Original prompt
    parts.append("[bold #f0883e]  Original Prompt[/]")
    parts.append("")

    content_lines: list[str] = []

    # Wrap description lines
    for line in description.split("\n"):
        content_lines.append(f"    [#c9d1d9]{line}[/]")
    content_lines.append("")

    # Creation timestamp
    if created_at:
        content_lines.append(f"  [#8b949e]Created: {created_at}[/]")
        content_lines.append("")

    # Task breakdown
    if tasks:
        content_lines.append("[bold #3fb950]  Task Breakdown[/]")
        content_lines.append("")
        for i, task in enumerate(tasks, 1):
            title = task.get("title", "Untitled")
            state = task.get("state", "todo")
            complexity = task.get("complexity", "medium")

            # State indicators
            state_icons = {
                "todo": "[#484f58]○[/]",
                "in_progress": "[#f0883e]◐[/]",
                "in_review": "[#a371f7]◑[/]",
                "done": "[#3fb950]●[/]",
                "error": "[#f85149]✗[/]",
                "awaiting_input": "[#58a6ff]?[/]",
                "awaiting_approval": "[#f0883e]![/]",
            }
            icon = state_icons.get(state, "[#484f58]○[/]")

            content_lines.append(
                f"    {icon} [bold #c9d1d9]{i}. {title}[/]  [#484f58]({complexity})[/]"
            )

            # Show description if available
            desc = task.get("description", "")
            if desc:
                # Truncate long descriptions
                if len(desc) > 100:
                    desc = desc[:97] + "..."
                content_lines.append(f"      [#8b949e]{desc}[/]")

            # Show files if available
            files = task.get("files", [])
            if files:
                files_str = ", ".join(files[:5])
                if len(files) > 5:
                    files_str += f" (+{len(files) - 5} more)"
                content_lines.append(f"      [#484f58]files: {files_str}[/]")

        content_lines.append("")

        # Summary stats
        done = sum(1 for t in tasks if t.get("state") == "done")
        total = len(tasks)
        content_lines.append(f"  [#8b949e]Progress: {done}/{total} tasks complete[/]")

    # Apply scroll window
    visible = content_lines[scroll_offset : scroll_offset + max_visible]
    parts.extend(visible)

    # Scroll indicator
    total_lines = len(content_lines)
    if total_lines > max_visible:
        remaining = max(0, total_lines - scroll_offset - max_visible)
        if remaining > 0:
            parts.append(f"  [#484f58]↓ {remaining} more lines (j/k to scroll)[/]")
        if scroll_offset > 0:
            parts.append(f"  [#484f58]↑ {scroll_offset} lines above[/]")

    # Footer
    parts.append("")
    parts.append("[#484f58]  Esc: dismiss │ j/k: scroll[/]")

    return "\n".join(parts)


class TaskDescriptionOverlay(Widget):
    """Modal overlay showing the original task/pipeline description.

    Mount this widget and call .open() to show, .close() to hide.
    Bindings: j/k scroll, Esc dismisses.
    """

    DEFAULT_CSS = """
    TaskDescriptionOverlay {
        width: 100%;
        height: 100%;
        background: rgba(13, 17, 23, 0.95);
        content-align: center top;
        padding: 2 4;
        layer: overlay;
        display: none;
    }
    TaskDescriptionOverlay.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Dismiss", show=False, priority=True),
        Binding("j", "scroll_down", "Scroll down", show=False, priority=True),
        Binding("k", "scroll_up", "Scroll up", show=False, priority=True),
        Binding("down", "scroll_down", "Scroll down", show=False, priority=True),
        Binding("up", "scroll_up", "Scroll up", show=False, priority=True),
    ]

    class Dismissed(Message):
        """Posted when the overlay is dismissed."""

        pass

    def __init__(
        self,
        description: str = "",
        tasks: list[dict] | None = None,
        created_at: str = "",
    ) -> None:
        super().__init__()
        self._description = description
        self._tasks: list[dict] = list(tasks or [])
        self._created_at = created_at
        self._scroll_offset: int = 0
        self._max_visible: int = 30

    @property
    def is_open(self) -> bool:
        return self.has_class("visible")

    @property
    def description(self) -> str:
        return self._description

    @property
    def tasks(self) -> list[dict]:
        return list(self._tasks)

    @property
    def scroll_offset(self) -> int:
        return self._scroll_offset

    def open(
        self,
        description: str | None = None,
        tasks: list[dict] | None = None,
        created_at: str | None = None,
    ) -> None:
        """Show the overlay, optionally updating content."""
        if description is not None:
            self._description = description
        if tasks is not None:
            self._tasks = list(tasks)
        if created_at is not None:
            self._created_at = created_at
        self._scroll_offset = 0
        self.add_class("visible")
        try:
            self.focus()
        except Exception:
            pass  # No active app in test context
        self.refresh()

    def close(self) -> None:
        """Hide the overlay."""
        self.remove_class("visible")
        self._scroll_offset = 0

    def _total_content_lines(self) -> int:
        """Calculate total content lines for scroll bounds."""
        total = 0
        # Description lines
        total += len(self._description.split("\n"))
        total += 1  # blank
        if self._created_at:
            total += 2  # created_at + blank
        if self._tasks:
            total += 2  # header + blank
            for task in self._tasks:
                total += 1  # task line
                if task.get("description"):
                    total += 1
                if task.get("files"):
                    total += 1
            total += 2  # blank + summary
        return total

    def action_scroll_down(self) -> None:
        max_offset = max(0, self._total_content_lines() - self._max_visible)
        if self._scroll_offset < max_offset:
            self._scroll_offset += 1
            self.refresh()

    def action_scroll_up(self) -> None:
        if self._scroll_offset > 0:
            self._scroll_offset -= 1
            self.refresh()

    def action_dismiss(self) -> None:
        self.close()
        self.post_message(self.Dismissed())

    def render(self) -> str:
        return format_task_description(
            self._description,
            self._tasks,
            self._created_at,
            self._scroll_offset,
            self._max_visible,
        )
