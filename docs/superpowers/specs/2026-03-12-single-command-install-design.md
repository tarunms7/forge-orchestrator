# Single-Command Install — Design Spec

**Date:** 2026-03-12
**Status:** Approved
**Goal:** Install Forge with one command: `curl -fsSL <url> | sh`

## Problem

Current install requires 5+ manual steps: clone repo, create venv, pip install, npm install, build frontend, symlink. Most users only need the TUI.

## Solution

Rewrite `install.sh` to use `uv` (Astral) for Python toolchain management and PyPI for package distribution. Publish `forge-orchestrator` to PyPI with split dependencies (TUI core vs web extras).

## Pre-Implementation Check

Verify `forge-orchestrator` is available on PyPI before starting. If taken, use an alternative name (e.g. `forge-engine`, `forgeai`).

## User Journey

```
$ curl -fsSL https://raw.githubusercontent.com/tarunms7/forge-orchestrator/main/install.sh | sh

[1/4] Installing uv...
  ✓ uv v0.7.x installed

[2/4] Installing Forge...
  ✓ forge-orchestrator 0.1.0 installed (Python 3.12 auto-provisioned)

[3/4] Verifying tools...
  ✓ git 2.39.3
  ✓ Claude CLI installed
  ⚠ GitHub CLI (gh) not found — PR creation won't work
    Install: https://cli.github.com

[4/4] Ready!
  Run:  forge tui        Launch the terminal UI
        forge doctor     Full health check
        forge --help     All commands

  Note: If 'forge' is not found, open a new terminal or run:
        source ~/.bashrc  (or ~/.zshrc)
```

Alternative install paths (also work after PyPI publish):
- `pipx install forge-orchestrator`
- `uv tool install forge-orchestrator`
- `pip install forge-orchestrator` (inside any venv)

Uninstall: `uv tool uninstall forge-orchestrator`

## Deliverables

### 1. Rewrite `install.sh`

Replace the current 358-line script with ~80 lines. Four steps:

**Step 1 — Install uv (skip if present)**
- Detect via `command -v uv`
- Install using Astral's official one-liner: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Source `$HOME/.local/bin/env` (uv's env file) so `uv` is available in the current subshell
- After install, verify `uv --version` succeeds; abort if not

**Limitation:** `curl | sh` runs in a subshell. The uv installer adds PATH entries to `~/.bashrc`/`~/.zshrc`, but the user's current terminal won't pick them up until they open a new terminal or source their profile. The quickstart message in Step 4 must mention this.

**Step 2 — Install Forge via uv**
- Detection logic: `uv tool list 2>/dev/null | grep -q forge-orchestrator`
  - If found: `uv tool upgrade forge-orchestrator`
  - If not found: `uv tool install forge-orchestrator`
- `uv` auto-provisions Python 3.12 if the system doesn't have it (managed toolchain)
- `uv` creates an isolated venv and puts `forge` on PATH automatically

**Step 3 — Verify tools (non-blocking)**
- Check: `git` (required), `claude` CLI (required for agents), `gh` CLI (optional, for PRs)
- Print ✓ for present, ⚠ with install link for missing
- Never exit on missing tools — warnings only

**Step 4 — Print quickstart**
- Show `forge tui`, `forge doctor`, `forge --help`
- Print note about opening a new terminal if `forge` is not found

**Removed from current script:**
- Python version check + install (uv handles it)
- Node.js check + install (not needed for TUI)
- Venv creation (uv handles it)
- Git clone of repo (installs from PyPI)
- Frontend npm install + build (not needed for TUI)
- Symlink creation + PATH detection (uv handles it)

**Script structure:**
```sh
#!/bin/sh
set -e

# Color helpers (same as current)
# Step 1: install uv (detect, install, source, verify)
# Step 2: uv tool install/upgrade forge-orchestrator
# Step 3: verify git, claude, gh
# Step 4: print quickstart + new-terminal note
```

**Idempotent:** Safe to run multiple times. Re-running upgrades Forge if a new version is on PyPI.

### 2. Update `pyproject.toml` for PyPI

**Add required metadata:**
```toml
[project]
name = "forge-orchestrator"
version = "0.1.0"
description = "Hybrid multi-agent orchestration engine"
requires-python = ">=3.12"
license = {text = "MIT"}
readme = "README.md"
authors = [{name = "Tarun MS"}]
keywords = ["ai", "agents", "orchestration", "claude", "tui"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Build Tools",
]

[project.urls]
Homepage = "https://github.com/tarunms7/forge-orchestrator"
Repository = "https://github.com/tarunms7/forge-orchestrator"
Issues = "https://github.com/tarunms7/forge-orchestrator/issues"
```

**Split dependencies — TUI core vs web extras:**

Core (what every user gets):
```toml
dependencies = [
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
    "psutil>=5.9",
    "click>=8.1",
    "rich>=13.0",
    "textual>=0.50",
    "claude-code-sdk>=0.0.25",
    "httpx>=0.27",
]
```

Note: `httpx` is added to core because `forge/tui/app.py` uses it in `detect_server()`.

Web extras (only for `forge serve` users):
```toml
[project.optional-dependencies]
web = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "python-jose[cryptography]>=3.3",
    "bcrypt>=4.0",
    "python-multipart>=0.0.9",
]
```

Install web UI: `pip install forge-orchestrator[web]`

