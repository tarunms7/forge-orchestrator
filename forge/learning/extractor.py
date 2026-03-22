"""Lesson extraction — converts failures into structured lessons."""

from __future__ import annotations

import logging
import os
import re
import uuid

from forge.learning.store import Lesson

logger = logging.getLogger("forge.learning")

_INFRA_NOISE_PATTERNS = [
    "timeout", "timed out", "etimedout",
    "connection refused", "econnrefused", "econnreset",
    "server down", "server unavailable", "service unavailable",
    "503", "502", "504",
    "database is locked", "db lock",
    "oom", "out of memory", "killed",
    "disk full", "no space left",
    "sigkill", "sigterm",
    "[infrastructure crash]",
]

_ACTION_VERBS = {
    "changed", "replaced", "removed", "added", "fixed", "updated",
    "switched", "moved", "renamed", "set", "used", "imported",
    "configured", "wrapped", "converted",
}


def is_infra_noise(text: str) -> bool:
    """Check if text describes infrastructure noise, not a real learning."""
    lower = text.lower()
    return any(pattern in lower for pattern in _INFRA_NOISE_PATTERNS)


def extract_from_agent_learning(
    data: dict,
    task_title: str = "",
    project_dir: str | None = None,
) -> Lesson | None:
    """Extract a validated lesson from an agent's FORGE_LEARNING self-report.

    Returns None if the data fails validation (missing fields, infra noise,
    no action verb, etc.).
    """
    trigger = data.get("trigger")
    resolution = data.get("resolution")
    files = data.get("files")

    # Validate required fields exist and are long enough
    if not isinstance(trigger, str) or len(trigger) <= 10:
        logger.debug("Learning rejected: trigger missing or too short")
        return None
    if not isinstance(resolution, str) or len(resolution) <= 10:
        logger.debug("Learning rejected: resolution missing or too short")
        return None
    if not isinstance(files, list) or not files:
        logger.debug("Learning rejected: files missing or empty")
        return None

    # Reject infrastructure noise
    if is_infra_noise(trigger) or is_infra_noise(resolution):
        logger.debug("Learning rejected: infrastructure noise detected")
        return None

    # Validate resolution contains an action verb
    words = set(resolution.lower().split())
    if not words & _ACTION_VERBS:
        logger.debug("Learning rejected: no action verb in resolution")
        return None

    # Build title from trigger
    title = trigger[:60] + ("..." if len(trigger) > 60 else "")
    if task_title:
        title = f"[{task_title[:30]}] {title}"

    scope = classify_scope(
        command="",
        error_output=f"{trigger} {resolution}",
        project_dir=project_dir,
    )

    return Lesson(
        id=str(uuid.uuid4()),
        scope=scope,
        category=data.get("category", "code_pattern"),
        title=title,
        content=f"Agent learning: {trigger}\nFiles: {', '.join(files)}",
        trigger=trigger,
        resolution=resolution,
        confidence=0.5,
    )


def extract_from_command_failures(
    failures: list,  # list[FailureRecord] but avoiding circular import
    project_dir: str | None = None,
) -> Lesson:
    """Extract a lesson from a sequence of command failures.

    Takes the failure records from RuntimeGuard and produces a structured
    lesson that can be stored and injected into future agent prompts.
    """
    if not failures:
        raise ValueError("Cannot extract lesson from empty failures list")

    # Use the first failure as the representative
    first = failures[0]
    # Build title from the command and error
    cmd_short = _shorten_command(first.command)
    title = f"{cmd_short} fails with {first.error_class}"

    # Build content describing the failure pattern
    content_lines = [
        f"Command pattern: `{first.normalized_command}`",
        f"Error type: {first.error_class}",
        f"Failed {len(failures)} times with the same approach.",
        "",
        "Failure details:",
    ]
    for f in failures[:5]:  # max 5 examples
        content_lines.append(f"  Attempt {f.attempt_number}: `{f.command}`")
        if f.stderr_snippet:
            # Keep just the first meaningful line of the error
            err_line = _first_meaningful_line(f.stderr_snippet)
            if err_line:
                content_lines.append(f"    Error: {err_line}")

    content = "\n".join(content_lines)

    # Build resolution based on error class
    resolution = _resolution_for_error(first.error_class, first.command, first.stderr_snippet)

    # Build trigger from normalized command
    trigger = first.normalized_command

    scope = classify_scope(
        command=first.command,
        error_output=first.stderr_snippet,
        project_dir=project_dir,
    )

    return Lesson(
        id=str(uuid.uuid4()),
        scope=scope,
        category="command_failure",
        title=title,
        content=content,
        trigger=trigger,
        resolution=resolution,
    )


