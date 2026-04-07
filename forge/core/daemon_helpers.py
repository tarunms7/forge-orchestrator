"""Module-level helper functions extracted from forge/core/daemon.py.

These utilities handle git operations, prompt construction, diff analysis,
and console output used by the Forge daemon orchestration loop.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
import subprocess

from rich.table import Table

from forge.core.logging_config import make_console
from forge.core.sanitize import validate_repo_id, validate_task_id

logger = logging.getLogger("forge")
console = make_console()

_FORGE_QUESTION_MARKER = "FORGE_QUESTION:"
_FORGE_LEARNING_MARKER = "FORGE_LEARNING:"
_REVIEW_DIFF_EXCLUDES = (".claude/", ".forge/")
_PLAINTEXT_QUESTION_LINE = re.compile(
    r"^\s*Question(?:\s+\d+)?\s*:\s*(.+\?)\s*$",
    re.IGNORECASE,
)
_PLAINTEXT_QUESTION_LABEL = re.compile(
    r"^\s*Question(?:\s+\d+)?\s*:\s*(.*\S)?\s*$",
    re.IGNORECASE,
)
_PLAINTEXT_QUESTION_BULLET = re.compile(r"^\s*(?:[-*]|\d+[.)]|[A-Za-z][.)])\s+(.*\S)\s*$")


async def async_subprocess(
    cmd: list[str],
    cwd: str,
    *,
    timeout: float = 30,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command asynchronously, returning a CompletedProcess for API compat.

    On timeout, kills the process and raises ``asyncio.TimeoutError`` with a
    descriptive message.  Does **not** raise on non-zero exit — callers handle
    check logic themselves.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,  # Prevent hangs on interactive prompts
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"Command {cmd} timed out after {timeout}s",
        )

    stdout_str = stdout_bytes.decode() if stdout_bytes else ""
    stderr_str = stderr_bytes.decode() if stderr_bytes else ""

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,  # type: ignore[arg-type]
        stdout=stdout_str,
        stderr=stderr_str,
    )


def _parse_forge_question(text: str | None) -> dict | None:
    """Parse a FORGE_QUESTION block from agent output.

    Returns dict with at least a 'question' key (string), or None.
    No restrictions on additional keys — accepts any valid JSON with a 'question' field.
    """
    if not text:
        return None

    marker_idx = text.rfind(_FORGE_QUESTION_MARKER)
    if marker_idx == -1:
        lines = text.splitlines()
        for idx, raw_line in enumerate(lines):
            normalized_line = raw_line.strip().replace("**", "")
            match = _PLAINTEXT_QUESTION_LINE.match(normalized_line)
            question = match.group(1).strip() if match else ""
            if not question:
                label_match = _PLAINTEXT_QUESTION_LABEL.match(normalized_line)
                if not label_match:
                    continue
                inline_text = (label_match.group(1) or "").strip()
                if inline_text.endswith("?"):
                    question = inline_text
                else:
                    forward_question = idx + 1
                    while forward_question < len(lines):
                        candidate = lines[forward_question].strip()
                        if not candidate:
                            forward_question += 1
                            continue
                        if _PLAINTEXT_QUESTION_BULLET.match(candidate):
                            forward_question += 1
                            continue
                        if candidate.endswith("?"):
                            question = candidate
                        break
            if not question:
                continue

            context_lines: list[str] = []
            back = idx - 1
            while back >= 0 and lines[back].strip():
                context_lines.append(lines[back].strip())
                back -= 1
            context = " ".join(reversed(context_lines)).strip()

            suggestions: list[str] = []
            forward = idx + 1
            while forward < len(lines):
                candidate = lines[forward].strip()
                if not candidate:
                    if suggestions:
                        break
                    forward += 1
                    continue
                bullet_match = _PLAINTEXT_QUESTION_BULLET.match(candidate)
                if not bullet_match:
                    break
                suggestions.append(bullet_match.group(1).strip())
                forward += 1

            logger.info(
                "Recovered plain-text question without FORGE_QUESTION marker: %s",
                question[:200],
            )
            return {
                "question": question,
                "context": context or None,
                "suggestions": suggestions,
                "source": "plaintext_fallback",
            }
        return None

    after_marker = text[marker_idx + len(_FORGE_QUESTION_MARKER) :].strip()

    # Strip markdown fences if present
    json_text = after_marker
    fence_match = re.match(r"```(?:json)?\s*\n?(.*?)\n?\s*```", json_text, re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1).strip()
    else:
        # Find the closing brace using string-aware matching
        brace_depth = 0
        json_end = -1
        in_string = False
        escape_next = False
        for i, ch in enumerate(json_text):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    json_end = i + 1
                    break
        if json_end == -1:
            logger.warning(
                "FORGE_QUESTION marker found but JSON brace matching failed. "
                "Raw text after marker: %s",
                after_marker[:500],
            )
            return None
        # Accept the question regardless of trailing text.
        # If the marker is present and JSON is valid, the agent intended to ask.
        json_text = json_text[:json_end]

    try:
        data = _json.loads(json_text)
    except (_json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "FORGE_QUESTION marker found but JSON parse failed: %s. Raw JSON text: %s",
            exc,
            json_text[:500],
        )
        return None

    if not isinstance(data, dict):
        logger.warning(
            "FORGE_QUESTION JSON parsed but is not a dict (got %s)",
            type(data).__name__,
        )
        return None
    if "question" not in data or not isinstance(data["question"], str):
        logger.warning(
            "FORGE_QUESTION JSON parsed but missing 'question' key. Keys found: %s",
            list(data.keys()),
        )
        return None

    return data


def _parse_forge_learning(text: str | None) -> dict | None:
    """Parse a FORGE_LEARNING block from agent output.

    Returns dict with 'trigger', 'resolution', and 'files' keys, or None.
    Unlike _parse_forge_question, does NOT require the marker to be at the end
    (the agent may add text after the learning report).
    """
    if not text:
        return None

    marker_idx = text.rfind(_FORGE_LEARNING_MARKER)
    if marker_idx == -1:
        return None

    after_marker = text[marker_idx + len(_FORGE_LEARNING_MARKER) :].strip()

    # Extract JSON: find matching braces
    json_text = after_marker
    # Strip markdown fences if present
    fence_match = re.match(r"```(?:json)?\s*\n?(.*?)\n?\s*```", json_text, re.DOTALL)
    if fence_match:
        json_text = fence_match.group(1).strip()
    else:
        # Find the closing brace
        brace_depth = 0
        json_end = -1
        for i, ch in enumerate(json_text):
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    json_end = i + 1
                    break
        if json_end == -1:
            return None
        json_text = json_text[:json_end]

    try:
        data = _json.loads(json_text)
    except (_json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    # Validate required fields
    if not isinstance(data.get("trigger"), str) or not data["trigger"]:
        return None
    if not isinstance(data.get("resolution"), str) or not data["resolution"]:
        return None
    if not isinstance(data.get("files"), list) or not data["files"]:
        return None

    return data


def _extract_text(message) -> str | None:
    """Extract human-readable text from a claude-code-sdk message or ProviderEvent.

    Accepts both legacy SDK messages (AssistantMessage/ResultMessage) and
    normalized ProviderEvent objects from the provider protocol.
    """
    # ── Provider protocol path: ProviderEvent ──
    from forge.providers.base import EventKind, ProviderEvent

    if isinstance(message, ProviderEvent):
        if message.kind == EventKind.TEXT and message.text:
            text = message.text.strip()
            if not text:
                return None
            # Skip JSON blobs (planner output, etc.)
            if text.startswith("{") or text.startswith("["):
                return None
            return text
        return None

    # ── Legacy SDK path ──
    try:
        from claude_code_sdk import AssistantMessage, ResultMessage
    except ImportError:
        return None
    if isinstance(message, AssistantMessage):
        parts = []
        for block in message.content or []:
            if hasattr(block, "text"):
                text = block.text.strip()
                # Skip empty, JSON blobs, and tool metadata
                if not text:
                    continue
                if text.startswith("{") or text.startswith("["):
                    continue
                parts.append(text)
        return "\n".join(parts) if parts else None
    if isinstance(message, ResultMessage):
        return None
    return None


def _extract_activity(message) -> str | None:
    """Extract human-readable activity from a claude-code-sdk message or ProviderEvent.

    Unlike ``_extract_text`` which only returns TextBlock content, this
    also formats ToolUseBlock messages as short activity descriptions
    (e.g. "📖 Reading src/models/user.py").

    Accepts both legacy SDK messages and normalized ProviderEvent objects.
    """
    # ── Provider protocol path: ProviderEvent ──
    from forge.providers.base import EventKind, ProviderEvent

    if isinstance(message, ProviderEvent):
        if message.kind == EventKind.TEXT and message.text:
            text = message.text.strip()
            if not text or text.startswith("{") or text.startswith("["):
                return None
            return text
        if message.kind == EventKind.TOOL_USE and message.tool_name:
            inp = _coerce_tool_input(message.tool_name, message.tool_input)
            label = _format_tool_activity(message.tool_name, inp)
            return label
        if message.kind == EventKind.TOOL_RESULT and message.tool_name and message.is_tool_error:
            tool_label = _humanize_tool_name(message.tool_name)
            return f"⚠️ {tool_label} failed"
        return None

    # ── Legacy SDK path ──
    try:
        from claude_code_sdk import AssistantMessage, ResultMessage
    except ImportError:
        return None

    if isinstance(message, AssistantMessage):
        parts: list[str] = []
        for block in message.content or []:
            # Text blocks — same filtering as _extract_text
            if hasattr(block, "text"):
                text = block.text.strip()
                if not text:
                    continue
                if text.startswith("{") or text.startswith("["):
                    continue
                parts.append(text)
            # Tool use blocks — show what tool is being called
            elif hasattr(block, "name"):
                tool = block.name
                inp = getattr(block, "input", {}) or {}
                label = _format_tool_activity(tool, inp)
                if label:
                    parts.append(label)
        return "\n".join(parts) if parts else None

    if isinstance(message, ResultMessage):
        return None
    return None


_TOOL_ICONS = {
    "read": "📖",
    "glob": "🔍",
    "grep": "🔎",
    "bash": "⚡",
    "write": "✏️",
    "edit": "✏️",
    "mcp_tool": "🧩",
}

_PATH_KEYS = (
    "file_path",
    "path",
    "target_path",
    "new_path",
    "old_path",
    "source_path",
    "destination_path",
)
_COMMAND_KEYS = ("command", "cmd")


def _normalize_tool_activity_name(tool: str) -> str:
    """Normalize legacy and provider tool names to Forge's lowercase form."""
    normalized = (tool or "").strip()
    if not normalized:
        return ""
    legacy_map = {
        "Bash": "bash",
        "Read": "read",
        "Write": "write",
        "Edit": "edit",
        "Glob": "glob",
        "Grep": "grep",
        "McpTool": "mcp_tool",
        "MCPTool": "mcp_tool",
    }
    if normalized in legacy_map:
        return legacy_map[normalized]
    return normalized.lower()


