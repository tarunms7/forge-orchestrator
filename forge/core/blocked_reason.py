"""Pure function utilities for formatting scheduler blocking/waiting reasons into human-readable text."""

import re


def format_blocked_reason(reason: str) -> str:
    """
    Convert raw scheduler reason into short human-friendly explanation.

    Args:
        reason: Raw reason string from TaskSchedulingInsight.reason

    Returns:
        Short human-friendly string, or empty string for empty/None input

    Examples:
        'Waiting on task-2' → 'Waiting on task-2'
        'Waiting on task-2, task-3, task-4' → 'Waiting on task-2 + 2 others'
        'Blocked by failed dependency: auth-backend' → 'Blocked: auth-backend failed'
        'Human decision required before resume' → 'Needs human input before retry'
    """
    if not reason:
        return ""

    # Single waiting dependency - pass through
    if reason.startswith("Waiting on ") and ", " not in reason:
        return reason

    # Multiple waiting dependencies
    if reason.startswith("Waiting on "):
        deps = reason[11:].split(", ")  # Remove "Waiting on " prefix
        if len(deps) == 2:
            return f"Waiting on {deps[0]} + 1 other"
        elif len(deps) > 2:
            return f"Waiting on {deps[0]} + {len(deps) - 1} others"

    # Single failed dependency
    match = re.match(r"Blocked by failed dependency: (.+)", reason)
    if match:
        dep = match.group(1)
        return f"Blocked: {dep} failed"

    # Multiple failed dependencies
    match = re.match(r"Blocked by failed dependencies: (.+)", reason)
    if match:
        deps = match.group(1).split(", ")
        if len(deps) == 2:
            return f"Blocked: {deps[0]} + 1 other failed"
        elif len(deps) > 2:
            return f"Blocked: {deps[0]} + {len(deps) - 1} others failed"

    # Human decision required
    if reason == "Human decision required before resume":
        return "Needs human input before retry"

    # Human approval required
    if reason == "Human approval required before merge":
        return "Waiting for approval"

    # Manual intervention
    if reason == "Blocked - waiting for manual intervention":
        return "Blocked: needs manual intervention"

    # Task failed
    if reason == "Task failed and needs retry or skip":
        return "Failed: needs retry or skip"

    # Default fallback - return original reason
    return reason


def format_blocked_detail(reason: str) -> str:
    """
    Convert raw scheduler reason into multi-line detail explanation.

    Args:
        reason: Raw reason string from TaskSchedulingInsight.reason

    Returns:
        Multi-line string for detail panel, or empty string for empty/None reason

    Examples:
        waiting: 'Waiting for dependencies to complete:\n  - task-2\n  - task-3'
        blocked: 'Blocked by failed dependencies:\n  - auth-backend (failed)\n  - db-setup (failed)'
        human: 'This task needs human input before it can continue.'
    """
    if not reason:
        return ""

    # Waiting on dependencies
    if reason.startswith("Waiting on "):
        deps = reason[11:].split(", ")  # Remove "Waiting on " prefix
        lines = ["Waiting for dependencies to complete:"]
        for dep in deps:
            lines.append(f"  - {dep}")
        return "\n".join(lines)

    # Blocked by failed dependencies
    if reason.startswith("Blocked by failed dependenc"):
        if "dependency:" in reason:  # Single
            dep = reason.split(": ", 1)[1]
            return f"Blocked by failed dependency:\n  - {dep} (failed)"
        elif "dependencies:" in reason:  # Multiple
            deps = reason.split(": ", 1)[1].split(", ")
            lines = ["Blocked by failed dependencies:"]
            for dep in deps:
                lines.append(f"  - {dep} (failed)")
            return "\n".join(lines)

    # Human decision/approval needed
    if reason in ("Human decision required before resume", "Human approval required before merge"):
        return "This task needs human input before it can continue."

    # Manual intervention
    if reason == "Blocked - waiting for manual intervention":
        return "This task is blocked and needs manual intervention."

    # Task failed
    if reason == "Task failed and needs retry or skip":
        return "This task failed and needs to be retried or skipped."

    # Default fallback - return original reason
    return reason
