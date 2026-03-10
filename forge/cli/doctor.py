"""Forge CLI doctor command. Checks environment health and prints diagnostics."""

import asyncio
import os
import shutil
import subprocess
import sys

import click
from rich.console import Console
from rich.table import Table


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


def _parse_node_version(raw: str) -> tuple[int, ...]:
    """Extract version tuple from 'vX.Y.Z' node --version output."""
    text = raw.strip().lstrip("v")
    parts = text.split(".")
    nums: list[int] = []
    for p in parts:
        if p.isdigit():
            nums.append(int(p))
        else:
            break
    if nums:
        return tuple(nums)
    return (0,)


def _check_node_version() -> tuple[str, str, str]:
    """Check Node.js >= 18 via subprocess, parsing semver from 'node --version'."""
    if not shutil.which("node"):
        return "warn", "Node.js version", "not installed — Web UI won't work"
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "warn", "Node.js version", "could not determine version"
        raw = result.stdout.strip()
        if not raw.startswith("v"):
            return "warn", "Node.js version", "could not determine version"
        version_tuple = _parse_node_version(raw)
        version_str = ".".join(str(v) for v in version_tuple)
        if len(version_tuple) >= 1 and version_tuple[0] >= 18:
            return "ok", "Node.js version", version_str
        return "fail", "Node.js version", f"{version_str} (requires >= 18)"
    except FileNotFoundError:
        return "warn", "Node.js version", "not installed — Web UI won't work"
    except subprocess.TimeoutExpired:
        return "fail", "Node.js version", "timed out"


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


async def _async_db_check() -> tuple[str, str, str]:
    """Run an async SELECT 1 via aiosqlite + sqlalchemy to verify DB stack."""
    import aiosqlite  # noqa: F401 — verify importable
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            row = result.scalar()
            if row == 1:
                return "ok", "Database", "aiosqlite + sqlalchemy OK"
            return "fail", "Database", f"unexpected result: {row}"
    finally:
        await engine.dispose()


def _check_db_connectivity() -> tuple[str, str, str]:
    """Check database stack (aiosqlite + sqlalchemy async) with in-memory SQLite."""
    try:
        return asyncio.run(_async_db_check())
    except ImportError as exc:
        return "fail", "Database", f"missing dependency: {exc}"
    except Exception as exc:
        return "fail", "Database", f"connection failed: {exc}"


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
        _check_node_version(),
        _check_node_npm(),
        _check_jwt_secret(),
        _check_disk_space(),
        _check_db_connectivity(),
    ]

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Status", width=3, justify="center")
    table.add_column("Check", min_width=20)
    table.add_column("Detail")

    has_failures = False
    for status, label, detail in checks:
        icon = _STATUS_ICONS[status]
        color = _STATUS_COLORS[status]
        table.add_row(icon, f"[{color}]{label}[/{color}]", detail)
        if status == "fail":
            has_failures = True

    console.print(table)
    console.print()
    if has_failures:
        console.print("[red]Some checks failed. Please fix the issues above.[/red]")
        raise SystemExit(1)
    else:
        console.print("[green]All checks passed![/green]")
