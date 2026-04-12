"""Retrieval-backed project context for planner, agent, and reviewer prompts."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from forge.core.context import (
    ProjectSnapshot,
    _format_languages,
    _format_module_index,
    format_multi_repo_snapshot,
)
from forge.core.paths import project_artifact_dir

if TYPE_CHECKING:
    from forge.config.settings import ForgeSettings
    from forge.core.models import RepoConfig

logger = logging.getLogger("forge.retrieval")


@dataclass
class RetrievalDiagnostics:
    """Diagnostics payload emitted when retrieval context is built."""

    stage: str  # 'planner', 'agent', or 'reviewer'
    used_retrieval: bool
    confidence: float | None = None
    top_files: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)
    missed_terms: list[str] = field(default_factory=list)
    evidence_files: list[dict] = field(default_factory=list)

    def to_event_dict(self) -> dict:
        return {
            "stage": self.stage,
            "used_retrieval": self.used_retrieval,
            "confidence": self.confidence,
            "top_files": self.top_files,
            "matched_terms": self.matched_terms,
            "missed_terms": self.missed_terms,
            "evidence_files": self.evidence_files,
        }


def _diagnostics_from_evidence(stage: str, data: dict) -> RetrievalDiagnostics:
    """Build a populated RetrievalDiagnostics from codegraph evidence."""
    files = data.get("files") or []

    # Extract evidence files detail (up to 10 files)
    evidence_files = []
    for file_entry in files[:10]:
        evidence_file = {
            "path": file_entry.get("path", ""),
            "reasons": (file_entry.get("reasons") or [])[:3],
            "symbols": [],
            "neighbors": [],
            "rank": file_entry.get("rank"),
            "focus_range": file_entry.get("focus_range"),
        }

        # Extract symbols (up to 4)
        for symbol in (file_entry.get("symbols") or [])[:4]:
            evidence_file["symbols"].append({
                "name": symbol.get("name", ""),
                "line": symbol.get("line"),
            })

        # Extract neighbors (up to 2)
        for neighbor in (file_entry.get("neighbors") or [])[:2]:
            evidence_file["neighbors"].append({
                "kind": neighbor.get("kind", ""),
                "path": neighbor.get("path", ""),
            })

        evidence_files.append(evidence_file)

    return RetrievalDiagnostics(
        stage=stage,
        used_retrieval=True,
        confidence=data.get("confidence"),
        top_files=[f.get("path", "") for f in files[:5]],
        matched_terms=data.get("matched_terms") or [],
        missed_terms=data.get("missed_terms") or [],
        evidence_files=evidence_files,
    )


def build_planner_context(
    *,
    project_dir_hint: str,
    repo_path: str,
    snapshot: ProjectSnapshot | None,
    query: str,
    settings: ForgeSettings,
    repo_label: str | None = None,
) -> tuple[str, RetrievalDiagnostics]:
    """Build planner context using retrieval when available."""
    fallback = snapshot.format_for_planner() if snapshot else ""
    evidence = _fetch_evidence(
        project_dir_hint=project_dir_hint,
        repo_path=repo_path,
        settings=settings,
        query=query,
    )
    if evidence is None or not _planner_evidence_is_usable(evidence, settings=settings):
        return fallback, RetrievalDiagnostics(stage="planner", used_retrieval=False)
    parts = [_compact_snapshot(snapshot, repo_label=repo_label, include_branch=True)]
    rendered = _render_evidence(evidence, heading="Planner Retrieval")
    if rendered:
        parts.append(rendered)
    context = "\n\n".join(part for part in parts if part)
    return context, _diagnostics_from_evidence("planner", evidence)


def build_multi_repo_planner_context(
    *,
    project_dir_hint: str,
    repos: dict[str, RepoConfig],
    snapshots: dict[str, ProjectSnapshot],
    query: str,
    settings: ForgeSettings,
) -> tuple[str, RetrievalDiagnostics]:
    """Build planner context for multi-repo workspaces."""
    rendered_sections: list[str] = []
    found_retrieval = False
    first_confidence: float | None = None
    all_top_files: list[str] = []
    all_matched: list[str] = []
    all_missed: list[str] = []
    all_evidence_files: list[dict] = []

    for repo_id in sorted(repos.keys()):
        repo = repos[repo_id]
        snapshot = snapshots.get(repo_id)
        evidence = _fetch_evidence(
            project_dir_hint=project_dir_hint,
            repo_path=repo.path,
            settings=settings,
            query=query,
        )
        if evidence is None or not _planner_evidence_is_usable(evidence, settings=settings):
            continue
        found_retrieval = True
        if first_confidence is None:
            first_confidence = evidence.get("confidence")
        files = evidence.get("files") or []
        all_top_files.extend(f.get("path", "") for f in files[:5])
        all_matched.extend(evidence.get("matched_terms") or [])
        all_missed.extend(evidence.get("missed_terms") or [])

        # Extract evidence files detail for this repo (up to 10 files)
        for file_entry in files[:10]:
            evidence_file = {
                "path": file_entry.get("path", ""),
                "reasons": (file_entry.get("reasons") or [])[:3],
                "symbols": [],
                "neighbors": [],
                "rank": file_entry.get("rank"),
                "focus_range": file_entry.get("focus_range"),
            }

            # Extract symbols (up to 4)
            for symbol in (file_entry.get("symbols") or [])[:4]:
                evidence_file["symbols"].append({
                    "name": symbol.get("name", ""),
                    "line": symbol.get("line"),
                })

            # Extract neighbors (up to 2)
            for neighbor in (file_entry.get("neighbors") or [])[:2]:
                evidence_file["neighbors"].append({
                    "kind": neighbor.get("kind", ""),
                    "path": neighbor.get("path", ""),
                })

            all_evidence_files.append(evidence_file)

        section_parts = [
            f"### Repo: {repo_id} ({repo.path})",
            _compact_snapshot(snapshot, repo_label=repo_id, include_branch=True),
        ]
        rendered = _render_evidence(evidence, heading="Planner Retrieval")
        if rendered:
            section_parts.append(rendered)
        rendered_sections.append("\n\n".join(part for part in section_parts if part))

    if found_retrieval:
        diag = RetrievalDiagnostics(
            stage="planner",
            used_retrieval=True,
            confidence=first_confidence,
            top_files=all_top_files[:5],
            matched_terms=list(dict.fromkeys(all_matched)),
            missed_terms=list(dict.fromkeys(all_missed)),
            evidence_files=all_evidence_files[:10],
        )
        return "\n\n".join(rendered_sections), diag

    return (
        format_multi_repo_snapshot(snapshots, repos),
        RetrievalDiagnostics(stage="planner", used_retrieval=False),
    )


def build_agent_context(
    *,
    project_dir_hint: str,
    repo_path: str,
    snapshot: ProjectSnapshot | None,
    settings: ForgeSettings,
    task_files: list[str] | None = None,
    task_prompt: str = "",
    repo_label: str | None = None,
) -> tuple[str, RetrievalDiagnostics]:
    """Build agent context using file-seeded retrieval when possible."""
    fallback = snapshot.format_for_agent() if snapshot else ""
    evidence = _fetch_evidence(
        project_dir_hint=project_dir_hint,
        repo_path=repo_path,
        settings=settings,
        files=task_files or None,
        query=task_prompt if not task_files else None,
    )
    if evidence is None:
        return fallback, RetrievalDiagnostics(stage="agent", used_retrieval=False)
    parts = [_compact_snapshot(snapshot, repo_label=repo_label, include_branch=True)]
    rendered = _render_evidence(evidence, heading="Task Retrieval")
    if rendered:
        parts.append(rendered)
    context = "\n\n".join(part for part in parts if part)
    return context, _diagnostics_from_evidence("agent", evidence)


def build_reviewer_context(
    *,
    project_dir_hint: str,
    repo_path: str,
    snapshot: ProjectSnapshot | None,
    settings: ForgeSettings,
    task_files: list[str] | None = None,
    task_prompt: str = "",
    repo_label: str | None = None,
) -> tuple[str, RetrievalDiagnostics]:
    """Build reviewer context focused on changed files and nearby code."""
    fallback = snapshot.format_for_reviewer() if snapshot else ""
    evidence = _fetch_evidence(
        project_dir_hint=project_dir_hint,
        repo_path=repo_path,
        settings=settings,
        files=task_files or None,
        query=task_prompt if not task_files else None,
    )
    if evidence is None:
        return fallback, RetrievalDiagnostics(stage="reviewer", used_retrieval=False)
    parts = [_compact_snapshot(snapshot, repo_label=repo_label, include_branch=False)]
    rendered = _render_evidence(evidence, heading="Review Retrieval")
    if rendered:
        parts.append(rendered)
    context = "\n\n".join(part for part in parts if part)
    return context, _diagnostics_from_evidence("reviewer", evidence)


def _fetch_evidence(
    *,
    project_dir_hint: str,
    repo_path: str,
    settings: ForgeSettings,
    query: str | None = None,
    files: list[str] | None = None,
) -> dict | None:
    """Fetch structured retrieval evidence from the local codegraph checkout."""
    if not getattr(settings, "retrieval_enabled", False):
        return None

    codegraph_dir = _resolve_codegraph_dir(
        project_dir_hint=project_dir_hint,
        retrieval_tool_dir=getattr(settings, "retrieval_tool_dir", ""),
    )
    if codegraph_dir is None:
        return None

    if bool(query) == bool(files):
        return None

    cli_args = [
        "evidence",
        repo_path,
        "--limit",
        str(settings.retrieval_max_files),
        "--symbols",
        str(settings.retrieval_max_symbols),
        "--neighbors",
        str(settings.retrieval_max_neighbors),
    ]
    if query:
        cli_args.extend(["--text", query])
    else:
        for path in files or []:
            cli_args.extend(["--file", path])

    env = os.environ.copy()
    env["CODEGRAPH_CACHE_DIR"] = _codegraph_cache_dir(repo_path)

    uv_bin = shutil.which("uv")
    if uv_bin:
        cmd = [uv_bin, "run", "codegraph", *cli_args]
    else:
        cmd = [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from codegraph.cli import main; "
                "sys.argv = ['codegraph', *sys.argv[1:]]; "
                "main()"
            ),
            *cli_args,
        ]

    try:
        result = subprocess.run(
            cmd,
            cwd=codegraph_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=settings.retrieval_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Codegraph retrieval failed for %s: %s", repo_path, exc)
        return None

    if result.returncode != 0:
        logger.warning(
            "Codegraph retrieval returned %s for %s: %s",
            result.returncode,
            repo_path,
            result.stderr.strip(),
        )
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("Codegraph retrieval returned invalid JSON for %s", repo_path)
        return None

    if not isinstance(data, dict):
        return None
    if not data.get("files"):
        return None
    return data


def _codegraph_cache_dir(repo_path: str) -> str:
    """Store Forge-triggered codegraph cache under the repo's .forge directory."""
    return project_artifact_dir(repo_path, "codegraph")


