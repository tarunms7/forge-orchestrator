"""Helpers for summarizing retry state on a task."""

from dataclasses import dataclass

from forge.core.error_classifier import classify_agent_error


@dataclass
class RetrySummary:
    """Human-readable summary of a task's retry state."""

    retry_count: int
    max_retries: int
    last_failure_category: str | None
    is_human_retry: bool
    label: str


def format_retry_summary(
    retry_count: int,
    max_retries: int,
    last_failure_category: str | None = None,
    is_human_retry: bool = False,
) -> RetrySummary:
    """Format retry state into a summary dataclass."""
    if retry_count == 0:
        label = ""
    else:
        label = f"Retry {retry_count}/{max_retries}"
        if last_failure_category:
            label += f" • last failure: {last_failure_category.replace('_', ' ')}"
        if is_human_retry:
            label += " (manual)"

    return RetrySummary(
        retry_count=retry_count,
        max_retries=max_retries,
        last_failure_category=last_failure_category,
        is_human_retry=is_human_retry,
        label=label,
    )


def retry_summary_from_task(task: dict, max_retries: int = 5) -> RetrySummary:
    """Build a retry summary from a task dict."""
    retry_count = task.get("retry_count", 0)
    error_message = task.get("error_message")
    retry_reason = task.get("retry_reason")
    last_failure_category = (
        classify_agent_error(error_message).category if error_message else None
    )
    return format_retry_summary(
        retry_count=retry_count,
        max_retries=max_retries,
        last_failure_category=last_failure_category,
        is_human_retry=retry_reason == "human",
    )
