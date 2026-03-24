"""Export formatters for pipeline data.

Pure functions that convert PipelineExportData dicts into JSON, Markdown, or CSV strings.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone


def _fmt_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    if seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _fmt_cost(cost: float) -> str:
    """Format cost in USD."""
    if cost <= 0:
        return "-"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _task_total_duration(task: dict) -> float:
    """Sum all phase durations for a task."""
    return (
        task.get("agent_duration_s", 0.0)
        + task.get("review_duration_s", 0.0)
        + task.get("lint_duration_s", 0.0)
        + task.get("merge_duration_s", 0.0)
    )


def format_json(data: dict) -> str:
    """Convert pipeline export data to a pretty-printed JSON string."""
    return json.dumps(data, indent=2, default=str)


def format_markdown(data: dict) -> str:
    """Convert pipeline export data to a professional Markdown report."""
    lines: list[str] = []

    # H1 - pipeline description
    lines.append(f"# {data.get('description', 'Pipeline Report')}")
    lines.append("")

    # Summary section
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Status:** {data.get('status', 'unknown')}")
    lines.append(f"- **Duration:** {_fmt_duration(data.get('duration_s', 0.0))}")
    lines.append(f"- **Total Cost:** {_fmt_cost(data.get('total_cost_usd', 0.0))}")
    lines.append(
        f"- **Tokens:** {data.get('total_input_tokens', 0):,} in / "
        f"{data.get('total_output_tokens', 0):,} out"
    )
    lines.append(
        f"- **Tasks:** {data.get('tasks_succeeded', 0)} succeeded, "
        f"{data.get('tasks_failed', 0)} failed"
    )
    lines.append(f"- **Retries:** {data.get('total_retries', 0)}")

    if data.get("branch_name"):
        lines.append(f"- **Branch:** {data['branch_name']}")
    if data.get("base_branch"):
        lines.append(f"- **Base Branch:** {data['base_branch']}")
    if data.get("pr_url"):
        lines.append(f"- **PR:** {data['pr_url']}")

    lines.append("")

    # Tasks section
    tasks = data.get("tasks", [])
    lines.append("## Tasks")
    lines.append("")

    if not tasks:
        lines.append("No tasks.")
    else:
        # Table header
        lines.append(
            "| Task ID | Title | Status | Cost | Duration "
            "| Retries | Files Changed | Agent |"
        )
        lines.append(
            "|---------|-------|--------|------|----------"
            "|---------|---------------|-------|"
        )

        for t in tasks:
            task_id = t.get("id", "")[:8]
            title = t.get("title", "")
            status = t.get("state", "")
            cost = _fmt_cost(t.get("cost_usd", 0.0))
            duration = _fmt_duration(_task_total_duration(t))
            retries = t.get("retry_count", 0)
            files_changed = len(t.get("files", []))
            agent = t.get("assigned_agent") or "-"

            lines.append(
                f"| {task_id} | {title} | {status} | {cost} | {duration} "
                f"| {retries} | {files_changed} | {agent} |"
            )

    lines.append("")
    lines.append("---")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    lines.append(f"*Generated at {now}*")

    return "\n".join(lines)


def format_csv(data: dict) -> str:
    """Convert pipeline export data to a CSV string with one row per task."""
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "task_id",
        "title",
        "status",
        "cost_usd",
        "duration_s",
        "retry_count",
        "files_changed",
        "agent",
        "complexity",
        "input_tokens",
        "output_tokens",
        "error_message",
    ]
    writer.writerow(headers)

    for t in data.get("tasks", []):
        writer.writerow([
            t.get("id", ""),
            t.get("title", ""),
            t.get("state", ""),
            t.get("cost_usd", 0.0),
            _task_total_duration(t),
            t.get("retry_count", 0),
            len(t.get("files", [])),
            t.get("assigned_agent") or "",
            t.get("complexity", ""),
            t.get("input_tokens", 0),
            t.get("output_tokens", 0),
            t.get("error_message") or "",
        ])

    return output.getvalue()
