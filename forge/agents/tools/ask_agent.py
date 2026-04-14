"""Query tool for downstream agents to get context from upstream completions.

Provides ``ask_prior_agent`` which queries the
:class:`~forge.agents.collaboration.AgentCollaborationBroker` for targeted
context about what an upstream task did — implementation summary, key
decisions, or file-specific diffs.
"""

from __future__ import annotations

import re

from forge.agents.collaboration import AgentCollaborationBroker

_MAX_RESPONSE_LEN = 4000

# Matches file-path-like tokens: at least one segment with a dot-extension,
# optionally preceded by directory components (e.g. "word.py", "forge/agents/foo.py").
_FILE_PATH_RE = re.compile(r"(?:[\w./-]+/)?[\w.-]+\.\w+")


def ask_prior_agent(
    broker: AgentCollaborationBroker,
    pipeline_id: str,
    task_id: str,
    question: str,
) -> str:
    """Query the broker for context about an upstream task.

    If *question* contains file path patterns, targeted diff sections are
    returned.  Otherwise a full summary (implementation_summary +
    key_decisions + first 2000 chars of the diff) is returned.

    The response is always prefixed with ``## Context from {task_id}`` and
    capped at 4000 characters.
    """
    record = broker.get_completion(pipeline_id, task_id)
    if record is None:
        return f"## Context from {task_id}\n\nNo completion record found for task `{task_id}` in pipeline `{pipeline_id}`."

    file_paths = _FILE_PATH_RE.findall(question)

    if file_paths:
        return _build_file_response(broker, pipeline_id, task_id, file_paths)

    return _build_summary_response(record)


def _build_file_response(
    broker: AgentCollaborationBroker,
    pipeline_id: str,
    task_id: str,
    file_paths: list[str],
) -> str:
    """Return targeted diff sections for the requested file paths."""
    parts: list[str] = [f"## Context from {task_id}\n"]

    for fp in file_paths:
        diff_section = broker.get_diff_for_file(pipeline_id, task_id, fp)
        if diff_section:
            parts.append(f"### {fp}\n```diff\n{diff_section}\n```\n")
        else:
            parts.append(f"### {fp}\nNo diff found for this file.\n")

    response = "\n".join(parts)
    return response[:_MAX_RESPONSE_LEN]


def _build_summary_response(record: "CompletionRecord") -> str:  # noqa: F821
    """Return implementation summary + key decisions + truncated diff."""
    parts: list[str] = [f"## Context from {record.task_id}\n"]

    parts.append(f"**Summary:** {record.implementation_summary}\n")

    if record.key_decisions:
        parts.append("**Key decisions:**")
        for decision in record.key_decisions:
            parts.append(f"- {decision}")
        parts.append("")

    diff_preview = record.diff[:2000]
    if diff_preview:
        parts.append(f"**Diff (truncated):**\n```diff\n{diff_preview}\n```")

    response = "\n".join(parts)
    return response[:_MAX_RESPONSE_LEN]