def _humanize_tool_name(tool: str) -> str:
    normalized = _normalize_tool_activity_name(tool)
    labels = {
        "bash": "command",
        "read": "file read",
        "write": "file write",
        "edit": "file edit",
        "glob": "file search",
        "grep": "code search",
        "mcp_tool": "MCP tool",
    }
    return labels.get(normalized, normalized.replace("_", " "))


def _coerce_tool_input(tool: str, raw_input: str | dict | None) -> dict:
    """Parse provider tool input, falling back to sensible raw-string adapters."""
    normalized = _normalize_tool_activity_name(tool)

    def _extract_path(value: object) -> str | None:
        if isinstance(value, dict):
            for key in _PATH_KEYS:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
            for candidate in value.values():
                path = _extract_path(candidate)
                if path:
                    return path
            return None
        if isinstance(value, list):
            for item in value:
                path = _extract_path(item)
                if path:
                    return path
        return None

    def _extract_command(value: object) -> str | list[str] | None:
        if isinstance(value, dict):
            for key in _COMMAND_KEYS:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
                if isinstance(candidate, list) and candidate:
                    return [str(part) for part in candidate]
            return None
        return None

    if isinstance(raw_input, dict):
        path = _extract_path(raw_input)
        command = _extract_command(raw_input)
        if normalized == "bash" and command:
            return {"command": command}
        if normalized in {"read", "write", "edit"} and path:
            enriched = dict(raw_input)
            enriched.setdefault("file_path", path)
            return enriched
        return raw_input
    if not raw_input:
        return {}
    if isinstance(raw_input, str):
        try:
            parsed = _json.loads(raw_input)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            path = _extract_path(parsed)
            command = _extract_command(parsed)
            if normalized == "bash" and command:
                return {"command": command}
            if normalized in {"read", "write", "edit"} and path:
                enriched = dict(parsed)
                enriched.setdefault("file_path", path)
                return enriched
            return parsed
        if isinstance(parsed, list):
            path = _extract_path(parsed)
            if normalized in {"read", "write", "edit"} and path:
                return {"file_path": path, "changes": parsed}
        if normalized == "bash":
            return {"command": raw_input}
        if normalized in {"read", "write", "edit"}:
            return {"file_path": raw_input}
        if normalized in {"glob", "grep"}:
            return {"pattern": raw_input}
        if normalized == "mcp_tool":
            return {"tool": raw_input}
    return {}


