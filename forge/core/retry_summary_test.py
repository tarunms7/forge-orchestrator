"""Tests for retry summary helpers."""

from forge.core.retry_summary import (
    format_retry_summary,
    retry_summary_from_task,
)


def test_format_retry_summary_zero_retries():
    summary = format_retry_summary(0, 5)
    assert summary.label == ""
    assert summary.retry_count == 0
    assert summary.max_retries == 5


def test_format_retry_summary_basic():
    summary = format_retry_summary(1, 3, last_failure_category="agent_timeout")
    assert summary.label == "Retry 1/3 • last failure: agent timeout"


def test_format_retry_summary_no_failure_category():
    summary = format_retry_summary(2, 5)
    assert summary.label == "Retry 2/5"


def test_format_retry_summary_human_retry():
    summary = format_retry_summary(1, 5, is_human_retry=True)
    assert summary.label == "Retry 1/5 (manual)"
    assert summary.is_human_retry


def test_human_retry_refund_behavior():
    summary = retry_summary_from_task(
        {
            "retry_count": 1,
            "retry_reason": "human",
        }
    )
    assert summary.label == "Retry 1/5 (manual)"
    assert summary.is_human_retry
    assert summary.max_retries - summary.retry_count == 4


def test_abrupt_failure_then_manual_retry_budget():
    summary = retry_summary_from_task(
        {
            "retry_count": 2,
            "retry_reason": "human",
        }
    )
    assert summary.label == "Retry 2/5 (manual)"
    assert summary.is_human_retry
    assert summary.max_retries - summary.retry_count == 3


def test_retry_summary_from_task_dict():
    summary = retry_summary_from_task(
        {
            "retry_count": 1,
            "error_message": "Agent timed out after 600s",
        }
    )
    assert summary.retry_count == 1
    assert summary.last_failure_category == "agent_timeout"
    assert summary.label == "Retry 1/5 • last failure: agent timeout"


def test_retry_summary_from_task_no_error():
    summary = retry_summary_from_task({"retry_count": 1})
    assert summary.retry_count == 1
    assert summary.last_failure_category is None
    assert summary.label == "Retry 1/5"
