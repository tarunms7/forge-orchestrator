"""PR creation utilities for TUI — push, generate, create."""

from __future__ import annotations
import asyncio
import logging

logger = logging.getLogger("forge.tui.pr_creator")


async def push_branch(project_dir: str, branch: str) -> bool:
    logger.info("Pushing branch %r from %s", branch, project_dir)
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "push", "-u", "origin", branch,
            cwd=project_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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
) -> str:
    total = len(tasks) + (len(failed_tasks) if failed_tasks else 0)
    completed = len(tasks)

    if failed_tasks:
        lines = ["## Summary", f"Built by Forge pipeline • {total} tasks • {completed}/{total} completed • {time} • ${cost:.2f}", ""]
        lines.append("## Completed Tasks")
    else:
        lines = ["## Summary", f"Built by Forge pipeline • {total} tasks • {time} • ${cost:.2f}", ""]
        lines.append("## Tasks")

    for t in tasks:
        added = t.get("added", 0)
        removed = t.get("removed", 0)
        files = t.get("files", 0)
        lines.append(f"- \u2705 **{t['title']}** — +{added}/-{removed}, {files} files")

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

    lines.extend(["", "\U0001f916 Built with [Forge](https://github.com/tarunms7/forge-orchestrator)"])
    return "\n".join(lines)


async def create_pr(
    project_dir: str, title: str, body: str, base: str = "main", head: str | None = None,
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
            cwd=project_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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