def _format_tool_activity(tool: str, inp: dict) -> str | None:
    """Format a tool use block as a short human-readable string."""
    normalized = _normalize_tool_activity_name(tool)
    icon = _TOOL_ICONS.get(normalized, "🔧")
    if normalized == "read":
        path = inp.get("file_path") or inp.get("path", "")
        if path:
            # Show just filename and parent dir for brevity
            short = "/".join(path.rsplit("/", 2)[-2:]) if "/" in path else path
            return f"{icon} Reading {short}"
        return f"{icon} Reading file"
    if normalized == "glob":
        pattern = inp.get("pattern", "")
        return f"{icon} Searching: {pattern}" if pattern else f"{icon} Searching files"
    if normalized == "grep":
        pattern = inp.get("pattern", "")
        return f"{icon} Grep: {pattern[:60]}" if pattern else f"{icon} Searching code"
    if normalized == "bash":
        cmd = inp.get("command", "")
        if isinstance(cmd, list):
            cmd = " ".join(str(part) for part in cmd)
        if cmd:
            short = cmd[:80] + ("..." if len(cmd) > 80 else "")
            return f"{icon} {short}"
        return f"{icon} Running command"
    if normalized == "write":
        path = inp.get("file_path") or inp.get("path", "")
        short = "/".join(path.rsplit("/", 2)[-2:]) if "/" in path else path
        return f"{icon} Writing {short}" if path else f"{icon} Writing file"
    if normalized == "edit":
        path = inp.get("file_path") or inp.get("path", "")
        short = "/".join(path.rsplit("/", 2)[-2:]) if "/" in path else path
        return f"{icon} Editing {short}" if path else f"{icon} Editing file"
    if normalized == "mcp_tool":
        server = inp.get("server", "")
        tool_name = inp.get("tool", "")
        if server and tool_name:
            return f"{icon} Calling {server}.{tool_name}"
        if tool_name:
            return f"{icon} Calling {tool_name}"
        return f"{icon} Calling MCP tool"
    return f"🔧 {_humanize_tool_name(tool)}"


