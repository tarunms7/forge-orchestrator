"""Safety boundaries and stage-specific tool policies.

Defines the denied operations list and pre-built ToolPolicy instances
for each pipeline stage (planner, contract_builder, agent, reviewer).
"""

from __future__ import annotations

from forge.providers.base import ToolPolicy

# ---------------------------------------------------------------------------
# Denied operations — Forge syntax patterns
# ---------------------------------------------------------------------------

AGENT_DENIED_OPERATIONS: list[str] = [
    # Git operations that could damage the repo
    "git:push",
    "git:rebase",
    "git:checkout",
    "git:reset_hard",
    "git:branch_delete",
    "git:merge",
    "git:clean",
    "git:stash",
    "git:cherry_pick",
    "git:tag",
    "git:remote",
    # Network access
    "net:curl",
    "net:wget",
    "net:ssh",
    "net:scp",
    "net:rsync",
    "net:nc",
    "net:telnet",
    "net:ftp",
    # Privilege escalation
    "priv:sudo",
    "priv:su",
    "priv:doas",
    # Permission changes
    "perm:chmod",
    "perm:chown",
    "perm:chgrp",
    # Process control
    "proc:kill",
    "proc:pkill",
    "proc:killall",
    # Container operations
    "container:docker",
    "container:podman",
    # System services
    "sys:systemctl",
    "sys:service",
    "sys:mount",
    "sys:umount",
    # Environment manipulation
    "env:export",
    "env:unset",
    # Sensitive file access
    "file:read_dotenv",
]


# ---------------------------------------------------------------------------
# Stage-specific ToolPolicy instances
# ---------------------------------------------------------------------------

PLANNER_TOOL_POLICY = ToolPolicy(
    mode="allowlist",
    allowed_tools=["Read", "Glob", "Grep", "Bash"],
    denied_operations=[],
)

CONTRACT_TOOL_POLICY = ToolPolicy(
    mode="allowlist",
    allowed_tools=["Read", "Glob", "Grep"],
    denied_operations=[],
)

# ---------------------------------------------------------------------------
# Collaboration tools (injected via system prompt, NOT as Claude SDK tools)
# ---------------------------------------------------------------------------
# The following tools are made available to agents through the system prompt's
# dependency context section rather than through the Claude SDK tool mechanism:
#
#   - ask_prior_agent: Queries the AgentCollaborationBroker for upstream task
#     context (implementation summaries, key decisions, targeted diffs).
#     See forge/agents/tools/ask_agent.py for implementation.
#
# Because AGENT_TOOL_POLICY uses denylist mode, these prompt-injected tools
# do not need to be explicitly allowed here.
# ---------------------------------------------------------------------------

AGENT_TOOL_POLICY = ToolPolicy(
    mode="denylist",
    allowed_tools=[],
    denied_operations=list(AGENT_DENIED_OPERATIONS),
)

REVIEWER_TOOL_POLICY = ToolPolicy(
    mode="allowlist",
    allowed_tools=["Read", "Glob", "Grep", "Bash"],
    denied_operations=[],
)
