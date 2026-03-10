"""Forge CLI doctor command. Checks environment health and prints diagnostics."""

import os
import shutil
import subprocess
import sys

import click
from rich.console import Console


def _check_python() -> tuple[str, str, str]:
    """Check Python >= 3.12. Returns (status, label, detail)."""
    vi = sys.version_info
    version = f"{vi.major}.{vi.minor}.{vi.micro}"
    if (vi.major, vi.minor) >= (3, 12):
        return "ok", "Python", version
    return "fail", "Python", f"{version} (requires >= 3.12)"


def _parse_git_version(raw: str) -> tuple[int, ...]:
    """Extract version tuple from 'git version X.Y.Z ...' output."""
    # Typical output: "git version 2.39.3 (Apple Git-146)"
    for token in raw.strip().split():
        parts = token.split(".")
        if len(parts) >= 2 and parts[0].isdigit():
            nums: list[int] = []
            for p in parts:
                if p.isdigit():
                    nums.append(int(p))
                else:
                    break
            if len(nums) >= 2:
                return tuple(nums)
    return (0,)


def _check_git() -> tuple[str, str, str]:
    """Check Git >= 2.20 via subprocess."""
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "fail", "Git", "not found or error"
        version_tuple = _parse_git_version(result.stdout)
        version_str = ".".join(str(v) for v in version_tuple)
        if version_tuple >= (2, 20):
            return "ok", "Git", version_str
        return "fail", "Git", f"{version_str} (requires >= 2.20)"
    except FileNotFoundError:
        return "fail", "Git", "not installed"
    except subprocess.TimeoutExpired:
        return "fail", "Git", "timed out"


def _check_claude_cli() -> tuple[str, str, str]:
    """Check claude CLI on PATH and ~/.claude auth directory."""
    path = shutil.which("claude")
    if not path:
        return "fail", "Claude CLI", "not found on PATH"
    claude_dir = os.path.expanduser("~/.claude")
    if not os.path.isdir(claude_dir):
        return "warn", "Claude CLI", "found, but ~/.claude not found (run 'claude login')"
    return "ok", "Claude CLI", "installed and authenticated"


def _check_gh() -> tuple[str, str, str]:
    """Check gh CLI availability."""
    path = shutil.which("gh")
    if not path:
        return "warn", "GitHub CLI (gh)", "not found — PR creation won't work"
    return "ok", "GitHub CLI (gh)", "installed"


def _check_node_npm() -> tuple[str, str, str]:
    """Check node and npm availability."""
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node and not npm:
        return "warn", "Node/npm", "not found — Web UI won't work"
    if not node:
        return "warn", "Node", "not found — Web UI won't work"
    if not npm:
        return "warn", "npm", "not found — Web UI won't work"
    return "ok", "Node/npm", "installed"


def _check_jwt_secret() -> tuple[str, str, str]:
    """Check FORGE_JWT_SECRET env var."""
    val = os.environ.get("FORGE_JWT_SECRET")
    if not val:
        return "warn", "FORGE_JWT_SECRET", "not set (will use random secret)"
    return "ok", "FORGE_JWT_SECRET", "set"


def _check_disk_space() -> tuple[str, str, str]:
    """Check at least 5 GB free disk space."""
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024**3)
        if free_gb >= 5:
            return "ok", "Disk space", f"{free_gb:.1f} GB free"
        return "fail", "Disk space", f"{free_gb:.1f} GB free (requires >= 5 GB)"
    except OSError as exc:
        return "fail", "Disk space", f"could not check ({exc})"


_STATUS_ICONS = {
    "ok": "[green]✓[/green]",
    "warn": "[yellow]⚠[/yellow]",
    "fail": "[red]✗[/red]",
}

_STATUS_COLORS = {
    "ok": "green",
    "warn": "yellow",
    "fail": "red",
}


@click.command("doctor")
def doctor() -> None:
    """Check environment health and print diagnostics."""
    console = Console()
    console.print("\n[bold]Forge Doctor[/bold]\n")

    checks = [
        _check_python(),
        _check_git(),
        _check_claude_cli(),
        _check_gh(),
        _check_node_npm(),
        _check_jwt_secret(),
        _check_disk_space(),
    ]

    has_failures = False
    for status, label, detail in checks:
        icon = _STATUS_ICONS[status]
        color = _STATUS_COLORS[status]
        console.print(f"  {icon}  [{color}]{label}[/{color}]: {detail}")
        if status == "fail":
            has_failures = True

    console.print()
    if has_failures:
        console.print("[red]Some checks failed. Please fix the issues above.[/red]")
        raise SystemExit(1)
    else:
        console.print("[green]All checks passed![/green]")