def extract_from_review_feedback(
    feedback: str,
    task_title: str = "",
    project_dir: str | None = None,
) -> Lesson:
    """Extract a lesson from review feedback.

    Creates a lesson from reviewer rejection feedback so future agents
    can avoid the same mistakes.
    """
    title = _summarize_feedback(feedback)

    # The trigger is a normalized version of the feedback theme
    trigger = _extract_feedback_theme(feedback)

    content = f"Review feedback: {feedback[:500]}"
    if task_title:
        content = f"Task: {task_title}\n{content}"

    resolution = f"Before submitting: {feedback[:200]}"

    scope = classify_scope(
        command="",
        error_output=feedback,
        project_dir=project_dir,
    )

    return Lesson(
        id=str(uuid.uuid4()),
        scope=scope,
        category="review_failure",
        title=title,
        content=content,
        trigger=trigger,
        resolution=resolution,
    )


def classify_scope(
    command: str = "",
    error_output: str = "",
    project_dir: str | None = None,
) -> str:
    """Classify whether a lesson is global or project-scoped.

    Project-scoped: references project-specific paths, configs, tools.
    Global: universal patterns that apply to any project.
    """
    text = f"{command} {error_output}".lower()

    # Project-specific indicators
    if project_dir:
        # References to project directory
        if project_dir.lower() in text:
            return "project"
        # References to project-specific config files
        project_name = os.path.basename(project_dir)
        if project_name.lower() in text and len(project_name) > 3:
            return "project"

    # Venv references are project-scoped (each project has its own venv)
    if '.venv/' in text or 'venv/' in text:
        return "project"

    # Project-specific config patterns
    project_configs = ['.forge/', 'pyproject.toml', 'package.json', 'tsconfig']
    if any(cfg in text for cfg in project_configs):
        return "project"

    # Everything else is global (applies universally)
    return "global"


def _shorten_command(command: str) -> str:
    """Shorten a command for use in a lesson title."""
    cmd = command.strip()
    # Take just the base command and first arg
    parts = cmd.split()
    if len(parts) <= 3:
        return cmd
    return " ".join(parts[:3]) + "..."


def _first_meaningful_line(text: str) -> str:
    """Extract the first meaningful error line from output."""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip generic Python traceback frames
        if line.startswith("File ") or line.startswith("Traceback"):
            continue
        if len(line) > 10:  # skip very short lines
            return line[:200]
    return text[:200] if text else ""


def _resolution_for_error(error_class: str, command: str, stderr: str) -> str:
    """Generate a resolution suggestion based on error class."""
    resolutions = {
        "module_not_found": "Check what's actually installed (`pip list` or `pip show`). "
                           "Use `python -m` prefix or install the missing module. "
                           "Don't retry the same import path.",
        "command_not_found": "Verify the tool exists (`which <tool>`). "
                            "Use an alternative tool or install the missing one. "
                            "Check if the tool is available under a different name.",
        "permission_denied": "Check file permissions first (`ls -la`). "
                            "Don't retry with sudo unless explicitly authorized.",
        "syntax_error": "Read the error location carefully. Fix the syntax issue "
                       "at the reported line before re-running.",
        "import_error": "Check the actual module structure. Verify the import path "
                       "matches the installed package layout.",
        "file_not_found": "Verify the file path exists before using it. "
                         "Use `ls` or `find` to locate the correct path.",
        "timeout": "The operation took too long. Try a smaller scope, "
                  "add a timeout flag, or use a different approach entirely.",
        "test_failure": "Read the test output carefully. Fix the failing assertion "
                       "rather than re-running and hoping for a different result.",
        "connection_error": "Verify the service is running and accessible. "
                           "Check the port and host. Don't retry immediately.",
    }
    base = resolutions.get(error_class, "Diagnose the root cause before retrying. "
                                         "Try a fundamentally different approach.")
    return base


def _summarize_feedback(feedback: str) -> str:
    """Create a short title from review feedback."""
    # Take first sentence or first 60 chars
    first_line = feedback.split('\n')[0].split('. ')[0]
    if len(first_line) > 60:
        return first_line[:57] + "..."
    return first_line


def _extract_feedback_theme(feedback: str) -> str:
    """Extract the core theme/pattern from review feedback for matching."""
    # Lowercase and strip specifics
    theme = feedback.lower()
    # Remove file paths
    theme = re.sub(r'[/\\][\w./\\-]+\.\w+', '', theme)
    # Remove line numbers
    theme = re.sub(r'line\s+\d+', '', theme)
    # Remove backtick code
    theme = re.sub(r'`[^`]+`', '', theme)
    # Take first 100 chars of what's left
    theme = ' '.join(theme.split())[:100]
    return theme
