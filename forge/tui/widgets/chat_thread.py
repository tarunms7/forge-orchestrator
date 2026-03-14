"""Chat thread widget for agent Q&A interaction."""

from __future__ import annotations
from textual.widget import Widget
from textual.widgets import Input
from textual.containers import VerticalScroll
from textual.message import Message

from forge.tui.widgets.suggestion_chips import SuggestionChips


def _escape(text: str | None) -> str:
    """Escape Rich markup characters in user-provided text."""
    if text is None:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


def format_work_log(lines: list[str]) -> str:
    if not lines:
        return "[#484f58]No activity yet[/]"
    formatted = []
    for line in lines[-10:]:  # show last 10
        formatted.append(f"  [#8b949e]{_escape(line)}[/]")
    return "\n".join(formatted)


def format_question_card(question: dict) -> str:
    q = question.get("question", "")
    ctx = question.get("context", "")
    parts = []
    if ctx:
        parts.append(f"[#c9d1d9]{_escape(ctx)}[/]")
    parts.append(f"\n[#f0883e]{_escape(q)}[/]")
    return "\n".join(parts)


class ChatThread(Widget):
    class AnswerSubmitted(Message):
        def __init__(self, task_id: str, answer: str) -> None:
            self.task_id = task_id
            self.answer = answer
            super().__init__()

    DEFAULT_CSS = """
    ChatThread { height: 1fr; }
    ChatThread VerticalScroll { height: 1fr; }
    ChatThread Input { dock: bottom; margin: 0 1; }
    """

    def __init__(self, task_id: str = "") -> None:
        super().__init__()
        self.task_id = task_id
        self._work_lines: list[str] = []
        self._question: dict | None = None
        self._history: list[dict] = []

    def compose(self):
        yield VerticalScroll(id="chat-scroll")
        yield SuggestionChips()
        yield Input(placeholder="Type your answer or click a suggestion...", id="chat-input")

    def update_question(self, question: dict, work_lines: list[str], history: list[dict] | None = None) -> None:
        self._question = question
        self._work_lines = work_lines
        self._history = history or []
        chips = self.query_one(SuggestionChips)
        suggestions = question.get("suggestions", [])
        suggestions.append("Let agent decide")
        chips.update_suggestions(suggestions)
        self.refresh()

    def clear_question(self) -> None:
        self._question = None
        self.query_one(SuggestionChips).update_suggestions([])
        self.query_one("#chat-input", Input).value = ""
        self.refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.post_message(self.AnswerSubmitted(self.task_id, event.value.strip()))
            event.input.value = ""

    def on_suggestion_chips_selected(self, event: SuggestionChips.Selected) -> None:
        self.post_message(self.AnswerSubmitted(self.task_id, event.text))
