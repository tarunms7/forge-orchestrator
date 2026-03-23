#!/bin/sh
# ──────────────────────────────────────────────────────────────────────
# Forge Orchestrator — Installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/tarunms7/forge-orchestrator/main/install.sh | sh
#
# This script is idempotent — safe to run multiple times.
# Re-running upgrades Forge to the latest version.
# ──────────────────────────────────────────────────────────────────────
set -e

FORGE_REPO_URL="https://github.com/tarunms7/forge-orchestrator.git"
FORGE_REPO_PIP="git+${FORGE_REPO_URL}"
FORGE_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/forge"
FORGE_REPO_DIR="${FORGE_DATA_DIR}/repo"
TOTAL_STEPS=7

# ── Color helpers ────────────────────────────────────────────────────
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' BOLD='' RESET=''
fi

info()    { printf "${BLUE}[info]${RESET}  %s\n" "$*"; }
warn()    { printf "${YELLOW}[warn]${RESET}  %s\n" "$*"; }
error()   { printf "${RED}[error]${RESET} %s\n" "$*" >&2; }
success() { printf "${GREEN}[ok]${RESET}    %s\n" "$*"; }
step()    { _n="$1"; shift; printf "\n${BOLD}[%s/%s]${RESET} %s\n" "$_n" "$TOTAL_STEPS" "$*"; }

# ══════════════════════════════════════════════════════════════════════
#  Step 1 — Install uv (Python package manager)
# ══════════════════════════════════════════════════════════════════════
step 1 "Installing uv package manager..."

if command -v uv >/dev/null 2>&1; then
    success "uv already installed ($(uv --version))"
else
    info "uv not found — installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
    if ! command -v uv >/dev/null 2>&1; then
        error "uv installation failed. Install manually: https://docs.astral.sh/uv/"
        exit 1
    fi
    success "uv installed ($(uv --version))"
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 2 — Install Forge CLI + all extras
# ══════════════════════════════════════════════════════════════════════
step 2 "Installing forge-orchestrator..."

# Ensure Python 3.12+ is available for uv
if ! uv python list 2>/dev/null | grep -q "3\.1[2-9]"; then
    info "Installing Python 3.12 (required by Forge)..."
    uv python install 3.12
fi

if uv tool list 2>/dev/null | grep -q forge-orchestrator; then
    info "Upgrading forge-orchestrator..."
    uv tool install --python 3.12 --upgrade --force "${FORGE_REPO_PIP}[web]"
else
    uv tool install --python 3.12 "${FORGE_REPO_PIP}[web]"
fi

FORGE_VERSION="$(forge --version 2>/dev/null || echo 'unknown')"
success "forge-orchestrator installed (${FORGE_VERSION})"

# ══════════════════════════════════════════════════════════════════════
#  Step 3 — Clone/update repo (needed for web frontend)
# ══════════════════════════════════════════════════════════════════════
step 3 "Setting up Forge repository..."

mkdir -p "$FORGE_DATA_DIR"

if [ -d "$FORGE_REPO_DIR/.git" ]; then
    info "Updating existing repo..."
    git -C "$FORGE_REPO_DIR" fetch origin main --quiet 2>/dev/null || true
    git -C "$FORGE_REPO_DIR" reset --hard origin/main --quiet 2>/dev/null || true
    success "Repository updated"
else
    info "Cloning repository..."
    git clone --depth 1 "$FORGE_REPO_URL" "$FORGE_REPO_DIR" 2>/dev/null
    success "Repository cloned to ${FORGE_REPO_DIR}"
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 4 — Install frontend dependencies
# ══════════════════════════════════════════════════════════════════════
step 4 "Installing web frontend..."

WEB_DIR="${FORGE_REPO_DIR}/web"

if command -v node >/dev/null 2>&1; then
    NODE_VER="$(node --version)"
    success "Node.js ${NODE_VER} found"

    if command -v npm >/dev/null 2>&1; then
        info "Installing frontend dependencies..."
        npm install --prefix "$WEB_DIR" --silent 2>/dev/null && \
            success "Frontend dependencies installed" || \
            warn "npm install failed — 'forge serve' web UI won't work, TUI still works fine"
    else
        warn "npm not found — 'forge serve' web UI won't work, TUI still works fine"
    fi
else
    warn "Node.js not found — 'forge serve' web UI won't work"
    info "Install Node.js 20+: https://nodejs.org"
    info "The TUI (forge tui) works without Node.js"
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 5 — Verify required tools
# ══════════════════════════════════════════════════════════════════════
step 5 "Checking required tools..."

if command -v git >/dev/null 2>&1; then
    success "git $(git --version | sed 's/git version //')"
else
    warn "git not found — required for forge"
fi

if command -v claude >/dev/null 2>&1; then
    success "claude CLI found"
else
    warn "claude CLI not found — run 'claude login' after installing: https://docs.anthropic.com/en/docs/claude-code"
fi

if command -v gh >/dev/null 2>&1; then
    success "gh CLI found (auto-PR creation enabled)"
else
    info "gh CLI not found (optional) — install from https://cli.github.com for auto-PR creation"
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 6 — Run forge doctor
# ══════════════════════════════════════════════════════════════════════
step 6 "Running forge doctor..."

if command -v forge >/dev/null 2>&1; then
    forge doctor 2>/dev/null && \
        success "All checks passed" || \
        warn "Some checks failed — run 'forge doctor' for details"
else
    warn "forge not found on PATH — open a new terminal"
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 7 — Done
# ══════════════════════════════════════════════════════════════════════
step 7 "You're all set!"

printf "\n${GREEN}${BOLD}Forge installed successfully!${RESET}\n\n"
info "Get started:"
info "  cd your-project"
info "  forge tui              — interactive terminal UI"
info "  forge run \"your task\"  — run from command line"
info "  forge serve            — web dashboard"
info "  forge upgrade          — upgrade to latest version"
printf "\n"
info "Central data: ${FORGE_DATA_DIR}"
info "Web frontend: ${WEB_DIR}"
printf "\n"
info "You may need to open a new terminal or run:"
info "  source ~/.bashrc   (bash)"
info "  source ~/.zshrc    (zsh)"
