"""Tests for AgentCollaborationBroker and CompletionRecord."""

from forge.agents.collaboration import (
    AgentCollaborationBroker,
    CompletionRecord,
    _extract_decisions,
)

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


def _make_broker_with_completion(
    pipeline_id: str = "pipe-1",
    task_id: str = "task-1",
    diff: str = SAMPLE_DIFF,
    agent_summary: str = "- Added asyncio import\n- Updated utils",
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


def test_register_and_retrieve_completion():
    broker = _make_broker_with_completion()
    record = broker.get_completion("pipe-1", "task-1")
    assert record is not None
    assert isinstance(record, CompletionRecord)
    assert record.task_id == "task-1"
    assert record.files_changed == ["forge/core/engine.py", "forge/core/utils.py"]
    assert record.implementation_summary == "Added asyncio and updated utils"
    assert record.completed_at  # ISO timestamp is populated
    assert len(record.key_decisions) == 2
    assert "Added asyncio import" in record.key_decisions
    assert "Updated utils" in record.key_decisions


def test_get_completion_returns_none_for_unknown_task():
    broker = _make_broker_with_completion()
    assert broker.get_completion("pipe-1", "nonexistent") is None
    assert broker.get_completion("nonexistent", "task-1") is None


def test_get_diff_for_file_extracts_correct_section():
    broker = _make_broker_with_completion()
    section = broker.get_diff_for_file("pipe-1", "task-1", "forge/core/engine.py")
    assert section is not None
    assert "forge/core/engine.py" in section
    assert "+import asyncio" in section
    # Should NOT contain the utils section
    assert "forge/core/utils.py" not in section


def test_get_diff_for_file_returns_none_for_unknown_file():
    broker = _make_broker_with_completion()
    assert broker.get_diff_for_file("pipe-1", "task-1", "nonexistent.py") is None


def test_get_diff_for_file_returns_none_for_unknown_task():
    broker = AgentCollaborationBroker()
    assert broker.get_diff_for_file("pipe-1", "task-1", "any.py") is None


def test_cleanup_removes_all_pipeline_data():
    broker = _make_broker_with_completion()
    broker.cleanup("pipe-1")
    assert broker.get_completion("pipe-1", "task-1") is None
    assert broker.get_all_completions("pipe-1") == {}


def test_cleanup_noop_for_unknown_pipeline():
    broker = AgentCollaborationBroker()
    broker.cleanup("nonexistent")  # Should not raise


def test_get_all_completions():
    broker = _make_broker_with_completion()
    broker.register_completion(
        "pipe-1",
        "task-2",
        files_changed=["README.md"],
        implementation_summary="Updated docs",
        agent_summary="* Rewrote the readme",
        diff="diff --git a/README.md b/README.md\n+new content",
    )
    all_completions = broker.get_all_completions("pipe-1")
    assert len(all_completions) == 2
    assert "task-1" in all_completions
    assert "task-2" in all_completions


def test_get_all_completions_empty_for_unknown_pipeline():
    broker = AgentCollaborationBroker()
    assert broker.get_all_completions("nonexistent") == {}


def test_multiple_pipelines_are_isolated():
    broker = AgentCollaborationBroker()
    broker.register_completion(
        "pipe-A",
        "task-1",
        files_changed=["a.py"],
        implementation_summary="Pipeline A work",
        agent_summary="- Did A stuff",
        diff="diff --git a/a.py b/a.py\n+a",
    )
    broker.register_completion(
        "pipe-B",
        "task-1",
        files_changed=["b.py"],
        implementation_summary="Pipeline B work",
        agent_summary="- Did B stuff",
        diff="diff --git a/b.py b/b.py\n+b",
    )

    a_record = broker.get_completion("pipe-A", "task-1")
    b_record = broker.get_completion("pipe-B", "task-1")
    assert a_record is not None
    assert b_record is not None
    assert a_record.files_changed == ["a.py"]
    assert b_record.files_changed == ["b.py"]

    # Cleanup one pipeline doesn't affect the other
    broker.cleanup("pipe-A")
    assert broker.get_completion("pipe-A", "task-1") is None
    assert broker.get_completion("pipe-B", "task-1") is not None


def test_extract_decisions_bullet_dash():
    decisions = _extract_decisions("Summary:\n- First decision\n- Second decision")
    assert decisions == ["First decision", "Second decision"]


def test_extract_decisions_bullet_star():
    decisions = _extract_decisions("* Star bullet one\n* Star bullet two")
    assert decisions == ["Star bullet one", "Star bullet two"]


def test_extract_decisions_numbered():
    decisions = _extract_decisions("1. First item\n2. Second item\n3. Third item")
    assert decisions == ["First item", "Second item", "Third item"]


def test_extract_decisions_mixed_formats():
    text = "Key decisions:\n- Bullet one\n* Star two\n1. Numbered three"
    decisions = _extract_decisions(text)
    assert "Bullet one" in decisions
    assert "Star two" in decisions
    assert "Numbered three" in decisions


def test_extract_decisions_caps_at_10():
    lines = "\n".join(f"- Decision {i}" for i in range(20))
    decisions = _extract_decisions(lines)
    assert len(decisions) == 10


def test_extract_decisions_truncates_long_items():
    long_text = "- " + "x" * 300
    decisions = _extract_decisions(long_text)
    assert len(decisions) == 1
    assert len(decisions[0]) == 200


def test_extract_decisions_empty_text():
    assert _extract_decisions("") == []
    assert _extract_decisions("No bullets here, just plain text.") == []


def test_extract_decisions_deduplicates():
    text = "- Same decision\n- Same decision\n* Same decision"
    decisions = _extract_decisions(text)
    assert decisions == ["Same decision"]
