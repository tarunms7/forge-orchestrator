from __future__ import annotations

import json
from types import SimpleNamespace

from forge.config.settings import ForgeSettings
from forge.core.context import ProjectSnapshot
from forge.core.retrieval_context import (
    _resolve_codegraph_dir,
    build_agent_context,
    build_multi_repo_planner_context,
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

    result = build_agent_context(
        project_dir_hint="/tmp/project",
        repo_path="/tmp/project",
        snapshot=snapshot,
        settings=settings,
        task_files=["forge/core/daemon.py"],
        task_prompt="daemon planner",
    )

    assert result == snapshot.format_for_agent()


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

    def fake_run(cmd, cwd, capture_output, text, timeout, check):
        assert cwd == str(codegraph_dir)
        assert "--file" in cmd
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("forge.core.retrieval_context.subprocess.run", fake_run)

    settings = ForgeSettings(retrieval_tool_dir=str(codegraph_dir))
    result = build_agent_context(
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


def test_build_multi_repo_planner_context_falls_back_without_retrieval():
    settings = ForgeSettings(retrieval_enabled=False)
    snapshots = {"backend": _snapshot()}
    repos = {"backend": SimpleNamespace(path="/tmp/backend")}

    result = build_multi_repo_planner_context(
        project_dir_hint="/tmp/project",
        repos=repos,
        snapshots=snapshots,
        query="planner daemon",
        settings=settings,
    )

    assert "### Repo: backend (/tmp/backend)" in result
    assert "### File Tree" in result
