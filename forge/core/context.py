"""Project snapshot gathering for shared context across Claude sessions.

Gathers a rich project snapshot from a git repo so that every Claude session
(planner, agents, reviewer) starts with the same understanding of the project
without each independently scanning the codebase.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectSnapshot:
    """Immutable snapshot of a project's structure, size, and metadata.

    Produced once per pipeline run and injected into all Claude sessions
    to avoid redundant codebase scanning.
    """

    file_tree: str = ""
    total_files: int = 0
    total_loc: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    readme_excerpt: str = ""
    config_summary: str = ""
    module_index: dict[str, str] = field(default_factory=dict)
    recent_commits: str = ""
    git_branch: str = ""

    def format_for_planner(self) -> str:
        """Full project context for the planner session.

        Includes everything: file tree, stats, README excerpt, config,
        module index, recent commits, and branch info.
        """
        sections = [
            "## Project Snapshot",
            "",
            f"**Branch:** {self.git_branch}",
            f"**Files:** {self.total_files} | **LOC:** {self.total_loc}",
            "",
            "### Languages",
            _format_languages(self.languages),
            "",
            "### File Tree",
            self.file_tree,
            "",
        ]
        if self.readme_excerpt:
            sections += [
                "### README Excerpt",
                self.readme_excerpt,
                "",
            ]
        if self.config_summary:
            sections += [
                "### Config Summary",
                self.config_summary,
                "",
            ]
        if self.module_index:
            sections += [
                "### Module Index",
                _format_module_index(self.module_index),
                "",
            ]
        if self.recent_commits:
            sections += [
                "### Recent Commits",
                self.recent_commits,
                "",
            ]
        return "\n".join(sections)

    def format_for_agent(self) -> str:
        """Condensed context for agent sessions (no README)."""
        sections = [
            "## Project Snapshot",
            "",
            f"**Branch:** {self.git_branch}",
            f"**Files:** {self.total_files} | **LOC:** {self.total_loc}",
            "",
            "### File Tree",
            self.file_tree,
            "",
        ]
        if self.module_index:
            sections += [
                "### Module Index",
                _format_module_index(self.module_index),
                "",
            ]
        return "\n".join(sections)

    def format_for_reviewer(self) -> str:
        """Minimal context for reviewer sessions (tree + modules)."""
        sections = [
            "## Project Snapshot",
            "",
            "### File Tree",
            self.file_tree,
            "",
        ]
        if self.module_index:
            sections += [
                "### Module Index",
                _format_module_index(self.module_index),
                "",
            ]
        return "\n".join(sections)


def gather_project_snapshot(project_dir: str) -> ProjectSnapshot:
    """Gather a complete project snapshot from a git repository.

    All operations are local (git + filesystem), no LLM calls.
    Performance target: < 2 seconds for a 500-file repo.

    Args:
        project_dir: Absolute path to the git repository root.

    Returns:
        A populated ProjectSnapshot dataclass.
    """
    tracked_files = _get_tracked_files(project_dir)
    file_tree = _get_file_tree(tracked_files)
    languages = _count_languages(tracked_files)
    total_loc = _count_loc(project_dir, tracked_files)
    readme_excerpt = _read_readme(project_dir)
    config_summary = _read_config(project_dir)
    module_index = _build_module_index(project_dir, tracked_files)
    recent_commits = _get_recent_commits(project_dir)
    git_branch = _get_branch(project_dir)

    return ProjectSnapshot(
        file_tree=file_tree,
        total_files=len(tracked_files),
        total_loc=total_loc,
        languages=languages,
        readme_excerpt=readme_excerpt,
        config_summary=config_summary,
        module_index=module_index,
        recent_commits=recent_commits,
        git_branch=git_branch,
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _get_tracked_files(project_dir: str) -> list[str]:
    """Return list of git-tracked files (respects .gitignore).

    Uses ``git ls-files`` so only committed/staged files are included.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []
    files = [f for f in result.stdout.strip().split("\n") if f]
    return sorted(files)


def _get_file_tree(files: list[str]) -> str:
    """Build an indented file tree string from a sorted list of file paths.

    Produces output like::

        README.md
        src/
          __init__.py
          main.py
        tests/
          test_main.py
    """
    lines: list[str] = []
    seen_dirs: set[str] = set()

    for filepath in files:
        parts = filepath.split("/")
        # Emit directory prefixes we haven't seen yet
        for depth in range(1, len(parts)):
            dir_path = "/".join(parts[:depth])
            if dir_path not in seen_dirs:
                seen_dirs.add(dir_path)
                indent = "  " * (depth - 1)
                lines.append(f"{indent}{parts[depth - 1]}/")
        # Emit the file itself
        indent = "  " * (len(parts) - 1)
        lines.append(f"{indent}{parts[-1]}")

    return "\n".join(lines)


