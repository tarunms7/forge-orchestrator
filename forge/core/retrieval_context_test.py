from __future__ import annotations

import json
from types import SimpleNamespace

from forge.config.settings import ForgeSettings
from forge.core.context import ProjectSnapshot
from forge.core.retrieval_context import (
    RetrievalDiagnostics,
    _resolve_codegraph_dir,
    build_agent_context,
    build_multi_repo_planner_context,
    build_planner_context,
)


def _snapshot() -> ProjectSnapshot:
    return ProjectSnapshot(
        file_tree="forge/\n  core/\n",
        total_files=42,
        total_loc=1337,
        languages={".py": 40, ".ts": 2},
        module_index={"forge": "Main package"},
        git_branch="main",
    )


def test_resolve_codegraph_dir_prefers_explicit_setting(tmp_path):
    codegraph_dir = tmp_path / "codegraph"
    package_dir = codegraph_dir / "codegraph"
    package_dir.mkdir(parents=True)
    (package_dir / "cli.py").write_text("")
    (package_dir / "__init__.py").write_text("")

    resolved = _resolve_codegraph_dir(
        project_dir_hint=str(tmp_path / "project"),
        retrieval_tool_dir=str(codegraph_dir),
    )

    assert resolved == str(codegraph_dir.resolve())


def test_build_agent_context_falls_back_when_retrieval_disabled():
    settings = ForgeSettings(retrieval_enabled=False)
    snapshot = _snapshot()

    result, diag = build_agent_context(
        project_dir_hint="/tmp/project",
        repo_path="/tmp/project",
        snapshot=snapshot,
        settings=settings,
        task_files=["forge/core/daemon.py"],
        task_prompt="daemon planner",
    )

    assert result == snapshot.format_for_agent()
    assert isinstance(diag, RetrievalDiagnostics)
    assert diag.stage == "agent"
    assert diag.used_retrieval is False


def test_build_agent_context_renders_retrieval(monkeypatch, tmp_path):
    codegraph_dir = tmp_path / "codegraph"
    package_dir = codegraph_dir / "codegraph"
    package_dir.mkdir(parents=True)
    (package_dir / "cli.py").write_text("")
    (package_dir / "__init__.py").write_text("")

    payload = {
        "mode": "files",
        "confidence": 0.97,
        "files": [
            {
                "path": "forge/core/daemon_executor.py",
                "rank": 8.5,
                "reasons": ["seed-file", "graph-neighbor"],
                "focus_range": [209, 1676],
                "symbols": [
                    {"name": "ExecutorMixin", "line": 41},
                    {"name": "_execute_task", "line": 209},
                ],
                "neighbors": [
                    {"kind": "imports", "path": "forge/core/models.py"},
                ],
            }
        ],
    }

    def fake_run(cmd, cwd, capture_output, text, env, timeout, check):
        assert cwd == str(codegraph_dir)
        assert "--file" in cmd
        assert env["CODEGRAPH_CACHE_DIR"].endswith("/.forge/codegraph")
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("forge.core.retrieval_context.subprocess.run", fake_run)

    settings = ForgeSettings(retrieval_tool_dir=str(codegraph_dir))
    result, diag = build_agent_context(
        project_dir_hint=str(tmp_path / "project"),
        repo_path="/tmp/project",
        snapshot=_snapshot(),
        settings=settings,
        task_files=["forge/core/daemon_executor.py"],
        task_prompt="daemon executor",
        repo_label="default",
    )

    assert "## Task Retrieval" in result
    assert "`forge/core/daemon_executor.py`" in result
    assert "ExecutorMixin L41" in result
    assert "imports forge/core/models.py" in result
    assert "### File Tree" not in result
    assert isinstance(diag, RetrievalDiagnostics)
    assert diag.stage == "agent"
    assert diag.used_retrieval is True
    assert diag.confidence == 0.97
    assert diag.top_files == ["forge/core/daemon_executor.py"]


def test_build_multi_repo_planner_context_falls_back_without_retrieval():
    settings = ForgeSettings(retrieval_enabled=False)
    snapshots = {"backend": _snapshot()}
    repos = {"backend": SimpleNamespace(path="/tmp/backend")}

    result, diag = build_multi_repo_planner_context(
        project_dir_hint="/tmp/project",
        repos=repos,
        snapshots=snapshots,
        query="planner daemon",
        settings=settings,
    )

    assert "### Repo: backend (/tmp/backend)" in result
    assert "### File Tree" in result
    assert isinstance(diag, RetrievalDiagnostics)
    assert diag.stage == "planner"
    assert diag.used_retrieval is False


