"""Chat thread widget for agent Q&A interaction."""

from __future__ import annotations
from textual.widget import Widget
from textual.widgets import Input, Static
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

    class InterjectionSubmitted(Message):
        def __init__(self, task_id: str, message: str) -> None:
            super().__init__()
            self.task_id = task_id
            self.message = message

    DEFAULT_CSS = """
    ChatThread { height: 1fr; }
    ChatThread VerticalScroll { height: 1fr; }
    ChatThread Input { dock: bottom; margin: 0 1; }
    """

    def __init__(self, task_id: str = "", mode: str = "answer") -> None:
        super().__init__()
        self.task_id = task_id
        self._mode = mode  # "answer" or "interjection"
        self._work_lines: list[str] = []
        self._question: dict | None = None
        self._history: list[dict] = []

    def compose(self):
        yield VerticalScroll(id="chat-scroll")
        chips = SuggestionChips()
        if self._mode == "interjection":
            chips.display = False
        yield chips
        placeholder = (
            "Type a message to the agent..."
            if self._mode == "interjection"
            else "Type your answer or click a suggestion..."
        )
        yield Input(placeholder=placeholder, id="chat-input")

    def _render_scroll_content(self) -> None:
        """Populate the VerticalScroll with current question, work log, and history."""
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.remove_children()

        # Show previous Q&A history
        for entry in self._history:
            q_text = _escape(entry.get("question", ""))
            a_text = _escape(entry.get("answer", ""))
            scroll.mount(Static(f"[#8b949e]Q: {q_text}[/]\n[#58a6ff]A: {a_text}[/]\n"))

        # Show work log
        if self._work_lines:
            scroll.mount(Static(format_work_log(self._work_lines)))
            scroll.mount(Static(""))  # spacer

        # Show current question
        if self._question:
            scroll.mount(Static(format_question_card(self._question)))

        scroll.scroll_end(animate=False)

    def update_question(self, question: dict, work_lines: list[str], history: list[dict] | None = None) -> None:
        self._question = question
        self._work_lines = work_lines
        self._history = history or []
        chips = self.query_one(SuggestionChips)
        suggestions = question.get("suggestions", [])
        suggestions.append("Let agent decide")
        chips.update_suggestions(suggestions)
        self._render_scroll_content()
        self.query_one("#chat-input", Input).focus()

    def clear_question(self) -> None:
        self._question = None
        self.query_one(SuggestionChips).update_suggestions([])
        self.query_one("#chat-input", Input).value = ""
        self.query_one("#chat-scroll", VerticalScroll).remove_children()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            if self._mode == "interjection":
                self.post_message(self.InterjectionSubmitted(self.task_id, event.value.strip()))
            else:
                self.post_message(self.AnswerSubmitted(self.task_id, event.value.strip()))
            event.input.value = ""

    def on_suggestion_chips_selected(self, event: SuggestionChips.Selected) -> None:
        self.post_message(self.AnswerSubmitted(self.task_id, event.text))