def _count_languages(files: list[str]) -> dict[str, int]:
    """Count files by extension.

    Returns a dict mapping extension (e.g. ``".py"``) to count.
    Files without an extension are counted under ``"(none)"``.
    """
    counts: dict[str, int] = {}
    for filepath in files:
        ext = os.path.splitext(filepath)[1]
        if not ext:
            ext = "(none)"
        counts[ext] = counts.get(ext, 0) + 1
    return counts


def _count_loc(project_dir: str, files: list[str]) -> int:
    """Count non-empty lines across all tracked files.

    Silently skips binary files and files that cannot be decoded.
    """
    total = 0
    for filepath in files:
        full_path = os.path.join(project_dir, filepath)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.strip():
                        total += 1
        except (OSError, UnicodeDecodeError):
            continue
    return total


def _read_readme(project_dir: str) -> str:
    """Read the first 200 lines of the project README.

    Searches for ``README.md``, ``readme.md``, and ``README.rst``
    (in that order). Returns empty string if none found.
    """
    candidates = ["README.md", "readme.md", "README.rst"]
    for name in candidates:
        readme_path = os.path.join(project_dir, name)
        if os.path.isfile(readme_path):
            try:
                with open(readme_path, "r", encoding="utf-8", errors="ignore") as fh:
                    lines = []
                    for i, line in enumerate(fh):
                        if i >= 200:
                            break
                        lines.append(line)
                    return "".join(lines).strip()
            except OSError:
                continue
    return ""


def _read_config(project_dir: str) -> str:
    """Extract the ``[project]`` section from ``pyproject.toml``.

    Returns the raw text of the section, or empty string if not found.
    """
    toml_path = os.path.join(project_dir, "pyproject.toml")
    if not os.path.isfile(toml_path):
        return ""
    try:
        with open(toml_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return ""

    section_lines: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[project]":
            in_section = True
            section_lines.append(line)
            continue
        if in_section:
            # A new section header ends the [project] section
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            section_lines.append(line)

    return "".join(section_lines).strip()


def _build_module_index(
    project_dir: str, files: list[str]
) -> dict[str, str]:
    """Find top-level ``__init__.py`` files and extract their docstrings.

    A "top-level" ``__init__.py`` is one that sits exactly one directory
    below the project root (e.g. ``src/__init__.py``).

    Returns a dict mapping package name to its docstring (or empty string).
    """
    index: dict[str, str] = {}
    for filepath in files:
        parts = filepath.split("/")
        if len(parts) == 2 and parts[1] == "__init__.py":
            package_name = parts[0]
            full_path = os.path.join(project_dir, filepath)
            docstring = _extract_docstring(full_path)
            index[package_name] = docstring
    return index


def _extract_docstring(filepath: str) -> str:
    """Extract the module-level docstring from a Python file.

    Reads the first 2000 bytes and looks for triple-quoted strings.
    Returns the docstring content (without quotes), or empty string.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read(2000)
    except OSError:
        return ""

    for quote in ('"""', "'''"):
        if quote in content:
            start = content.index(quote)
            end = content.find(quote, start + 3)
            if end != -1:
                return content[start + 3 : end].strip()
    return ""


def _get_recent_commits(project_dir: str, count: int = 10) -> str:
    """Return the last ``count`` commit summaries (oneline format).

    Returns empty string if git log fails.
    """
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{count}"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def _get_branch(project_dir: str) -> str:
    """Return the current git branch name.

    Falls back to empty string on error.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_languages(languages: dict[str, int]) -> str:
    """Format language counts as a compact list."""
    if not languages:
        return "(none detected)"
    items = sorted(languages.items(), key=lambda x: -x[1])
    return ", ".join(f"{ext}: {count}" for ext, count in items)


def _format_module_index(module_index: dict[str, str]) -> str:
    """Format module index as a list of package names with docstrings."""
    lines = []
    for name, doc in sorted(module_index.items()):
        if doc:
            lines.append(f"- **{name}**: {doc}")
        else:
            lines.append(f"- **{name}**")
    return "\n".join(lines)
