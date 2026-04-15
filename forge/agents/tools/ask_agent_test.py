"""Tests for ask_prior_agent query tool."""

from forge.agents.collaboration import AgentCollaborationBroker
from forge.agents.tools.ask_agent import ask_prior_agent

SAMPLE_DIFF = """\
diff --git a/forge/core/engine.py b/forge/core/engine.py
index abc1234..def5678 100644
--- a/forge/core/engine.py
+++ b/forge/core/engine.py
@@ -10,6 +10,7 @@
 import logging
+import asyncio
diff --git a/forge/core/utils.py b/forge/core/utils.py
index 1111111..2222222 100644
--- a/forge/core/utils.py
+++ b/forge/core/utils.py
@@ -1,3 +1,5 @@
+# New utility helpers
 def existing():
     pass
"""

AGENT_SUMMARY = """\
Implemented the engine and utils changes:
- Added asyncio import to engine.py
- Created new utility helpers in utils.py
* Kept backward compatibility
"""


def _make_broker(
    pipeline_id: str = "pipe-1",
    task_id: str = "task-1",
    diff: str = SAMPLE_DIFF,
    agent_summary: str = AGENT_SUMMARY,
) -> AgentCollaborationBroker:
    broker = AgentCollaborationBroker()
    broker.register_completion(
        pipeline_id,
        task_id,
        files_changed=["forge/core/engine.py", "forge/core/utils.py"],
        implementation_summary="Added asyncio and updated utils",
        agent_summary=agent_summary,
        diff=diff,
    )
    return broker


def test_valid_task_returns_formatted_context():
    broker = _make_broker()
    result = ask_prior_agent(broker, "pipe-1", "task-1", "What did you do?")
    assert result.startswith("## Context from task-1")
    assert "Added asyncio and updated utils" in result
    assert "Added asyncio import to engine.py" in result


def test_unknown_task_returns_not_found():
    broker = _make_broker()
    result = ask_prior_agent(broker, "pipe-1", "task-99", "What happened?")
    assert "## Context from task-99" in result
    assert "No completion record found" in result


def test_unknown_pipeline_returns_not_found():
    broker = _make_broker()
    result = ask_prior_agent(broker, "missing-pipe", "task-1", "What happened?")
    assert "No completion record found" in result


def test_question_with_file_path_returns_targeted_diff():
    broker = _make_broker()
    result = ask_prior_agent(broker, "pipe-1", "task-1", "What changed in forge/core/engine.py?")
    assert "## Context from task-1" in result
    assert "forge/core/engine.py" in result
    assert "+import asyncio" in result
    # Should NOT include utils diff
    assert "utility helpers" not in result


def test_question_with_simple_filename_returns_diff():
    broker = _make_broker()
    result = ask_prior_agent(broker, "pipe-1", "task-1", "Show me utils.py changes")
    assert "## Context from task-1" in result
    assert "forge/core/utils.py" in result
    assert "utility helpers" in result


def test_question_with_missing_file_shows_no_diff():
    broker = _make_broker()
    result = ask_prior_agent(broker, "pipe-1", "task-1", "What changed in nonexistent.py?")
    assert "No diff found for this file" in result


def test_response_capped_at_4000_chars():
    huge_diff = "diff --git a/big.py b/big.py\n" + "+" + "x" * 10000 + "\n"
    broker = _make_broker(diff=huge_diff)
    result = ask_prior_agent(broker, "pipe-1", "task-1", "Tell me everything")
    assert len(result) <= 4000


def test_empty_question_returns_full_summary():
    broker = _make_broker()
    result = ask_prior_agent(broker, "pipe-1", "task-1", "")
    assert result.startswith("## Context from task-1")
    assert "Added asyncio and updated utils" in result
    assert "Added asyncio import to engine.py" in result
    # The diff should be present (truncated preview)
    assert "diff" in result.lower()


def test_diff_preview_truncated_to_2000_chars():
    """Summary mode should only include first 2000 chars of the diff."""
    long_diff = "diff --git a/f.py b/f.py\n" + "+" + "a" * 5000 + "\n"
    broker = _make_broker(diff=long_diff)
    result = ask_prior_agent(broker, "pipe-1", "task-1", "What did you do?")
    # The full 5000-char diff line should be truncated
    assert "a" * 2001 not in result
