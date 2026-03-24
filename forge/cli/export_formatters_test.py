"""Tests for forge.cli.export_formatters."""

from __future__ import annotations

import csv
import io
import json

import pytest

from forge.cli.export_formatters import format_csv, format_json, format_markdown

SAMPLE_TASK_1 = {
    "id": "t1-uuid-1234-5678-abcdef",
    "title": "Add validators",
    "description": "Add input validation to all API endpoints",
    "state": "done",
    "files": ["forge/api/routes/tasks.py", "forge/api/models/schemas.py"],
    "assigned_agent": "agent-0",
    "cost_usd": 0.45,
    "agent_cost_usd": 0.35,
    "review_cost_usd": 0.10,
    "retry_count": 0,
    "input_tokens": 15000,
    "output_tokens": 4000,
    "started_at": "2026-03-24T10:00:00",
    "completed_at": "2026-03-24T10:04:08",
    "agent_duration_s": 200.0,
    "review_duration_s": 30.0,
    "lint_duration_s": 10.0,
    "merge_duration_s": 8.0,
    "num_turns": 5,
    "error_message": None,
    "complexity": "medium",
    "repo_id": "default",
}

SAMPLE_TASK_2 = {
    "id": "t2-uuid-9876-5432-fedcba",
    "title": "Fix auth bug",
    "description": "Fix authentication bypass in middleware",
    "state": "error",
    "files": ["forge/api/security/dependencies.py"],
    "assigned_agent": None,
    "cost_usd": 0.003,
    "agent_cost_usd": 0.003,
    "review_cost_usd": 0.0,
    "retry_count": 2,
    "input_tokens": 5000,
    "output_tokens": 1200,
    "started_at": "2026-03-24T10:05:00",
    "completed_at": "2026-03-24T10:06:30",
    "agent_duration_s": 80.0,
    "review_duration_s": 0.0,
    "lint_duration_s": 5.0,
    "merge_duration_s": 0.0,
    "num_turns": 3,
    "error_message": "Lint check failed after 3 retries",
    "complexity": "high",
    "repo_id": "default",
}

SAMPLE_PIPELINE: dict = {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "description": "Add input validation to API",
    "status": "done",
    "created_at": "2026-03-24T10:00:00",
    "completed_at": "2026-03-24T10:15:00",
    "duration_s": 900.0,
    "total_cost_usd": 0.453,
    "planner_cost_usd": 0.05,
    "total_input_tokens": 20000,
    "total_output_tokens": 5200,
    "tasks_succeeded": 1,
    "tasks_failed": 1,
    "total_retries": 2,
    "base_branch": "main",
    "branch_name": "forge/add-validation",
    "pr_url": "https://github.com/example/repo/pull/42",
    "model_strategy": "auto",
    "project_name": "forge-orchestrator",
    "tasks": [SAMPLE_TASK_1, SAMPLE_TASK_2],
}

EMPTY_PIPELINE: dict = {
    "id": "empty-0000-0000-0000-000000000000",
    "description": "Empty pipeline",
    "status": "planned",
    "created_at": "2026-03-24T10:00:00",
    "completed_at": None,
    "duration_s": 0.0,
    "total_cost_usd": 0.0,
    "planner_cost_usd": 0.0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "tasks_succeeded": 0,
    "tasks_failed": 0,
    "total_retries": 0,
    "base_branch": "main",
    "branch_name": None,
    "pr_url": None,
    "model_strategy": "auto",
    "project_name": None,
    "tasks": [],
}


class TestFormatJson:
    def test_format_json(self):
        result = format_json(SAMPLE_PIPELINE)
        parsed = json.loads(result)
        assert parsed["id"] == SAMPLE_PIPELINE["id"]
        assert parsed["description"] == SAMPLE_PIPELINE["description"]
        assert isinstance(parsed["tasks"], list)
        assert len(parsed["tasks"]) == 2
        assert parsed["tasks"][0]["id"] == SAMPLE_TASK_1["id"]

    def test_format_json_empty_tasks(self):
        result = format_json(EMPTY_PIPELINE)
        parsed = json.loads(result)
        assert parsed["id"] == EMPTY_PIPELINE["id"]
        assert parsed["tasks"] == []


class TestFormatMarkdown:
    def test_format_markdown(self):
        result = format_markdown(SAMPLE_PIPELINE)

        # H1 with description
        assert "# Add input validation to API" in result

        # Summary stats
        assert "**Status:** done" in result
        assert "**Duration:** 15m 0s" in result
        assert "**Total Cost:** $0.45" in result
        assert "20,000 in" in result
        assert "5,200 out" in result
        assert "1 succeeded" in result
        assert "1 failed" in result
        assert "**Retries:** 2" in result
        assert "**Branch:** forge/add-validation" in result
        assert "**PR:** https://github.com/example/repo/pull/42" in result

        # Table headers
        assert "| Task ID |" in result
        assert "| Title |" in result
        assert "| Status |" in result

        # Task rows
        assert "t1-uuid-" in result
        assert "Add validators" in result
        assert "agent-0" in result

        # Footer
        assert "Generated at" in result

    def test_format_markdown_empty_tasks(self):
        result = format_markdown(EMPTY_PIPELINE)
        assert "# Empty pipeline" in result
        assert "No tasks." in result
        # Should still have summary
        assert "## Summary" in result

    def test_format_markdown_cost_formatting(self):
        """Small costs use 4 decimal places."""
        result = format_markdown(SAMPLE_PIPELINE)
        # Task 2 has cost 0.003 which is < 0.01
        assert "$0.0030" in result


class TestFormatCsv:
    def test_format_csv(self):
        result = format_csv(SAMPLE_PIPELINE)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)

        # Header + 2 data rows
        assert len(rows) == 3

        headers = rows[0]
        assert headers == [
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

        # First task row
        row1 = rows[1]
        assert row1[0] == SAMPLE_TASK_1["id"]
        assert row1[1] == "Add validators"
        assert row1[2] == "done"
        assert float(row1[3]) == 0.45
        assert float(row1[4]) == 248.0  # 200+30+10+8
        assert int(row1[5]) == 0
        assert int(row1[6]) == 2  # 2 files
        assert row1[7] == "agent-0"
        assert row1[8] == "medium"

        # Second task row - error_message present
        row2 = rows[2]
        assert row2[11] == "Lint check failed after 3 retries"

    def test_format_csv_empty_tasks(self):
        result = format_csv(EMPTY_PIPELINE)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # Header only
        assert len(rows) == 1
        assert rows[0][0] == "task_id"
