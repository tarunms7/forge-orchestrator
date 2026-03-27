"""PR creation utilities for TUI — push, generate, create."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass, field

logger = logging.getLogger("forge.tui.pr_creator")


# Formatters to try, in order.  Each entry: (tool_name, check_binary, cmd, cwd_subdir, success_indicator)
# cwd_subdir: if set, run from that subdirectory (e.g. "web" for prettier).
# success_indicator: bytes to look for in stdout to know if anything was formatted.
_FORMATTERS: list[tuple[str, str, list[str], str | None, bytes]] = [
    ("ruff", "ruff", ["ruff", "format", "."], None, b"reformatted"),
    ("gofmt", "gofmt", ["gofmt", "-w", "."], None, b""),  # gofmt has no output on success
    ("cargo fmt", "cargo", ["cargo", "fmt"], None, b""),
]


def _detect_formatters(worktree_path: str) -> list[tuple[str, list[str], str]]:
    """Detect which formatters are available and relevant for this project.

    Returns list of (name, command, cwd) tuples.
    """
    import os

    result = []

    for name, binary, cmd, subdir, _ in _FORMATTERS:
        if not shutil.which(binary):
            continue
        cwd = os.path.join(worktree_path, subdir) if subdir else worktree_path
        # Check if there are relevant files
        if name == "ruff" and not any(
            f.endswith(".py")
            for f in os.listdir(worktree_path)
            if os.path.isfile(os.path.join(worktree_path, f))
        ):
            # Check deeper — maybe Python files are in subdirs
            has_py = os.path.isfile(
                os.path.join(worktree_path, "pyproject.toml")
            ) or os.path.isfile(os.path.join(worktree_path, "setup.py"))
            if not has_py:
                continue
        if name == "cargo fmt" and not os.path.isfile(os.path.join(worktree_path, "Cargo.toml")):
            continue
        if name == "gofmt" and not os.path.isfile(os.path.join(worktree_path, "go.mod")):
            continue
        result.append((name, cmd, cwd))

    # Prettier: check in root and common frontend subdirs
    if shutil.which("npx"):
        for subdir in [None, "web", "frontend", "client"]:
            check_dir = os.path.join(worktree_path, subdir) if subdir else worktree_path
            if os.path.isfile(os.path.join(check_dir, "package.json")):
                # Check if prettier is a dependency
                try:
                    import json

                    with open(os.path.join(check_dir, "package.json"), encoding="utf-8") as f:
                        pkg = json.load(f)
                    all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "prettier" in all_deps:
                        result.append(("prettier", ["npx", "prettier", "--write", "."], check_dir))
                except Exception:
                    pass

    return result


async def auto_format_branch(project_dir: str, branch: str) -> bool:
    """Run code formatters on the pipeline branch before push.

    Creates a temporary worktree, detects project languages, runs the
    appropriate formatters (ruff for Python, gofmt for Go, cargo fmt for
    Rust, prettier for JS/TS), commits changes if any, then cleans up.

    Returns True if formatting was applied, False otherwise.
    Completely non-fatal — if anything fails, PR creation proceeds.
    """
    with tempfile.TemporaryDirectory(prefix="forge-format-") as tmp_dir:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "worktree",
                "add",
                tmp_dir,
                branch,
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                logger.warning("Could not create format worktree: %s", stderr.decode())
                return False

            # Detect and run formatters
            formatters = _detect_formatters(tmp_dir)
            if not formatters:
                logger.debug("No formatters detected for this project")
                return False

            for name, cmd, cwd in formatters:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        cwd=cwd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, fmt_stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                    if proc.returncode == 0:
                        logger.info("Formatter %s completed: %s", name, stdout.decode().strip()[:200])
                    else:
                        logger.warning(
                            "Formatter %s failed (exit %d): %s",
                            name,
                            proc.returncode,
                            fmt_stderr.decode()[:200],
                        )
                except TimeoutError:
                    logger.warning("Formatter %s timed out after 120s", name)
                except Exception as e:
                    logger.warning("Formatter %s error: %s", name, e)

            # Check if any files actually changed
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--quiet",
                cwd=tmp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                # No changes — formatters ran but nothing to commit
                return False

            # Stage and commit
            proc = await asyncio.create_subprocess_exec(
                "git",
                "add",
                "-A",
                cwd=tmp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            proc = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--cached",
                "--quiet",
                cwd=tmp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "commit",
                    "-m",
                    "style: auto-format code before PR",
                    cwd=tmp_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30)
                logger.info("Committed auto-format changes on %s", branch)
                return True

            return False
        except Exception as e:
            logger.warning("Auto-format failed (non-fatal): %s", e)
            return False
        finally:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "worktree",
                    "remove",
                    tmp_dir,
                    "--force",
                    cwd=project_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30)
            except Exception as e:
                logger.warning("Failed to remove format worktree %s: %s", tmp_dir, e)


async def push_branch(project_dir: str, branch: str) -> bool:
    logger.info("Pushing branch %r from %s", branch, project_dir)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "push",
            "-u",
            "origin",
            branch,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            logger.error("Push failed (exit %d): %s", proc.returncode, stderr.decode())
            return False
        logger.info("Push succeeded: %s", stderr.decode().strip() or stdout.decode().strip())
        return True
    except TimeoutError:
        logger.error("Push timed out after 120s")
        return False
    except FileNotFoundError:
        logger.error("git not found on PATH")
        return False


def generate_pr_body(
    *,
    tasks: list[dict],
    failed_tasks: list[dict] | None = None,
    time: str,
    cost: float,
    questions: list[dict],
    related_prs: dict[str, str] | None = None,
    repo_id: str | None = None,
) -> str:
    total = len(tasks) + (len(failed_tasks) if failed_tasks else 0)
    completed = len(tasks)

    summary_suffix = f" [{repo_id}]" if repo_id else ""
    if failed_tasks:
        lines = [
            "## Summary",
            f"Built by Forge pipeline • {total} tasks • {completed}/{total} completed • {time} • ${cost:.2f}{summary_suffix}",
            "",
        ]
    else:
        lines = [
            "## Summary",
            f"Built by Forge pipeline • {total} tasks • {time} • ${cost:.2f}{summary_suffix}",
            "",
        ]

    if related_prs:
        lines.append("## Related PRs")
        for rid, url in related_prs.items():
            lines.append(f"- **{rid}**: {url}")
        lines.append("")

    if failed_tasks:
        lines.append("## Completed Tasks")
    else:
        lines.append("## Tasks")

    for t in tasks:
        added = t.get("added", 0)
        removed = t.get("removed", 0)
        files_count = t.get("files", 0)
        file_list = t.get("file_list", [])
        description = t.get("description", "")
        impl_summary = t.get("implementation_summary", "")

        # Header line with diff stats
        stats_parts = []
        if added or removed:
            stats_parts.append(f"+{added}/-{removed}")
        if files_count:
            stats_parts.append(f"{files_count} files")
        stats_str = f" — {', '.join(stats_parts)}" if stats_parts else ""
        lines.append(f"- \u2705 **{t['title']}**{stats_str}")

        # Collapsible detail block with description, summary, and files
        detail_lines: list[str] = []
        if description:
            detail_lines.append(f"  **What:** {description}")
        if impl_summary:
            detail_lines.append(f"  **Done:** {impl_summary}")
        if file_list:
            formatted_files = ", ".join(f"`{f}`" for f in file_list)
            detail_lines.append(f"  **Files:** {formatted_files}")
        if detail_lines:
            lines.append("  <details><summary>Details</summary>\n")
            lines.extend(detail_lines)
            lines.append("\n  </details>")

    if failed_tasks:
        lines.append("")
        lines.append("## Failed Tasks (not included in this PR)")
        for t in failed_tasks:
            error = t.get("error", "failed")
            lines.append(f"- \u274c **{t['title']}** — {error}")

    if questions:
        lines.append("")
        lines.append("## Human Decisions")
        for q in questions:
            lines.append(f"- **Q:** {q['question']} → **A:** {q['answer']}")

    lines.extend(
        ["", "\U0001f916 Built with [Forge](https://github.com/tarunms7/forge-orchestrator)"]
    )
    return "\n".join(lines)


async def create_pr(
    project_dir: str,
    title: str,
    body: str,
    base: str = "main",
    head: str | None = None,
) -> str | None:
    # --head is REQUIRED: the working directory is on main, not the pipeline branch.
    # Without --head, gh tries to create a PR from main→main which always fails.
    cmd = ["gh", "pr", "create", "--title", title, "--body", body, "--base", base]
    if head:
        cmd.extend(["--head", head])
    logger.info("Creating PR: %s → %s, title=%r", head or "(current)", base, title)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            logger.error("PR creation failed (exit %d): %s", proc.returncode, stderr.decode())
            return None
        url = stdout.decode().strip()
        logger.info("PR created: %s", url)
        return url
    except TimeoutError:
        logger.error("PR creation timed out after 60s")
        return None
    except FileNotFoundError:
        logger.error("gh CLI not found on PATH")
        return None


@dataclass
class MultiRepoPrResult:
    """Result of creating PRs across multiple repositories."""

    pr_urls: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


async def create_prs_multi_repo(
    *,
    task_summaries: list[dict],
    repos: dict[str, dict],
    pipeline_branches: dict[str, str],
    description: str,
    elapsed_str: str,
    questions: list[dict],
    failed_tasks: list[dict] | None = None,
) -> MultiRepoPrResult:
    """Create PRs across multiple repositories, grouping tasks by repo_id."""
    result = MultiRepoPrResult()
    is_multi = len(repos) > 1

    # Group task summaries by repo_id
    tasks_by_repo: dict[str, list[dict]] = {}
    failed_by_repo: dict[str, list[dict]] = {}
    for t in task_summaries:
        rid = t.get("repo_id", "default")
        tasks_by_repo.setdefault(rid, []).append(t)
    if failed_tasks:
        for t in failed_tasks:
            rid = t.get("repo_id", "default")
            failed_by_repo.setdefault(rid, []).append(t)

    # Create PRs for each repo
    for repo_id, repo_cfg in repos.items():
        project_dir = repo_cfg["project_dir"]
        base_branch = repo_cfg.get("base_branch", "main")
        branch = pipeline_branches.get(repo_id, "")

        # Push the branch
        pushed = await push_branch(project_dir, branch)
        if not pushed:
            result.failures[repo_id] = f"push_branch failed for {repo_id}"
            continue

        # Compute per-repo cost
        repo_tasks = tasks_by_repo.get(repo_id, [])
        repo_failed = failed_by_repo.get(repo_id) or None
        repo_cost = sum(t.get("cost_usd", 0) for t in repo_tasks)
        if repo_failed:
            repo_cost += sum(t.get("cost_usd", 0) for t in repo_failed)

        # Build PR title
        if is_multi:
            title = f"Forge: {description} [{repo_id}]"
        else:
            title = f"Forge: {description}"

        # Build PR body — include related_prs from already-created PRs
        related = dict(result.pr_urls) if is_multi else None
        body = generate_pr_body(
            tasks=repo_tasks,
            failed_tasks=repo_failed,
            time=elapsed_str,
            cost=repo_cost,
            questions=questions,
            related_prs=related if related else None,
            repo_id=repo_id if is_multi else None,
        )

        pr_url = await create_pr(project_dir, title, body, base=base_branch, head=branch)
        if pr_url:
            result.pr_urls[repo_id] = pr_url
        else:
            result.failures[repo_id] = f"create_pr failed for {repo_id}"

    # Cross-link all PRs via comments (only for multi-repo)
    if is_multi and len(result.pr_urls) > 1:
        for repo_id, pr_url in result.pr_urls.items():
            project_dir = repos[repo_id]["project_dir"]
            other_prs = {rid: url for rid, url in result.pr_urls.items() if rid != repo_id}
            if other_prs:
                try:
                    await _add_related_prs_comment(
                        pr_url=pr_url,
                        related_prs=other_prs,
                        project_dir=project_dir,
                    )
                except Exception:
                    logger.warning("Failed to cross-link PR %s", pr_url, exc_info=True)

    return result


async def _add_related_prs_comment(
    *,
    pr_url: str,
    related_prs: dict[str, str],
    project_dir: str,
) -> None:
    """Add a comment to a PR linking to related PRs in other repos."""
    # Extract PR number from URL
    pr_number = pr_url.rstrip("/").split("/")[-1]

    # Build comment body
    comment_lines = ["## Related Forge PRs"]
    for rid, url in related_prs.items():
        comment_lines.append(f"- **{rid}**: {url}")
    comment_body = "\n".join(comment_lines)

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "pr",
            "comment",
            pr_number,
            "--body",
            comment_body,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            logger.warning(
                "Failed to add related PRs comment to %s (exit %d): %s",
                pr_url,
                proc.returncode,
                stderr.decode(),
            )
    except Exception:
        logger.warning("Failed to add related PRs comment to %s", pr_url, exc_info=True)
