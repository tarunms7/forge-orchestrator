from __future__ import annotations

import json
from types import SimpleNamespace

from forge.config.settings import ForgeSettings
from forge.core.context import ProjectSnapshot
from forge.core.retrieval_context import (
    RetrievalDiagnostics,
    _diagnostics_from_evidence,
    _resolve_codegraph_dir,
    build_agent_context,
    build_multi_repo_planner_context,
    build_planner_context,
    derive_task_evidence,
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
        "evidence_files": [],
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
    assert event["evidence_files"] == []


def test_diagnostics_from_evidence_populates_evidence_files():
    """Test that _diagnostics_from_evidence extracts symbols, neighbors, reasons, rank, and focus_range."""
    data = {
        "confidence": 0.85,
        "matched_terms": ["test", "function"],
        "missed_terms": ["missing"],
        "files": [
            {
                "path": "forge/core/daemon_executor.py",
                "rank": 8.5,
                "reasons": ["seed-file", "graph-neighbor", "symbol-match"],
                "focus_range": [27, 61],
                "symbols": [
                    {"name": "ExecutorMixin", "line": 41},
                    {"name": "_execute_task", "line": 209},
                    {"name": "logger", "line": 15},
                    {"name": "TaskState", "line": 25},
                    {"name": "extra_symbol", "line": 100},  # 5th symbol - should be excluded
                ],
                "neighbors": [
                    {"kind": "imports", "path": "forge/core/models.py"},
                    {"kind": "imported_by", "path": "forge/api/routes.py"},
                    {
                        "kind": "co_changed",
                        "path": "forge/core/context.py",
                    },  # 3rd neighbor - should be excluded
                ],
            },
            {
                "path": "forge/config/settings.py",
                "rank": 2.1,
                "reasons": ["import_match"],
                "focus_range": [10, 346],
                "symbols": [{"name": "ForgeSettings", "line": 13}],
                "neighbors": [{"kind": "imports", "path": "forge/api/app.py"}],
            },
        ],
    }

    diag = _diagnostics_from_evidence("agent", data)

    assert diag.stage == "agent"
    assert diag.used_retrieval is True
    assert diag.confidence == 0.85
    assert diag.matched_terms == ["test", "function"]
    assert diag.missed_terms == ["missing"]
    assert diag.top_files == ["forge/core/daemon_executor.py", "forge/config/settings.py"]

    # Check evidence_files structure
    assert len(diag.evidence_files) == 2

    # First file
    file1 = diag.evidence_files[0]
    assert file1["path"] == "forge/core/daemon_executor.py"
    assert file1["rank"] == 8.5
    assert file1["reasons"] == ["seed-file", "graph-neighbor", "symbol-match"]  # First 3
    assert file1["focus_range"] == [27, 61]

    # Check symbols (first 4)
    assert len(file1["symbols"]) == 4
    assert file1["symbols"][0] == {"name": "ExecutorMixin", "line": 41}
    assert file1["symbols"][1] == {"name": "_execute_task", "line": 209}
    assert file1["symbols"][2] == {"name": "logger", "line": 15}
    assert file1["symbols"][3] == {"name": "TaskState", "line": 25}

    # Check neighbors (first 2)
    assert len(file1["neighbors"]) == 2
    assert file1["neighbors"][0] == {"kind": "imports", "path": "forge/core/models.py"}
    assert file1["neighbors"][1] == {"kind": "imported_by", "path": "forge/api/routes.py"}

    # Second file
    file2 = diag.evidence_files[1]
    assert file2["path"] == "forge/config/settings.py"
    assert file2["rank"] == 2.1
    assert file2["reasons"] == ["import_match"]
    assert file2["focus_range"] == [10, 346]
    assert len(file2["symbols"]) == 1
    assert file2["symbols"][0] == {"name": "ForgeSettings", "line": 13}
    assert len(file2["neighbors"]) == 1
    assert file2["neighbors"][0] == {"kind": "imports", "path": "forge/api/app.py"}


def test_diagnostics_to_event_dict_includes_evidence_files():
    """Test that to_event_dict() serializes the evidence_files field."""
    evidence_files = [
        {
            "path": "test.py",
            "reasons": ["test-reason"],
            "symbols": [{"name": "TestClass", "line": 10}],
            "neighbors": [{"kind": "imports", "path": "other.py"}],
            "rank": 1.0,
            "focus_range": [5, 20],
        }
    ]

    diag = RetrievalDiagnostics(
        stage="reviewer",
        used_retrieval=True,
        confidence=0.90,
        top_files=["test.py"],
        matched_terms=["test"],
        missed_terms=["missing"],
        evidence_files=evidence_files,
    )

    event = diag.to_event_dict()

    assert "evidence_files" in event
    assert event["evidence_files"] == evidence_files
    assert len(event["evidence_files"]) == 1
    assert event["evidence_files"][0]["path"] == "test.py"
    assert event["evidence_files"][0]["symbols"][0]["name"] == "TestClass"


def test_evidence_files_defaults_to_empty_when_no_retrieval():
    """Test that evidence_files defaults to empty list when used_retrieval=False."""
    diag = RetrievalDiagnostics(stage="planner", used_retrieval=False)

    assert diag.evidence_files == []

    event = diag.to_event_dict()
    assert event["evidence_files"] == []


# --- derive_task_evidence tests ---


def _sample_planner_diagnostics() -> dict:
    """Build a realistic planner diagnostics dict for derive_task_evidence tests."""
    return {
        "stage": "planner",
        "used_retrieval": True,
        "confidence": 0.91,
        "matched_terms": ["auth", "login"],
        "missed_terms": ["obscure"],
        "evidence_files": [
            {
                "path": "forge/core/auth.py",
                "reasons": ["import_match", "symbol_hit"],
                "symbols": [{"name": "authenticate", "line": 15}],
                "neighbors": [{"kind": "imports", "path": "forge/core/models.py"}],
                "rank": 1,
                "focus_range": [10, 50],
            },
            {
                "path": "forge/api/routes.py",
                "reasons": ["text_match"],
                "symbols": [{"name": "login_route", "line": 30}],
                "neighbors": [{"kind": "imports", "path": "forge/core/auth.py"}],
                "rank": 2,
                "focus_range": None,
            },
            {
                "path": "forge/tui/app.py",
                "reasons": ["co_changed"],
                "symbols": [],
                "neighbors": [],
                "rank": 5,
                "focus_range": None,
            },
        ],
    }


def test_derive_task_evidence_matches_exact_path():
    diag = _sample_planner_diagnostics()
    result = derive_task_evidence(diag, ["forge/core/auth.py"])

    assert result["used_retrieval"] is True
    assert len(result["evidence_files"]) >= 1
    paths = [ef["path"] for ef in result["evidence_files"]]
    assert "forge/core/auth.py" in paths
    assert result["confidence"] == 0.91
    assert result["matched_terms"] == ["auth", "login"]
    assert result["missed_terms"] == ["obscure"]


def test_derive_task_evidence_matches_neighbor():
    diag = _sample_planner_diagnostics()
    # forge/api/routes.py has neighbor forge/core/auth.py — task touches auth.py
    result = derive_task_evidence(diag, ["forge/core/auth.py"])

    paths = [ef["path"] for ef in result["evidence_files"]]
    # routes.py should be included because its neighbor (auth.py) is a task file
    assert "forge/api/routes.py" in paths
    assert result["used_retrieval"] is True


def test_derive_task_evidence_matches_same_directory():
    diag = _sample_planner_diagnostics()
    # forge/core/auth.py is in forge/core/ — use a task file in the same directory
    # that is NOT referenced as a neighbor (models.py IS a neighbor of auth.py,
    # so it would match via Check 2 instead of Check 3).
    result = derive_task_evidence(diag, ["forge/core/utils.py"])

    paths = [ef["path"] for ef in result["evidence_files"]]
    assert "forge/core/auth.py" in paths
    assert result["used_retrieval"] is True
    assert "shares directory" in result["rationale"]


def test_derive_task_evidence_no_match():
    diag = _sample_planner_diagnostics()
    result = derive_task_evidence(diag, ["forge/unrelated/something.py"])

    assert result["used_retrieval"] is False
    assert result["evidence_files"] == []
    assert result["rationale"] == ""
    assert result["confidence"] == 0.91  # still copied


def test_derive_task_evidence_empty_diagnostics():
    # None input
    result = derive_task_evidence(None, ["forge/core/auth.py"])
    assert result["used_retrieval"] is False
    assert result["evidence_files"] == []
    assert result["rationale"] == ""

    # Empty dict input
    result = derive_task_evidence({}, ["forge/core/auth.py"])
    assert result["used_retrieval"] is False
    assert result["evidence_files"] == []

    # Empty task_files
    result = derive_task_evidence(_sample_planner_diagnostics(), [])
    assert result["used_retrieval"] is False
    assert result["evidence_files"] == []


def test_derive_task_evidence_generates_rationale():
    diag = _sample_planner_diagnostics()
    result = derive_task_evidence(diag, ["forge/core/auth.py"])

    rationale = result["rationale"]
    assert rationale  # non-empty
    assert "auth.py" in rationale
    assert "import_match" in rationale or "symbol_hit" in rationale