def _humanize_model_spec(model: object) -> str:
    """Render provider:model values as clean user-facing labels."""
    from forge.providers.base import ModelSpec

    raw = str(model).strip()
    if not raw:
        return raw
    try:
        spec = model if isinstance(model, ModelSpec) else ModelSpec.parse(raw)
    except Exception:
        return raw

    if spec.provider == "claude":
        labels = {
            "opus": "Claude Opus",
            "sonnet": "Claude Sonnet",
            "haiku": "Claude Haiku",
        }
        return labels.get(spec.model, f"Claude {spec.model.replace('-', ' ').title()}")

    if spec.provider == "openai":
        labels = {
            "gpt-5.4": "GPT-5.4",
            "gpt-5.4-mini": "GPT-5.4 Mini",
            "gpt-5.3-codex": "GPT-5.3 Codex",
            "o3": "o3",
        }
        return labels.get(spec.model, spec.model)

    return raw


def format_routing_summary(
    planner_model: object,
    agent_low_model: object,
    agent_medium_model: object,
    agent_high_model: object,
    reviewer_model: object,
    reviewer_effort: str | None = None,
) -> str:
    """Create a human-readable routing summary for the pipeline configuration.

    Args:
        planner_model: Model used for planning phase
        agent_low_model: Model used for low-complexity agent tasks
        agent_medium_model: Model used for medium-complexity agent tasks
        agent_high_model: Model used for high-complexity agent tasks
        reviewer_model: Model used for review phase
        reviewer_effort: Optional reasoning effort level (e.g. 'high', 'medium')

    Returns:
        Formatted routing string like:
        'Routing: Planner Claude Opus | Agent (L/M/H) Claude Haiku/Claude Sonnet/Claude Opus | Review Claude Sonnet'
        with optional ' (high reasoning)' suffix when reviewer_effort is provided.
    """
    routing_line = (
        "Routing: "
        f"Planner {_humanize_model_spec(planner_model)} | "
        f"Agent (L/M/H) {_humanize_model_spec(agent_low_model)}/"
        f"{_humanize_model_spec(agent_medium_model)}/"
        f"{_humanize_model_spec(agent_high_model)} | "
        f"Review {_humanize_model_spec(reviewer_model)}"
    )
    if reviewer_effort:
        routing_line += f" ({reviewer_effort} reasoning)"
    return routing_line


def _is_review_excluded_path(path: str) -> bool:
    """Return True when a diff path points at Forge-managed infrastructure."""
    normalized = path.strip()
    return normalized.startswith(".claude/") or normalized.startswith(".forge/")


def _filter_review_diff(diff_text: str) -> str:
    """Strip Forge-managed file blocks from a unified diff."""
    if not diff_text or "diff --git " not in diff_text:
        return diff_text

    parts = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        if not part.startswith("diff --git "):
            kept.append(part)
            continue
        header = part.splitlines()[0] if part.splitlines() else ""
        match = re.match(r"diff --git a/(.+?) b/(.+)$", header)
        if not match:
            kept.append(part)
            continue
        left_path, right_path = match.groups()
        if _is_review_excluded_path(left_path) or _is_review_excluded_path(right_path):
            continue
        kept.append(part)
    return "".join(kept)


async def update_repos_json_branches(
    db,
    pipeline_id: str,
    pipeline_branches: dict[str, str],
) -> None:
    """Update repos_json in the DB with per-repo branch names.

    For each repo entry whose ``id`` appears in *pipeline_branches*, sets
    ``branch_name`` to the pipeline branch and ``pr_url`` to an empty string.
    Non-matching entries are left unchanged.
    """
    import json

    pipeline_row = await db.get_pipeline(pipeline_id)
    if not pipeline_row or not pipeline_row.repos_json:
        return

    repos_data = json.loads(pipeline_row.repos_json)
    for repo_entry in repos_data:
        repo_id = repo_entry.get("id")
        if repo_id and repo_id in pipeline_branches:
            repo_entry["branch_name"] = pipeline_branches[repo_id]
            repo_entry["pr_url"] = ""

    await db.update_pipeline_repos_json(pipeline_id, json.dumps(repos_data))


async def _get_current_branch(repo_path: str) -> str:
    """Get the current branch name of the repo.

    Falls back to 'main' if the branch can't be determined (e.g. detached
    HEAD or empty repo). Never returns the literal string 'HEAD' since
    that's not a valid branch name for merge targets.
    """
    result = await async_subprocess(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
    )
    branch = result.stdout.strip()
    # "HEAD" is returned for detached HEAD — not a valid branch name.
    # Empty string means the command failed (no commits yet).
    if branch and branch != "HEAD":
        return branch
    # Try symbolic-ref as fallback (works even before first commit)
    sym = await async_subprocess(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_path,
    )
    sym_branch = sym.stdout.strip()
    return sym_branch if sym_branch else "main"