def _planner_evidence_is_usable(data: dict, *, settings: ForgeSettings) -> bool:
    """Use retrieval for planning only when the result looks strong enough."""
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)):
        return False
    return confidence >= settings.retrieval_planner_min_confidence


def _resolve_codegraph_dir(
    *,
    project_dir_hint: str,
    retrieval_tool_dir: str = "",
) -> str | None:
    """Find a local codegraph checkout that Forge can shell out to."""
    candidates: list[Path] = []
    env_path = os.getenv("FORGE_CODEGRAPH_DIR", "").strip()
    if retrieval_tool_dir:
        candidates.append(Path(retrieval_tool_dir).expanduser())
    if env_path:
        candidates.append(Path(env_path).expanduser())

    project_dir = Path(project_dir_hint).resolve()
    candidates.append(project_dir / "codegraph")
    candidates.append(project_dir.parent / "codegraph")

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "codegraph" / "cli.py").is_file() and (
            resolved / "codegraph" / "__init__.py"
        ).is_file():
            return str(resolved)
    return None


def _compact_snapshot(
    snapshot: ProjectSnapshot | None,
    *,
    repo_label: str | None,
    include_branch: bool,
) -> str:
    """Render a low-token repo overview instead of the full file tree."""
    if snapshot is None:
        return ""

    sections = ["## Project Snapshot", ""]
    if repo_label:
        sections.append(f"**Repo:** {repo_label}")
    if include_branch and snapshot.git_branch:
        sections.append(f"**Branch:** {snapshot.git_branch}")
    sections.append(f"**Files:** {snapshot.total_files} | **LOC:** {snapshot.total_loc}")
    if snapshot.languages:
        sections.extend(["", "### Languages", _format_languages(snapshot.languages)])
    if snapshot.module_index:
        module_lines = _format_module_index(snapshot.module_index).splitlines()[:8]
        if len(snapshot.module_index) > 8:
            module_lines.append(f"- ... ({len(snapshot.module_index)} packages total)")
        sections.extend(["", "### Module Index", "\n".join(module_lines)])
    return "\n".join(sections)


