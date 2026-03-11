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
    *, tasks: list[dict], time: str, cost: float, questions: list[dict],
) -> str:
    lines = [f"## Summary", f"Built by Forge pipeline • {len(tasks)} tasks • {time} • ${cost:.2f}", ""]
    lines.append("## Tasks")
    for t in tasks:
        added = t.get("added", 0)
        removed = t.get("removed", 0)
        files = t.get("files", 0)
        lines.append(f"- \u2705 **{t['title']}** — +{added}/-{removed}, {files} files")
    if questions:
        lines.append("")
        lines.append("## Human Decisions")
        for q in questions:
            lines.append(f"- **Q:** {q['question']} → **A:** {q['answer']}")
    lines.extend(["", "\U0001f916 Built with [Forge](https://github.com/tarunms7/forge-orchestrator)"])
    return "\n".join(lines)


async def create_pr(project_dir: str, title: str, body: str, base: str = "main") -> str | None:
    logger.info("Creating PR: branch → %s, title=%r", base, title)
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "create", "--title", title, "--body", body, "--base", base,
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