Existing extras unchanged:
```toml
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.3"]
postgres = ["asyncpg>=0.29"]
remote = ["asyncssh>=2.14"]
```

Note: `httpx` removed from dev (now in core).

**Exclude test files from wheel:**
```toml
[tool.hatch.build.targets.wheel]
packages = ["forge"]
exclude = ["*_test.py", "**/*_test.py"]
```

Note: `web/` exclusion is unnecessary since `packages = ["forge"]` already limits the wheel to the `forge/` directory only.

### 3. Lazy Import Guards

Two files need import guards to avoid `ImportError` on TUI-only installs:

#### 3a. `forge/cli/main.py` — guard `serve` command

```python
@cli.command()
@click.option(...)
def serve(port, host, db_url, jwt_secret, build_frontend):
    """Start the Forge web server."""
    try:
        import uvicorn
        from forge.api.app import create_app
    except ImportError:
        click.echo(
            "Web UI requires additional dependencies.\n"
            "Install them with: pip install forge-orchestrator[web]\n\n"
            "Note: 'forge serve' also requires a git clone of the repository\n"
            "for the Next.js frontend. See: https://github.com/tarunms7/forge-orchestrator"
        )
        raise SystemExit(1)
    # ... rest of serve logic unchanged
```

Note: `serve` also calculates `repo_root` and `web_dir` relative to `__file__`, which resolves into site-packages when installed from PyPI (not a git clone). The import guard message documents this limitation. Fixing the path resolution is out of scope for this spec — `forge serve` is a git-clone-only feature for now.

#### 3b. `forge/storage/db.py` — guard `bcrypt` import

`db.py` has `import bcrypt` at the top level. This module is imported by `forge/core/daemon.py` (top-level), which is imported by `forge tui` when starting a pipeline. With `bcrypt` moved to web extras, this would crash TUI-only installs at runtime.

Fix: make the bcrypt import lazy — only used by `create_user()` and `verify_password()`, which are web-only code paths.

```python
# At the top of db.py, REMOVE:
import bcrypt

# In create_user() and verify_password(), ADD:
def create_user(self, ...):
    try:
        import bcrypt
    except ImportError:
        raise ImportError(
            "bcrypt is required for user management. "
            "Install with: pip install forge-orchestrator[web]"
        )
    # ... existing bcrypt usage
```

All other commands (`tui`, `run`, `doctor`, `init`, `status`, `logs`, `clean`, `fix`, `ping`) use only core deps and work without web extras.

### 4. Update `forge doctor` for TUI-only installs

When web extras are not installed, suppress Node.js/npm checks to avoid confusing TUI-only users:

```python
# In doctor.py, detect web extras:
def _web_extras_installed() -> bool:
    try:
        import fastapi  # noqa: F401
        return True
    except ImportError:
        return False

# In doctor(), conditionally include Node checks:
checks = [
    _check_python(),
    _check_git(),
    _check_claude_cli(),
    _check_gh(),
    _check_disk_space(),
    _check_db_connectivity(),
]
if _web_extras_installed():
    checks.insert(4, _check_node_version())
    checks.insert(5, _check_node_npm())
    checks.insert(6, _check_jwt_secret())
```

### 5. GitHub Actions Publish Workflow (optional)

`.github/workflows/publish.yml` — triggered on GitHub release creation:

```yaml
name: Publish to PyPI
on:
  release:
    types: [published]
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install hatch
      - run: hatch build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

Uses PyPI trusted publishing (no API token needed, OIDC-based).

## What Changes Per File

| File | Change |
|------|--------|
| `install.sh` | Full rewrite (~80 lines). uv-based, 4 steps. |
| `pyproject.toml` | Add PyPI metadata, split deps (core vs web), exclude test files from wheel, move httpx to core |
| `forge/cli/main.py` | Add try/except ImportError guard around uvicorn/fastapi in `serve()` |
| `forge/storage/db.py` | Make `bcrypt` import lazy (inside `create_user`/`verify_password` only) |
| `forge/cli/doctor.py` | Conditionally skip Node.js/npm/JWT checks when web extras not installed |
| `.github/workflows/publish.yml` | New file. Publish to PyPI on release. |

## Testing Plan

1. `hatch build` succeeds — produces `.whl` and `.tar.gz`
2. `pip install dist/forge_orchestrator-0.1.0-py3-none-any.whl` in a clean venv → `forge --help` works
3. `forge tui` works without web deps installed
4. `forge serve` prints helpful error when web deps missing
5. `forge doctor` works without web deps, does NOT show Node.js warnings
6. **Import isolation test:** In a TUI-only venv, `python -c "from forge.cli.main import cli"` succeeds
7. **Import isolation test:** In a TUI-only venv, `python -c "from forge.storage.db import Database"` succeeds (no bcrypt crash)
8. Run `install.sh` on a clean macOS — verify all 4 steps complete
9. Run `install.sh` twice — verify idempotent (upgrades, doesn't duplicate)
10. Verify `.whl` does NOT contain `*_test.py` files: `unzip -l dist/*.whl | grep _test`

## Out of Scope

- Web UI packaging (Next.js frontend stays git-clone-only for now)
- Docker image
- Homebrew formula (future enhancement)
- Windows support (macOS + Linux only)
- `forge setup` interactive wizard (verification is inline in install.sh)
- Fixing `forge serve` path resolution for PyPI installs (git-clone-only for now)
- `py.typed` marker (nice-to-have, not blocking)
