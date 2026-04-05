"""Safety auditor for enforcing tool policies during provider execution.

Checks every TOOL_USE event against the active ToolPolicy and workspace
boundaries before allowing execution.
"""

from __future__ import annotations

import logging
import os
from dataclasses import field as dataclass_field

from forge.providers.base import (
    AuditVerdict,
    EventKind,
    ProviderEvent,
    SafetyViolation,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.providers.catalog import CLAUDE_TOOL_MAP, CODEX_TOOL_MAP, CoreTool

logger = logging.getLogger("forge.providers.safety_auditor")

# Merge both tool maps for normalization
_ALL_TOOL_MAPS: dict[str, CoreTool] = {**CLAUDE_TOOL_MAP, **CODEX_TOOL_MAP}

# Forge syntax pattern -> shell command substrings
_PATTERN_TO_COMMANDS: dict[str, list[str]] = {
    "git:push": ["git push"],
    "git:rebase": ["git rebase"],
    "git:checkout": ["git checkout"],
    "git:reset_hard": ["git reset --hard"],
    "git:branch_delete": ["git branch -d", "git branch -D", "git branch --delete"],
    "git:merge": ["git merge"],
    "git:clean": ["git clean"],
    "git:stash": ["git stash"],
    "git:cherry_pick": ["git cherry-pick"],
    "git:tag": ["git tag"],
    "git:remote": ["git remote"],
    "net:curl": ["curl"],
    "net:wget": ["wget"],
    "net:ssh": ["ssh "],
    "net:scp": ["scp "],
    "net:rsync": ["rsync"],
    "net:nc": ["nc ", "netcat"],
    "net:telnet": ["telnet"],
    "net:ftp": ["ftp "],
    "priv:sudo": ["sudo "],
    "priv:su": ["su "],
    "priv:doas": ["doas "],
    "perm:chmod": ["chmod "],
    "perm:chown": ["chown "],
    "perm:chgrp": ["chgrp "],
    "proc:kill": ["kill "],
    "proc:pkill": ["pkill "],
    "proc:killall": ["killall "],
    "container:docker": ["docker "],
    "container:podman": ["podman "],
    "sys:systemctl": ["systemctl "],
    "sys:service": ["service "],
    "sys:mount": ["mount "],
    "sys:umount": ["umount "],
    "env:export": ["export "],
    "env:unset": ["unset "],
    "file:read_dotenv": [".env"],
}


class SafetyAuditor:
    """Enforces tool policies and workspace boundaries on provider events."""

    def __init__(self, policy: ToolPolicy, workspace: WorkspaceRoots) -> None:
        self.policy = policy
        self.workspace = workspace
        self.violations: list[SafetyViolation] = []

    def check(self, event: ProviderEvent) -> AuditVerdict:
        """Check a provider event against the active policy.

        Only TOOL_USE events are checked. All other events return ALLOW.
        """
        if event.kind != EventKind.TOOL_USE:
            return AuditVerdict.ALLOW

        tool_name = event.tool_name or ""
        tool_input = event.tool_input or ""

        # Normalize tool name to CoreTool
        core_tool = _ALL_TOOL_MAPS.get(tool_name, CoreTool.UNKNOWN)

        # Check workspace boundaries for write operations
        if core_tool in (CoreTool.WRITE, CoreTool.EDIT):
            if self._is_read_only(tool_input):
                violation = SafetyViolation(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    denied_pattern="read_only_dir",
                    verdict=AuditVerdict.ABORT,
                    reason=f"Write to read-only directory: {tool_input}",
                )
                self.violations.append(violation)
                logger.warning("Safety ABORT: %s", violation.reason)
                return AuditVerdict.ABORT

        # Policy mode checks
        if self.policy.mode == "unrestricted":
            return AuditVerdict.ALLOW

        if self.policy.mode == "allowlist":
            if tool_name not in self.policy.allowed_tools:
                violation = SafetyViolation(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    denied_pattern="allowlist",
                    verdict=AuditVerdict.ABORT,
                    reason=f"Tool {tool_name!r} not in allowlist",
                )
                self.violations.append(violation)
                logger.warning("Safety ABORT: %s", violation.reason)
                return AuditVerdict.ABORT
            return AuditVerdict.ALLOW

        if self.policy.mode == "denylist":
            # Check if tool_input matches any denied operation
            for pattern in self.policy.denied_operations:
                if self._matches(pattern, tool_name, tool_input, core_tool):
                    violation = SafetyViolation(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        denied_pattern=pattern,
                        verdict=AuditVerdict.ABORT,
                        reason=f"Denied operation {pattern!r} matched",
                    )
                    self.violations.append(violation)
                    logger.warning("Safety ABORT: %s", violation.reason)
                    return AuditVerdict.ABORT
            return AuditVerdict.ALLOW

        return AuditVerdict.ALLOW

    def _matches(
        self,
        pattern: str,
        tool_name: str,
        tool_input: str,
        core_tool: CoreTool,
    ) -> bool:
        """Check if a denied operation pattern matches the tool use.

        Supports Forge syntax patterns like 'git:push', 'net:curl',
        and Bash(cmd) patterns.
        """
        # Look up command substrings for the Forge pattern
        commands = _PATTERN_TO_COMMANDS.get(pattern)
        if commands is None:
            return False

        # For Bash tools, check if the command input contains the pattern
        if core_tool == CoreTool.BASH:
            input_lower = tool_input.lower()
            return any(cmd.lower() in input_lower for cmd in commands)

        # For file:read_dotenv, also check Read tool
        if pattern == "file:read_dotenv" and core_tool == CoreTool.READ:
            return any(cmd in tool_input for cmd in commands)

        return False

    def _is_read_only(self, tool_input: str) -> bool:
        """Check if a path targets a read-only directory."""
        if not self.workspace.read_only_dirs:
            return False

        # Try to extract a file path from the tool input
        # tool_input may be JSON or a raw path
        path = self._extract_path(tool_input)
        if not path:
            return False

        try:
            real_path = os.path.realpath(path)
        except (OSError, ValueError):
            return False

        for ro_dir in self.workspace.read_only_dirs:
            try:
                real_ro = os.path.realpath(ro_dir)
            except (OSError, ValueError):
                continue
            if real_path.startswith(real_ro + os.sep) or real_path == real_ro:
                return True

        return False

    @staticmethod
    def _extract_path(tool_input: str) -> str | None:
        """Extract a file path from tool input (JSON or raw)."""
        import json as _json

        try:
            data = _json.loads(tool_input)
            if isinstance(data, dict):
                return data.get("file_path") or data.get("path") or data.get("file")
        except (ValueError, TypeError):
            pass

        # Treat as raw path if it looks like one
        stripped = tool_input.strip()
        if stripped.startswith("/") or stripped.startswith("./"):
            return stripped

        return None
