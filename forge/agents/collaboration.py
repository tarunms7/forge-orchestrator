"""Cross-agent collaboration broker for pipeline context sharing.

When agents in a pipeline complete their work, the broker stores a
CompletionRecord with the diff, files changed, and key decisions extracted
from the agent's summary.  Downstream agents can query this to understand
what upstream tasks did — enabling informed decisions about shared files,
naming conventions, and interface contracts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("forge.agents.collaboration")

_MAX_DECISIONS = 10
_MAX_DECISION_LEN = 200

# Pattern to match diff section headers: "diff --git a/path b/path"
_DIFF_HEADER_RE = re.compile(r"^diff --git a/", re.MULTILINE)

# Patterns for extracting key decisions from agent summaries
_BULLET_RE = re.compile(r"^[ \t]*[-*]\s+(.+)", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^[ \t]*\d+\.\s+(.+)", re.MULTILINE)


@dataclass
class CompletionRecord:
    """Immutable record of a completed agent task."""

    task_id: str
    files_changed: list[str]
    implementation_summary: str
    key_decisions: list[str]
    diff: str
    completed_at: str


class AgentCollaborationBroker:
    """In-memory registry of CompletionRecords keyed by pipeline_id -> task_id.

    Provides cross-agent context sharing so downstream tasks can see what
    upstream tasks did, which files they changed, and what decisions they made.
    """

    def __init__(self) -> None:
        self._completions: dict[str, dict[str, CompletionRecord]] = {}

    def register_completion(
        self,
        pipeline_id: str,
        task_id: str,
        *,
        files_changed: list[str],
        implementation_summary: str,
        agent_summary: str,
        diff: str,
    ) -> None:
        """Create a CompletionRecord and store under pipeline_id -> task_id."""
        key_decisions = _extract_decisions(agent_summary)
        record = CompletionRecord(
            task_id=task_id,
            files_changed=files_changed,
            implementation_summary=implementation_summary,
            key_decisions=key_decisions,
            diff=diff,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if pipeline_id not in self._completions:
            self._completions[pipeline_id] = {}
        self._completions[pipeline_id][task_id] = record

    def get_completion(self, pipeline_id: str, task_id: str) -> CompletionRecord | None:
        """Retrieve a single CompletionRecord, or None if not found."""
        pipeline = self._completions.get(pipeline_id)
        if pipeline is None:
            return None
        return pipeline.get(task_id)

    def get_diff_for_file(
        self, pipeline_id: str, task_id: str, file_path: str
    ) -> str | None:
        """Parse the stored diff and return only the section for file_path.

        Splits on 'diff --git a/...' header pattern and returns the section
        whose header matches the given file_path.  Returns None if the task
        is not found or the file is not in the diff.
        """
        record = self.get_completion(pipeline_id, task_id)
        if record is None:
            return None

        # Split diff into sections by header
        sections = _DIFF_HEADER_RE.split(record.diff)
        # First element is content before the first header (usually empty)
        # Remaining elements need the header prefix restored
        for section in sections[1:]:
            # section starts right after "diff --git a/"
            # The full header line is "diff --git a/<path> b/<path>"
            first_line_end = section.find("\n")
            if first_line_end == -1:
                header_line = section
            else:
                header_line = section[:first_line_end]

            # Check if file_path appears in the header
            # Header format: "<path> b/<path>\n..."
            if file_path in header_line:
                return "diff --git a/" + section

        return None

    def get_all_completions(self, pipeline_id: str) -> dict[str, CompletionRecord]:
        """Return all CompletionRecords for a pipeline.

        Returns empty dict if pipeline_id not found.
        """
        return dict(self._completions.get(pipeline_id, {}))

    def cleanup(self, pipeline_id: str) -> None:
        """Remove all data for a pipeline. No-op if pipeline_id not found."""
        self._completions.pop(pipeline_id, None)


def _extract_decisions(text: str) -> list[str]:
    """Extract key decisions from agent summary text.

    Matches lines starting with '- ', '* ', or 'N. ' patterns.
    Caps at 10 decisions, each truncated to 200 chars.
    """
    decisions: list[str] = []

    for match in _BULLET_RE.finditer(text):
        decisions.append(match.group(1).strip())

    for match in _NUMBERED_RE.finditer(text):
        decisions.append(match.group(1).strip())

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for d in decisions:
        if d not in seen:
            seen.add(d)
            unique.append(d)

    # Truncate each decision and cap total count
    return [d[:_MAX_DECISION_LEN] for d in unique[:_MAX_DECISIONS]]