def _render_evidence(data: dict, *, heading: str) -> str:
    """Render codegraph evidence into a concise prompt block."""
    files = data.get("files") or []
    if not files:
        return ""

    lines = [f"## {heading}", ""]
    confidence = data.get("confidence")
    if confidence is not None:
        lines.append(f"**Confidence:** {confidence}")
    matched = data.get("matched_terms") or []
    missed = data.get("missed_terms") or []
    if matched:
        lines.append(f"**Matched Terms:** {', '.join(matched)}")
    if missed:
        lines.append(f"**Missed Terms:** {', '.join(missed)}")
    lines.append("")
    lines.append("### Evidence Files")

    for item in files:
        path = item.get("path", "")
        reasons = ", ".join((item.get("reasons") or [])[:3])
        focus = item.get("focus_range")
        focus_text = ""
        if isinstance(focus, list) and len(focus) == 2:
            focus_text = f" | focus L{focus[0]}-L{focus[1]}"
        rank = item.get("rank")
        rank_text = f" | rank {rank}" if rank is not None else ""
        reason_text = f" | {reasons}" if reasons else ""
        lines.append(f"- `{path}`{rank_text}{focus_text}{reason_text}")

        symbols = item.get("symbols") or []
        if symbols:
            rendered_symbols = []
            for symbol in symbols[:4]:
                name = symbol.get("name", "")
                line = symbol.get("line")
                rendered_symbols.append(f"{name} L{line}" if line else name)
            lines.append(f"  symbols: {', '.join(rendered_symbols)}")

        neighbors = item.get("neighbors") or []
        if neighbors:
            rendered_neighbors = []
            for neighbor in neighbors[:2]:
                relation = neighbor.get("kind", "")
                neighbor_path = neighbor.get("path", "")
                rendered_neighbors.append(f"{relation} {neighbor_path}")
            lines.append(f"  nearby: {', '.join(rendered_neighbors)}")

    return "\n".join(lines)
