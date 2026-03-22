"""PR creation utilities for TUI — push, generate, create."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("forge.tui.pr_creator")


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
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("Push failed (exit %d): %s", proc.returncode, stderr.decode())
            return False
        logger.info("Push succeeded: %s", stderr.decode().strip() or stdout.decode().strip())
        return True
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
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("PR creation failed (exit %d): %s", proc.returncode, stderr.decode())
            return None
        url = stdout.decode().strip()
        logger.info("PR created: %s", url)
        return url
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
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "Failed to add related PRs comment to %s (exit %d): %s",
                pr_url,
                proc.returncode,
                stderr.decode(),
            )
    except Exception:
        logger.warning("Failed to add related PRs comment to %s", pr_url, exc_info=True)
