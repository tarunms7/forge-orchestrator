"""Chat thread widget for agent Q&A interaction."""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

from forge.tui.theme import ACCENT_BLUE, ACCENT_ORANGE, TEXT_MUTED, TEXT_SECONDARY
from forge.tui.widgets.suggestion_chips import SuggestionChips


def _escape(text: str | None) -> str:
    """Escape Rich markup characters in user-provided text."""
    if text is None:
        return ""
    return text.replace("[", "\\[").replace("]", "\\]")


def format_work_log(lines: list[str]) -> str:
    if not lines:
        return f"[{TEXT_MUTED}]No activity yet[/]"
    formatted = []
    for line in lines[-10:]:  # show last 10
        formatted.append(f"  [{TEXT_SECONDARY}]{_escape(line)}[/]")
    return "\n".join(formatted)


def format_question_card(question: dict) -> str:
    """Format a question card with clear visual structure.

    Header changes based on question source:
    - review_escalation: "Review Could Not Complete"
    - review_uncertain: "Reviewer Is Uncertain"
    - default: "Question from Agent" (or Planner for planning phase)
    """
    q = question.get("question", "")
    ctx = question.get("context", "")
    source = question.get("source")

    if source == "review_escalation":
        header = "Review Could Not Complete"
    elif source == "review_uncertain":
        header = "Reviewer Is Uncertain"
    else:
        header = "Question from Agent"

    parts = []
    parts.append(f"[bold {ACCENT_ORANGE}]━━━ {_escape(header)} ━━━[/]")
    parts.append("")
    if ctx:
        parts.append(f"[{TEXT_SECONDARY}]{_escape(ctx)}[/]")
        parts.append("")
    parts.append(f"[bold {ACCENT_ORANGE}]{_escape(q)}[/]")
    parts.append("")
    parts.append(
        f"[{TEXT_SECONDARY}]Type your answer below, or press a number key (1-9) to select a suggestion:[/]"
    )
    return "\n".join(parts)


def format_review_progress(
    strategy: str | None,
    diff_lines: int | None,
    chunks: dict,  # {chunk_index: {"files": [...], "verdict": str|None, "risk_label": str}}
    current_chunk: int | str | None,
    chunk_count: int | None,
) -> str:
    """Format review progress header for Tier 2/3 reviews.

    Returns empty string for Tier 1 (no special display needed).
    """
    if not strategy or strategy == "tier1":
        return ""

    lines_str = f"{diff_lines} lines · " if diff_lines else ""

    if strategy == "tier2":
        # Just show the tier label — the risk map is already in the review text
        return f"[{TEXT_SECONDARY}]  ({lines_str}Risk-Enhanced)[/]"

    if strategy != "tier3":
        return ""

    # Tier 3: show chunk grid
    header = f"[{TEXT_SECONDARY}]  ({lines_str}Chunked · {chunk_count or len(chunks)} chunks)[/]"
    parts = [header]

    # Normalize current_chunk to int for comparison (JSON may deserialize as str)
    try:
        _current = (
            int(current_chunk)
            if current_chunk is not None and current_chunk != "synthesis"
            else current_chunk
        )
    except (ValueError, TypeError):
        _current = current_chunk

    for idx in sorted(chunks.keys()):
        chunk = chunks[idx]
        files = chunk.get("files", [])
        file_preview = ", ".join(str(f).split("/")[-1] for f in files[:3])
        if len(files) > 3:
            file_preview += f" +{len(files) - 3}"

        verdict = chunk.get("verdict")
        risk = chunk.get("risk_label", "?")
        total = chunk_count or len(chunks)

        if verdict == "PASS":
            icon = "[green]✓[/]"
            verdict_str = f"[green]{verdict}[/]"
        elif verdict == "FAIL":
            icon = "[red]✗[/]"
            verdict_str = f"[red]{verdict}[/]"
        elif verdict in ("UNCERTAIN", "TIMEOUT"):
            icon = "[yellow]?[/]"
            verdict_str = f"[yellow]{verdict}[/]"
        elif _current == idx:
            icon = f"[{ACCENT_BLUE}]⟳[/]"
            verdict_str = f"[{ACCENT_BLUE}]reviewing...[/]"
        else:
            icon = f"[{TEXT_MUTED}]○[/]"
            verdict_str = ""

        risk_badge = f"[{TEXT_MUTED}][{risk}][/]" if risk else ""
        chunk_line = (
            f"  {icon} Chunk {idx}/{total} {risk_badge} · "
            f"[{TEXT_SECONDARY}]{_escape(file_preview)}[/]"
        )
        if verdict_str:
            chunk_line += f"  {verdict_str}"
        parts.append(chunk_line)

    if current_chunk == "synthesis":
        parts.append(f"  [{ACCENT_BLUE}]⟳ Synthesizing results...[/]")

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
    ChatThread SuggestionChips { height: auto; max-height: 8; }
    ChatThread Input {
        dock: bottom;
        margin: 0 1;
        border: tall #30363d;
        background: #161b22;
    }
    ChatThread Input:focus {
        border: tall #58a6ff;
    }
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
            scroll.mount(
                Static(f"[{TEXT_SECONDARY}]Q: {q_text}[/]\n[{ACCENT_BLUE}]A: {a_text}[/]\n")
            )

        # Show work log
        if self._work_lines:
            scroll.mount(Static(format_work_log(self._work_lines)))
            scroll.mount(Static(""))  # spacer

        # Show current question
        if self._question:
            scroll.mount(Static(format_question_card(self._question)))

        scroll.scroll_end(animate=False)

    def update_question(
        self, question: dict, work_lines: list[str], history: list[dict] | None = None
    ) -> None:
        self._question = question
        self._work_lines = work_lines
        self._history = history or []
        chips = self.query_one(SuggestionChips)
        suggestions = list(question.get("suggestions", []))
        suggestions.append("Let agent decide")
        chips.update_suggestions(suggestions)
        chips.display = True
        self._render_scroll_content()
        # Focus the input after a short delay to ensure rendering is complete
        self.set_timer(0.1, self._focus_input)

    def _focus_input(self) -> None:
        """Focus the chat input. Called after a short delay to ensure widgets are mounted."""
        try:
            inp = self.query_one("#chat-input", Input)
            inp.focus()
        except Exception:
            pass

    def clear_question(self) -> None:
        self._question = None
        self.query_one(SuggestionChips).update_suggestions([])
        self.query_one("#chat-input", Input).value = ""
        self.query_one("#chat-scroll", VerticalScroll).remove_children()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        # Check if user typed just a number to select a suggestion
        if text.isdigit() and self._question:
            chips = self.query_one(SuggestionChips)
            n = int(text)
            suggestions = chips._suggestions
            if 1 <= n <= len(suggestions):
                event.input.value = ""
                self.post_message(self.AnswerSubmitted(self.task_id, suggestions[n - 1]))
                return
        if self._mode == "interjection":
            self.post_message(self.InterjectionSubmitted(self.task_id, text))
        else:
            self.post_message(self.AnswerSubmitted(self.task_id, text))
        event.input.value = ""

    def on_suggestion_chips_selected(self, event: SuggestionChips.Selected) -> None:
        self.post_message(self.AnswerSubmitted(self.task_id, event.text))
