"""Enhanced error detail panel with classification, root cause analysis, and suggestions.

Pure functions for error classification and suggestion generation, plus a
format_error_panel() function that produces Rich markup for the error detail view.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.widget import Widget


@dataclass
class ErrorClassification:
    """Result of classifying an error string."""

    category: str  # 'syntax_error', 'import_error', 'test_failure', 'timeout', 'permission', 'git_conflict', 'budget_exceeded', 'unknown'
    root_cause: str  # One-liner human-readable explanation
    file_path: str | None = None
    line_number: int | None = None


@dataclass
class ErrorSuggestion:
    """A suggested recovery action for an error."""

    key: str  # Keyboard shortcut (e.g. 'R', 'e', 's')
    label: str  # Display label
    action: str  # Action identifier: 'retry', 'edit', 'skip', 'view_output', 'increase_budget', 'manual_merge'


# Patterns for error classification, ordered by specificity
_PATTERNS: list[tuple[str, str, str]] = [
    (r"SyntaxError", "syntax_error", "Python syntax error — the agent produced invalid syntax"),
    (r"IndentationError", "syntax_error", "Indentation error — inconsistent indentation in generated code"),
    (r"ImportError|ModuleNotFoundError|No module named", "import_error", "Import error — the agent tried to import a module that doesn't exist or isn't installed"),
    (r"CONFLICT|merge conflict|rebase.*conflict|Merge.*failed", "git_conflict", "Git conflict — changes conflict with upstream code that needs manual resolution"),
    (r"budget|cost.*exceed|spending.*limit|budget_exceeded", "budget_exceeded", "Budget exceeded — the task ran over its allocated cost budget"),
    (r"TimeoutError|timed?\s*out|deadline exceeded|took too long", "timeout", "Timeout — the agent exceeded the time limit for this task"),
    (r"PermissionError|permission denied|EACCES|Operation not permitted", "permission", "Permission denied — the agent couldn't access a required file or resource"),
    (r"FAILED|AssertionError|pytest|test.*fail|failures?=\d+", "test_failure", "Test failure — one or more tests did not pass"),
]

# Traceback file/line extraction pattern
_TRACEBACK_RE = re.compile(
    r'File "([^"]+)", line (\d+)',
)


def classify_error(error_text: str) -> ErrorClassification:
    """Classify an error string into a category with root cause analysis.

    Pure function that examines the error text for known patterns and returns
    an ErrorClassification with the best matching category and a human-readable
    root cause explanation.
    """
    if not error_text:
        return ErrorClassification(
            category="unknown",
            root_cause="No error message available",
        )

    # Extract file path and line number from traceback if present
    file_path: str | None = None
    line_number: int | None = None
    tb_matches = list(_TRACEBACK_RE.finditer(error_text))
    if tb_matches:
        last_match = tb_matches[-1]  # Most relevant is usually the last frame
        file_path = last_match.group(1)
        line_number = int(last_match.group(2))

    # Match against patterns
    for pattern, category, root_cause in _PATTERNS:
        if re.search(pattern, error_text, re.IGNORECASE):
            # Enhance root cause with specifics when available
            enhanced = _enhance_root_cause(category, error_text, root_cause)
            return ErrorClassification(
                category=category,
                root_cause=enhanced,
                file_path=file_path,
                line_number=line_number,
            )

    return ErrorClassification(
        category="unknown",
        root_cause="Unexpected error — check the output log for details",
        file_path=file_path,
        line_number=line_number,
    )


def _enhance_root_cause(category: str, error_text: str, default: str) -> str:
    """Try to extract a more specific root cause from the error text."""
    if category == "import_error":
        m = re.search(r"No module named ['\"]?([^'\";\n]+)", error_text)
        if m:
            return f"Module '{m.group(1)}' not found — the agent tried to import a module that doesn't exist in the project"
    elif category == "syntax_error":
        m = re.search(r"SyntaxError:\s*(.+?)(?:\n|$)", error_text)
        if m:
            return f"Syntax error: {m.group(1).strip()}"
    elif category == "test_failure":
        m = re.search(r"(\d+)\s+(?:failed|failures)", error_text, re.IGNORECASE)
        if m:
            return f"{m.group(1)} test(s) failed — check the test output for assertion details"
    return default


# Suggestion sets per error category
_CATEGORY_SUGGESTIONS: dict[str, list[ErrorSuggestion]] = {
    "syntax_error": [
        ErrorSuggestion("R", "Retry with feedback", "retry"),
        ErrorSuggestion("e", "Edit files manually", "edit"),
        ErrorSuggestion("s", "Skip", "skip"),
    ],
    "import_error": [
        ErrorSuggestion("R", "Retry with feedback", "retry"),
        ErrorSuggestion("e", "Edit files manually", "edit"),
        ErrorSuggestion("s", "Skip", "skip"),
    ],
    "test_failure": [
        ErrorSuggestion("R", "Retry", "retry"),
        ErrorSuggestion("v", "View test output", "view_output"),
        ErrorSuggestion("s", "Skip", "skip"),
    ],
    "timeout": [
        ErrorSuggestion("R", "Retry (agent will resume)", "retry"),
        ErrorSuggestion("b", "Increase budget", "increase_budget"),
        ErrorSuggestion("s", "Skip", "skip"),
    ],
    "permission": [
        ErrorSuggestion("R", "Retry with feedback", "retry"),
        ErrorSuggestion("e", "Edit files manually", "edit"),
        ErrorSuggestion("s", "Skip", "skip"),
    ],
    "git_conflict": [
        ErrorSuggestion("R", "Retry (will rebase)", "retry"),
        ErrorSuggestion("m", "Manual merge", "manual_merge"),
        ErrorSuggestion("s", "Skip", "skip"),
    ],
    "budget_exceeded": [
        ErrorSuggestion("b", "Increase budget", "increase_budget"),
        ErrorSuggestion("R", "Retry", "retry"),
        ErrorSuggestion("s", "Skip", "skip"),
    ],
    "unknown": [
        ErrorSuggestion("R", "Retry", "retry"),
        ErrorSuggestion("v", "View full output", "view_output"),
        ErrorSuggestion("s", "Skip", "skip"),
    ],
}


def get_suggestions(classification: ErrorClassification, task: dict) -> list[ErrorSuggestion]:
    """Return context-aware action suggestions based on error classification.

    Pure function that maps error categories to appropriate recovery actions.
    The task dict provides additional context (e.g. files, state) for
    potentially customized suggestions.
    """
    return list(_CATEGORY_SUGGESTIONS.get(classification.category, _CATEGORY_SUGGESTIONS["unknown"]))


_ERROR_TAIL_LINES = 20

# Category display configuration
_CATEGORY_ICONS: dict[str, str] = {
    "syntax_error": "🔴",
    "import_error": "📦",
    "test_failure": "🧪",
    "timeout": "⏱",
    "permission": "🔒",
    "git_conflict": "⚔",
    "budget_exceeded": "💰",
    "unknown": "❓",
}

_CATEGORY_COLORS: dict[str, str] = {
    "syntax_error": "#f85149",
    "import_error": "#f0883e",
    "test_failure": "#d29922",
    "timeout": "#79c0ff",
    "permission": "#f85149",
    "git_conflict": "#a371f7",
    "budget_exceeded": "#f0883e",
    "unknown": "#8b949e",
}


def format_error_panel(
    task_id: str,
    task: dict,
    output_lines: list[str],
    error_history: list[str] | None = None,
) -> str:
    """Render an enhanced error detail view as a Rich markup string.

    Replaces the existing format_error_detail() output with:
    - Error classification header with icon and category
    - Root cause analysis one-liner
    - File/line context if parseable from traceback
    - Error history (if task has failed before)
    - Last output tail
    - Context-aware suggested actions
    """
    title = task.get("title", "Untitled")
    error = task.get("error", "Unknown error")
    files_changed = task.get("files_changed", [])

    # Classify the error
    # Combine error message with recent output for better classification
    full_error_text = error
    if output_lines:
        tail = output_lines[-_ERROR_TAIL_LINES:]
        full_error_text = error + "\n" + "\n".join(tail)

    classification = classify_error(full_error_text)
    suggestions = get_suggestions(classification, task)

    icon = _CATEGORY_ICONS.get(classification.category, "❓")
    color = _CATEGORY_COLORS.get(classification.category, "#8b949e")

    parts: list[str] = []

    # Header with classification
    parts.append(f"[bold {color}]{icon} {title} — {classification.category.upper().replace('_', ' ')}[/]")
    parts.append(f"[#30363d]{'─' * 60}[/]")

    # Root cause analysis
    parts.append(f"[bold #c9d1d9]Root cause:[/] [{color}]{classification.root_cause}[/]")

    # File/line context
    if classification.file_path or classification.line_number:
        loc_parts: list[str] = []
        if classification.file_path:
            loc_parts.append(classification.file_path)
        if classification.line_number:
            loc_parts.append(f"line {classification.line_number}")
        parts.append(f"[#8b949e]  📍 {' : '.join(loc_parts)}[/]")

    # Error message
    parts.append("")
    parts.append(f"[#f85149]{error}[/]")

    # Files changed
    if files_changed:
        parts.append("")
        parts.append("[#8b949e]Files modified:[/]")
        for f in files_changed:
            parts.append(f"[#8b949e]  {f}[/]")

    # Error history — show previous failures to help spot patterns
    if error_history:
        parts.append("")
        parts.append(f"[bold #d29922]⚠ Previous failures ({len(error_history)}):[/]")
        # Show last 3 previous errors
        for i, prev_error in enumerate(error_history[-3:], 1):
            # Truncate long messages
            display = prev_error[:120] + "..." if len(prev_error) > 120 else prev_error
            parts.append(f"[#d29922]  {i}. {display}[/]")

    # Last output
    parts.append("")
    parts.append("[#8b949e]── Last output ──[/]")
    tail = output_lines[-_ERROR_TAIL_LINES:] if output_lines else []
    if tail:
        parts.extend(tail)
    else:
        parts.append("[#8b949e]No output captured[/]")

    # Suggested actions
    parts.append("")
    action_parts = [f"\\[{s.key}] {s.label}" for s in suggestions]
    parts.append(f"[#8b949e]{' | '.join(action_parts)}[/]")

    return "\n".join(parts)


class ErrorPanel(Widget):
    """Textual widget that renders enhanced error detail for a task.

    Wraps format_error_panel() in a Textual Widget for composability.
    """

    DEFAULT_CSS = """
    ErrorPanel {
        layout: vertical;
        height: auto;
        padding: 1 2;
        background: #161b22;
        border: solid #30363d;
    }
    """

    def __init__(
        self,
        task_id: str = "",
        task: dict | None = None,
        output_lines: list[str] | None = None,
        error_history: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._task = task or {}
        self._output_lines = output_lines or []
        self._error_history = error_history

    def compose(self) -> ComposeResult:
        from textual.widgets import Static

        rendered = format_error_panel(
            self._task_id,
            self._task,
            self._output_lines,
            self._error_history,
        )
        yield Static(rendered, id="error-panel-content")

    def update_error(
        self,
        task_id: str,
        task: dict,
        output_lines: list[str],
        error_history: list[str] | None = None,
    ) -> None:
        """Update the error panel with new data."""
        self._task_id = task_id
        self._task = task
        self._output_lines = output_lines
        self._error_history = error_history
        try:
            from textual.widgets import Static

            content = self.query_one("#error-panel-content", Static)
            content.update(
                format_error_panel(task_id, task, output_lines, error_history)
            )
        except Exception:
            pass