async def list_local_branches(
    repo_path: str,
    include_remote: bool = False,
    return_current: bool = False,
) -> list[str] | tuple[list[str], str]:
    """Return local branch names (current branch first).

    If *include_remote* is True, also includes remote-tracking branches that
    don't have a local counterpart (shown as ``origin/branch-name``).
    Requires a prior ``git fetch`` — does NOT hit the network.

    If *return_current* is True, returns ``(branches, current_branch)`` tuple
    to avoid a redundant subprocess call for callers that need both.
    """
    cmd = ["git", "branch", "--format=%(refname:short)"]
    if include_remote:
        cmd = ["git", "branch", "-a", "--format=%(refname:short)"]
    result = await async_subprocess(cmd, cwd=repo_path)
    if result.returncode != 0:
        fallback = ["main"]
        return (fallback, "main") if return_current else fallback
    raw = [b.strip() for b in result.stdout.splitlines() if b.strip()]
    if not raw:
        fallback = ["main"]
        return (fallback, "main") if return_current else fallback

    local: list[str] = []
    remote_only: list[str] = []
    local_names: set[str] = set()
    for b in raw:
        if b.startswith("origin/"):
            short = b[len("origin/") :]
            if short not in local_names and short != "HEAD":
                remote_only.append(b)
        else:
            local.append(b)
            local_names.add(b)

    current = await _get_current_branch(repo_path)
    if current in local:
        local.remove(current)
        local.insert(0, current)

    branches = local + remote_only
    return (branches, current) if return_current else branches


async def fetch_remote_branches(repo_path: str) -> bool:
    """Run ``git fetch --prune``. Returns True on success."""
    result = await async_subprocess(
        ["git", "fetch", "--prune"],
        cwd=repo_path,
        timeout=30,
    )
    return result.returncode == 0


def _build_agent_prompt(
    title: str, description: str, files: list[str], agent_prompt_modifier: str = ""
) -> str:
    files_str = ", ".join(files) if files else "(no file restrictions)"
    prompt = f"## Task: {title}\n\n{description}\n\n**Files in scope:** {files_str}\n"
    if agent_prompt_modifier:
        prompt += "\n" + agent_prompt_modifier
    return prompt


def _build_retry_prompt(
    title: str,
    description: str,
    files: list[str],
    review_feedback: str,
    retry_number: int,
    agent_prompt_modifier: str = "",
) -> str:
    """Build a prompt for a retry that includes the review failure feedback.

    The agent gets the original task spec PLUS the reviewer's notes so it
    can fix the specific issues instead of starting from scratch.
    """
    files_str = ", ".join(files) if files else "(no file restrictions)"
    prompt = (
        f"## Task: {title} (Retry #{retry_number})\n\n"
        f"{description}\n\n"
        f"**Files in scope:** {files_str}\n\n"
        f"## Review Feedback — FIX THESE SPECIFIC ISSUES\n\n"
        f"Your previous attempt was reviewed and rejected. "
        f"The worktree has your previous code. "
        f"DO NOT start over. Read your existing code, find the specific issues below, and fix them.\n\n"
        f"{review_feedback}\n\n"
        f"## Retry Instructions\n\n"
        f"1. Read the files you modified (listed above) to see your current code\n"
        f"2. For each issue the reviewer flagged, find the EXACT location in your code\n"
        f"3. Make the MINIMAL fix for each issue — do not rewrite unrelated code\n"
        f"4. If the reviewer says something is 'missing' or 'not wired', check if it's in YOUR task scope.\n"
        f"   If it belongs to a sibling task, note that in your commit message and move on.\n"
        f"5. Run linting before committing: `ruff check <files>`\n"
    )
    prompt += (
        "\n\n## Self-Report Learning\n\n"
        "If you fix the issues and your changes work, report what you learned "
        "at the END of your response using this EXACT format:\n\n"
        "FORGE_LEARNING:\n"
        '{"trigger": "brief description of what was wrong", '
        '"resolution": "what you changed and why \u2014 reference specific files and code", '
        '"files": ["file1.py", "file2.py"]}\n\n'
        "Rules:\n"
        "- ONLY include if you made a REAL code change that fixed the issue\n"
        "- Do NOT include if you just retried the same approach and it worked\n"
        "- Do NOT report infrastructure issues (timeouts, connection errors)\n"
        "- Reference specific files and describe concrete changes\n"
    )
    if agent_prompt_modifier:
        prompt += "\n" + agent_prompt_modifier
    return prompt


