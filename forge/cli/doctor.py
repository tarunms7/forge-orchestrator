"""Forge CLI doctor command. Checks environment health and prints diagnostics."""

from __future__ import annotations

import os
import shutil
import sqlite3
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


def _check_db_connectivity() -> tuple[str, str, str]:
    """Check database stack (aiosqlite + sqlalchemy async) with in-memory SQLite.

    Uses synchronous sqlite3 to avoid event-loop side-effects in test envs.
    """
    try:
        import aiosqlite  # noqa: F401 — verify importable
        from sqlalchemy.ext.asyncio import create_async_engine  # noqa: F401

        conn = sqlite3.connect(":memory:")
        try:
            row = conn.execute("SELECT 1").fetchone()
            if row and row[0] == 1:
                return "ok", "Database", "aiosqlite + sqlalchemy OK"
            return "fail", "Database", f"unexpected result: {row}"
        finally:
            conn.close()
    except ImportError as exc:
        return "fail", "Database", f"missing dependency: {exc}"
    except Exception as exc:
        return "fail", "Database", f"connection failed: {exc}"


def _check_central_data_dir() -> tuple[str, str, str]:
    """Check that the central Forge data directory exists and is writable."""
    from forge.core.paths import forge_data_dir

    try:
        data_dir = forge_data_dir()
        if not os.path.isdir(data_dir):
            return "fail", "Central data dir", f"not found: {data_dir}"
        if not os.access(data_dir, os.W_OK):
            return "fail", "Central data dir", f"not writable: {data_dir}"
        return "ok", "Central data dir", data_dir
    except Exception as exc:
        return "fail", "Central data dir", f"error: {exc}"


def _check_central_db() -> tuple[str, str, str]:
    """Check that the central SQLite database is accessible."""
    from forge.core.paths import forge_db_path

    db_path = forge_db_path()
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT 1").fetchone()
            if row and row[0] == 1:
                return "ok", "Central DB", db_path
            return "fail", "Central DB", f"query returned unexpected result at {db_path}"
        finally:
            conn.close()
    except Exception as exc:
        return "fail", "Central DB", f"cannot connect: {exc}"


def _check_provider_health() -> list[tuple[str, str, str]]:
    """Run provider health checks via ProviderRegistry.preflight_all().

    Returns a list of (status, label, detail) tuples — one per provider.
    """
    results: list[tuple[str, str, str]] = []
    try:
        from forge.config.settings import ForgeSettings
        from forge.providers.claude import ClaudeProvider
        from forge.providers.registry import ProviderRegistry

        settings = ForgeSettings()
        registry = ProviderRegistry(settings)
        registry.register(ClaudeProvider())

        health = registry.preflight_all()
        for name, status in health.items():
            if status.healthy:
                results.append(("ok", f"Provider: {name}", status.details))
            else:
                detail = "; ".join(status.errors) if status.errors else "unhealthy"
                results.append(("fail", f"Provider: {name}", detail))
    except Exception as exc:
        results.append(("warn", "Provider health", f"could not check: {exc}"))
    return results


def _check_observed_health() -> list[tuple[str, str, str]]:
    """Surface warnings from observed health_state.json."""
    import json

    results: list[tuple[str, str, str]] = []
    health_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "providers",
        "health_state.json",
    )
    if not os.path.isfile(health_path):
        return results
    try:
        with open(health_path, encoding="utf-8") as f:
            data = json.load(f)
        for item in data if isinstance(data, list) else []:
            failing = item.get("stages_failing", [])
            if failing:
                spec = item.get("spec", "unknown")
                results.append(
                    ("warn", f"Observed health: {spec}", f"failing stages: {', '.join(failing)}")
                )
    except Exception:
        pass
    return results


def _check_experimental_models() -> list[tuple[str, str, str]]:
    """Warn about experimental models in the routing config."""
    results: list[tuple[str, str, str]] = []
    try:
        from forge.config.settings import ForgeSettings

        settings = ForgeSettings()
        overrides = settings.build_routing_overrides()
        from forge.providers.base import ModelSpec
        from forge.providers.catalog import FORGE_MODEL_CATALOG

        experimental = {
            f"{e.provider}:{e.alias}" for e in FORGE_MODEL_CATALOG if e.tier == "experimental"
        }
        for stage, value in overrides.items():
            spec = ModelSpec.parse(value)
            spec_str = str(spec)
            if spec_str in experimental:
                results.append(
                    ("warn", f"Experimental model: {spec_str}", f"configured for {stage}")
                )
    except Exception:
        pass
    return results


def _web_extras_installed() -> bool:
    """Check if web extras (fastapi, uvicorn, etc.) are installed."""
    try:
        import fastapi  # noqa: F401

        return True
    except ImportError:
        return False


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
        _check_disk_space(),
        _check_db_connectivity(),
        _check_central_data_dir(),
        _check_central_db(),
    ]
    if _web_extras_installed():
        checks.insert(4, _check_node_version())
        checks.insert(5, _check_node_npm())
        checks.insert(6, _check_jwt_secret())

    # Provider health checks
    checks.extend(_check_provider_health())
    checks.extend(_check_observed_health())
    checks.extend(_check_experimental_models())

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