def test_build_planner_context_falls_back_when_confidence_is_low(monkeypatch, tmp_path):
    codegraph_dir = tmp_path / "codegraph"
    package_dir = codegraph_dir / "codegraph"
    package_dir.mkdir(parents=True)
    (package_dir / "cli.py").write_text("")
    (package_dir / "__init__.py").write_text("")

    payload = {
        "mode": "query",
        "confidence": 0.42,
        "files": [
            {
                "path": "forge/core/daemon.py",
                "rank": 5.1,
                "reasons": ["path-match"],
                "focus_range": [10, 20],
            }
        ],
    }

    def fake_run(cmd, cwd, capture_output, text, env, timeout, check):
        assert cwd == str(codegraph_dir)
        assert "--text" in cmd
        assert env["CODEGRAPH_CACHE_DIR"].endswith("/.forge/codegraph")
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("forge.core.retrieval_context.subprocess.run", fake_run)

    snapshot = _snapshot()
    settings = ForgeSettings(retrieval_tool_dir=str(codegraph_dir))
    result, diag = build_planner_context(
        project_dir_hint=str(tmp_path / "project"),
        repo_path="/tmp/project",
        snapshot=snapshot,
        query="planner behavior",
        settings=settings,
    )

    assert result == snapshot.format_for_planner()
    assert isinstance(diag, RetrievalDiagnostics)
    assert diag.stage == "planner"
    assert diag.used_retrieval is False


def test_build_planner_context_uses_retrieval_when_confidence_is_high(monkeypatch, tmp_path):
    codegraph_dir = tmp_path / "codegraph"
    package_dir = codegraph_dir / "codegraph"
    package_dir.mkdir(parents=True)
    (package_dir / "cli.py").write_text("")
    (package_dir / "__init__.py").write_text("")

    payload = {
        "mode": "query",
        "confidence": 0.91,
        "matched_terms": ["planner", "retry"],
        "files": [
            {
                "path": "forge/core/planning/unified_planner.py",
                "rank": 7.2,
                "reasons": ["path-match", "symbol-match"],
                "focus_range": [50, 90],
                "symbols": [{"name": "UnifiedPlanner", "line": 55}],
            }
        ],
    }

    def fake_run(cmd, cwd, capture_output, text, env, timeout, check):
        assert cwd == str(codegraph_dir)
        assert "--text" in cmd
        assert env["CODEGRAPH_CACHE_DIR"].endswith("/.forge/codegraph")
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("forge.core.retrieval_context.subprocess.run", fake_run)

    settings = ForgeSettings(retrieval_tool_dir=str(codegraph_dir))
    result, diag = build_planner_context(
        project_dir_hint=str(tmp_path / "project"),
        repo_path="/tmp/project",
        snapshot=_snapshot(),
        query="planner retry",
        settings=settings,
    )

    assert "## Planner Retrieval" in result
    assert "`forge/core/planning/unified_planner.py`" in result
    assert "### File Tree" not in result
    assert isinstance(diag, RetrievalDiagnostics)
    assert diag.stage == "planner"
    assert diag.used_retrieval is True
    assert diag.confidence == 0.91
    assert diag.matched_terms == ["planner", "retry"]


def test_retrieval_diagnostics_to_event_dict():
    diag = RetrievalDiagnostics(
        stage="agent",
        used_retrieval=True,
        confidence=0.85,
        top_files=["a.py", "b.py"],
        matched_terms=["foo"],
        missed_terms=["bar"],
    )
    event = diag.to_event_dict()
    assert event == {
        "stage": "agent",
        "used_retrieval": True,
        "confidence": 0.85,
        "top_files": ["a.py", "b.py"],
        "matched_terms": ["foo"],
        "missed_terms": ["bar"],
    }


def test_retrieval_diagnostics_to_event_dict_fallback():
    diag = RetrievalDiagnostics(stage="reviewer", used_retrieval=False)
    event = diag.to_event_dict()
    assert event["stage"] == "reviewer"
    assert event["used_retrieval"] is False
    assert event["confidence"] is None
    assert event["top_files"] == []
    assert event["matched_terms"] == []
    assert event["missed_terms"] == []