async def _get_diff_vs_main(worktree_path: str, *, base_ref: str | None = None) -> str:
    """Get diff of the worktree branch vs its merge-base with the parent branch.

    Args:
        worktree_path: Path to the agent's git worktree.
        base_ref: Explicit base ref (e.g. the pipeline branch name) to diff
            against.  When provided, diffs ``base_ref..HEAD`` directly —
            this is reliable regardless of remote state and avoids the
            ``--not --remotes`` heuristic which breaks when the user's
            workflow (squash-merge + delete remote branch) leaves local
            commits unreachable from any remote.

    Falls back to the commit-count heuristic (``HEAD~N``) when *base_ref*
    is ``None`` or cannot be resolved.  Handles root commits (orphan
    branches from repos with no prior history) by diffing against the
    empty tree.

    Forge infrastructure files under ``.claude/`` and ``.forge/`` are excluded
    from the diff so the LLM reviewer only sees agent work product. Repo-level
    files like ``.gitignore`` are preserved because they can be real task
    deliverables and must remain visible to review.
    """
    # ── Fast path: explicit base ref ──────────────────────────────────
    if base_ref is not None:
        verify = await async_subprocess(
            ["git", "rev-parse", "--verify", base_ref],
            cwd=worktree_path,
        )
        if verify.returncode == 0:
            merge_base = await async_subprocess(
                ["git", "merge-base", base_ref, "HEAD"],
                cwd=worktree_path,
            )
            if merge_base.returncode == 0 and merge_base.stdout.strip():
                result = await async_subprocess(
                    ["git", "diff", merge_base.stdout.strip(), "HEAD"],
                    cwd=worktree_path,
                )
                return _filter_review_diff(result.stdout)
            logger.warning(
                "_get_diff_vs_main: merge-base for %r in %s could not be resolved "
                "— falling back to commit-count heuristic",
                base_ref,
                worktree_path,
            )
        logger.warning(
            "_get_diff_vs_main: base_ref %r not found in %s — "
            "falling back to commit-count heuristic",
            base_ref,
            worktree_path,
        )

    # ── Fallback: commit-count heuristic ──────────────────────────────
    count_result = await async_subprocess(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    # Check if HEAD~{commit_count} exists (won't if this is a root commit)
    heuristic_ref = f"HEAD~{commit_count}"
    verify = await async_subprocess(
        ["git", "rev-parse", "--verify", heuristic_ref],
        cwd=worktree_path,
    )

    if verify.returncode == 0:
        # Normal case: diff the agent's commits against their base
        result = await async_subprocess(
            ["git", "diff", heuristic_ref, "HEAD"],
            cwd=worktree_path,
        )
    else:
        # Root commit (orphan branch / new repo): diff against empty tree
        empty_tree_result = await async_subprocess(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
        )
        empty_tree = empty_tree_result.stdout.strip()
        result = await async_subprocess(
            ["git", "diff", empty_tree, "HEAD"],
            cwd=worktree_path,
        )

    return _filter_review_diff(result.stdout)


async def _resolve_ref(repo_path: str, ref: str) -> str | None:
    """Resolve a git ref (branch name) to its immutable commit SHA.

    Used to snapshot the pipeline branch *before* a merge so that
    ``_get_diff_stats`` can compute per-task stats against a fixed
    point rather than the (now-moved) branch tip.
    """
    result = await async_subprocess(
        ["git", "rev-parse", ref],
        cwd=repo_path,
    )
    return result.stdout.strip() if result.returncode == 0 else None


async def _get_diff_stats(worktree_path: str, pipeline_branch: str | None = None) -> dict[str, int]:
    """Get lines added/removed for this task's commits in its worktree.

    When ``pipeline_branch`` is provided the diff is computed from the
    merge-base of ``pipeline_branch`` and ``HEAD``. This isolates only the
    task branch's unique commits even after earlier sibling tasks have already
    advanced the pipeline branch.

    Falls back to the commit-count heuristic (``HEAD~N``) when the pipeline
    branch ref cannot be resolved, logging a warning so the degradation is
    visible in the logs.
    """
    if pipeline_branch is not None:
        # Verify that the pipeline branch ref exists in this worktree
        verify = await async_subprocess(
            ["git", "rev-parse", "--verify", pipeline_branch],
            cwd=worktree_path,
        )
        if verify.returncode == 0:
            merge_base = await async_subprocess(
                ["git", "merge-base", pipeline_branch, "HEAD"],
                cwd=worktree_path,
            )
            if merge_base.returncode == 0 and merge_base.stdout.strip():
                result = await async_subprocess(
                    ["git", "diff", "--shortstat", merge_base.stdout.strip(), "HEAD"],
                    cwd=worktree_path,
                )
                added, removed, files = 0, 0, 0
                if result.returncode == 0 and result.stdout.strip():
                    m_files = re.search(r"(\d+) file", result.stdout)
                    m_add = re.search(r"(\d+) insertion", result.stdout)
                    m_del = re.search(r"(\d+) deletion", result.stdout)
                    if m_files:
                        files = int(m_files.group(1))
                    if m_add:
                        added = int(m_add.group(1))
                    if m_del:
                        removed = int(m_del.group(1))
                return {"linesAdded": added, "linesRemoved": removed, "filesChanged": files}
            logger.warning(
                "_get_diff_stats: merge-base for %r not found in %s — "
                "falling back to commit-count heuristic",
                pipeline_branch,
                worktree_path,
            )
        else:
            logger.warning(
                "_get_diff_stats: pipeline branch %r not found in %s — "
                "falling back to commit-count heuristic",
                pipeline_branch,
                worktree_path,
            )

    # Fallback: find how many commits the agent added on top of the base
    count_result = await async_subprocess(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    base_ref = f"HEAD~{commit_count}"
    verify = await async_subprocess(
        ["git", "rev-parse", "--verify", base_ref],
        cwd=worktree_path,
    )

    if verify.returncode == 0:
        result = await async_subprocess(
            ["git", "diff", "--shortstat", base_ref, "HEAD"],
            cwd=worktree_path,
        )
    else:
        # Root commit (orphan branch / new repo): diff against empty tree
        empty_tree_result = await async_subprocess(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
        )
        empty_tree = empty_tree_result.stdout.strip()
        result = await async_subprocess(
            ["git", "diff", "--shortstat", empty_tree, "HEAD"],
            cwd=worktree_path,
        )

    added, removed, files = 0, 0, 0
    if result.returncode == 0 and result.stdout.strip():
        m_files = re.search(r"(\d+) file", result.stdout)
        m_add = re.search(r"(\d+) insertion", result.stdout)
        m_del = re.search(r"(\d+) deletion", result.stdout)
        if m_files:
            files = int(m_files.group(1))
        if m_add:
            added = int(m_add.group(1))
        if m_del:
            removed = int(m_del.group(1))
    return {"linesAdded": added, "linesRemoved": removed, "filesChanged": files}


async def _get_changed_files_vs_main(
    worktree_path: str, *, base_ref: str | None = None
) -> list[str]:
    """Get list of files changed by the agent (not the entire feature branch).

    Args:
        worktree_path: Path to the agent's git worktree.
        base_ref: Explicit base ref (e.g. the pipeline branch name) to diff
            against.  See :func:`_get_diff_vs_main` for why this is preferred
            over the ``--not --remotes`` heuristic.
    """
    # ── Fast path: explicit base ref ──────────────────────────────────
    if base_ref is not None:
        verify = await async_subprocess(
            ["git", "rev-parse", "--verify", base_ref],
            cwd=worktree_path,
        )
        if verify.returncode == 0:
            merge_base = await async_subprocess(
                ["git", "merge-base", base_ref, "HEAD"],
                cwd=worktree_path,
            )
            if merge_base.returncode == 0 and merge_base.stdout.strip():
                result = await async_subprocess(
                    ["git", "diff", "--name-only", merge_base.stdout.strip(), "HEAD"],
                    cwd=worktree_path,
                )
                return [f for f in result.stdout.strip().split("\n") if f.strip()]
            logger.warning(
                "_get_changed_files_vs_main: merge-base for %r not found in %s — "
                "falling back to commit-count heuristic",
                base_ref,
                worktree_path,
            )
        logger.warning(
            "_get_changed_files_vs_main: base_ref %r not found in %s — "
            "falling back to commit-count heuristic",
            base_ref,
            worktree_path,
        )

    # ── Fallback: commit-count heuristic ──────────────────────────────
    count_result = await async_subprocess(
        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
        cwd=worktree_path,
    )
    try:
        commit_count = int(count_result.stdout.strip())
        if commit_count <= 0:
            commit_count = 1
    except (ValueError, AttributeError):
        commit_count = 1

    heuristic_ref = f"HEAD~{commit_count}"
    verify = await async_subprocess(
        ["git", "rev-parse", "--verify", heuristic_ref],
        cwd=worktree_path,
    )

    if verify.returncode == 0:
        result = await async_subprocess(
            ["git", "diff", "--name-only", heuristic_ref, "HEAD"],
            cwd=worktree_path,
        )
    else:
        # Root commit: diff against empty tree
        empty_tree_result = await async_subprocess(
            ["git", "hash-object", "-t", "tree", "/dev/null"],
            cwd=worktree_path,
        )
        empty_tree = empty_tree_result.stdout.strip()
        result = await async_subprocess(
            ["git", "diff", "--name-only", empty_tree, "HEAD"],
            cwd=worktree_path,
        )

    return [f for f in result.stdout.strip().split("\n") if f.strip()]


def _load_conventions_md(project_dir: str) -> str | None:
    """Read ``.forge/conventions.md`` from the project directory.

    Returns the stripped file content, or ``None`` if the file doesn't
    exist or is empty.
    """
    filepath = os.path.join(project_dir, ".forge", "conventions.md")
    try:
        with open(filepath, encoding="utf-8") as fh:
            content = fh.read().strip()
            return content if content else None
    except (OSError, FileNotFoundError):
        return None


async def _extract_implementation_summary(
    worktree_path: str,
    agent_summary: str,
    pipeline_branch: str | None = None,
) -> str:
    """Extract a brief (≤300 char) summary from completed agent work.

    Combines git commit messages with the agent's summary text to produce
    a concise description of what was implemented.  Falls back to a generic
    message when no useful information is available.
    """
    commit_messages: list[str] = []

    # Try explicit base-ref first (most accurate)
    if pipeline_branch is not None:
        verify = await async_subprocess(
            ["git", "rev-parse", "--verify", pipeline_branch],
            cwd=worktree_path,
        )
        if verify.returncode == 0:
            result = await async_subprocess(
                ["git", "log", "--format=%s", f"{pipeline_branch}..HEAD"],
                cwd=worktree_path,
            )
            if result.returncode == 0 and result.stdout.strip():
                commit_messages = [
                    line.strip() for line in result.stdout.strip().splitlines() if line.strip()
                ]

    # Fallback: recent local-only commits
    if not commit_messages:
        result = await async_subprocess(
            ["git", "log", "--format=%s", "--not", "--remotes", "-5"],
            cwd=worktree_path,
        )
        if result.returncode == 0 and result.stdout.strip():
            commit_messages = [
                line.strip() for line in result.stdout.strip().splitlines() if line.strip()
            ]

    parts: list[str] = []
    if commit_messages:
        parts.append("; ".join(commit_messages))

    # Only include agent summary if it's not generic
    if agent_summary and agent_summary.strip().lower() != "task completed":
        parts.append(agent_summary.strip())

    if parts:
        summary = " | ".join(parts)
        return summary[:300]

    return "Task completed (no detailed summary available)"[:300]


def _print_status_table(tasks) -> None:
    table = Table(title="Forge Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("State")
    table.add_column("Agent")
    table.add_column("Retries")

    state_colors = {
        "todo": "white",
        "in_progress": "yellow",
        "in_review": "blue",
        "merging": "magenta",
        "done": "green",
        "error": "red",
        "cancelled": "dim",
    }

    for t in tasks:
        color = state_colors.get(t.state, "white")
        table.add_row(
            t.id,
            t.title,
            f"[{color}]{t.state}[/{color}]",
            t.assigned_agent or "-",
            str(t.retry_count),
        )

    console.print(table)


def _is_pytest_cmd(cmd: str) -> bool:
    """Check if a test command is pytest-based (can be scoped to specific files)."""
    return "pytest" in cmd.lower()


async def _find_related_test_files(
    worktree_path: str,
    changed_files: list[str],
    *,
    allowed_files: list[str] | None = None,
    base_ref: str | None = None,
) -> list[str] | tuple[list[str], list[str]]:
    """Find test files related to the changed source files.

    Handles two common Python test naming conventions:
    - Co-located: ``foo.py`` → ``foo_test.py`` (same directory)
    - Test directory: ``src/foo.py`` → ``tests/test_foo.py``

    Changed files that ARE test files are included directly.

    When *allowed_files* is provided, returns ``(in_scope, out_of_scope)``
    tuple. A test is in-scope if it appears in *allowed_files* OR was
    newly created (not on *base_ref*).

    When *allowed_files* is None (default), returns a flat list for backward compat.
    """
    test_files: set[str] = set()
    for f in changed_files:
        if not f.endswith(".py"):
            continue
        basename = os.path.basename(f)

        if basename.startswith("test_") or basename.endswith("_test.py"):
            if os.path.isfile(os.path.join(worktree_path, f)):
                test_files.add(f)
            continue

        co_located = f"{f[:-3]}_test.py"
        if os.path.isfile(os.path.join(worktree_path, co_located)):
            test_files.add(co_located)

        dirname = os.path.dirname(f)
        test_dir_path = os.path.join(dirname, "tests", f"test_{basename}")
        if os.path.isfile(os.path.join(worktree_path, test_dir_path)):
            test_files.add(test_dir_path)

        root_test_path = os.path.join("tests", f"test_{basename}")
        if os.path.isfile(os.path.join(worktree_path, root_test_path)):
            test_files.add(root_test_path)

    all_tests = sorted(test_files)

    if allowed_files is None:
        return all_tests

    allowed_set = set(allowed_files)

    new_files: set[str] = set()
    if base_ref:
        try:
            result = await async_subprocess(
                ["git", "diff", "--name-only", "--diff-filter=A", f"{base_ref}...HEAD"],
                cwd=worktree_path,
                timeout=10,
            )
            if result.returncode == 0:
                new_files = set(result.stdout.strip().splitlines())
        except Exception:
            logger.warning("Failed to detect newly created files for scope filtering")

    in_scope: list[str] = []
    out_of_scope: list[str] = []
    for tf in all_tests:
        if tf in allowed_set or tf in new_files:
            in_scope.append(tf)
        else:
            out_of_scope.append(tf)

    return in_scope, out_of_scope


def compute_worktree_path(
    workspace_dir: str,
    repo_id: str,
    task_id: str,
    *,
    repo_count: int = 1,
) -> str:
    """Compute worktree path for a task.

    Single-repo (repo_count=1, repo_id='default'): <workspace_dir>/.forge/worktrees/<task_id>
    Multi-repo (repo_count > 1): <workspace_dir>/.forge/worktrees/<repo_id>/<task_id>
    """
    validate_task_id(task_id)
    if repo_count <= 1 and repo_id == "default":
        return os.path.join(workspace_dir, ".forge", "worktrees", task_id)
    validate_repo_id(repo_id)
    return os.path.join(workspace_dir, ".forge", "worktrees", repo_id, task_id)


async def _run_git(
    args: list[str],
    cwd: str,
    *,
    check: bool = True,
    description: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run a git command with consistent logging and error handling.

    Args:
        args: Git arguments (e.g. ["rev-parse", "HEAD"]).
        cwd: Working directory.
        check: If True (default), raise on non-zero exit. If False, log
            a warning and return the result.
        description: Human-readable description for log messages.
    """
    cmd = ["git"] + args
    result = await async_subprocess(cmd, cwd=cwd)
    desc = description or " ".join(args[:3])
    if result.returncode != 0:
        if check:
            logger.error(
                "git %s failed (exit %d) in %s: %s",
                desc,
                result.returncode,
                cwd,
                result.stderr.strip(),
            )
            raise subprocess.CalledProcessError(
                result.returncode,
                cmd,
                result.stdout,
                result.stderr,
            )
        else:
            logger.warning(
                "git %s returned %d in %s: %s",
                desc,
                result.returncode,
                cwd,
                result.stderr.strip(),
            )
    return result
