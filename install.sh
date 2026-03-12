#!/bin/sh
# ──────────────────────────────────────────────────────────────────────
# Forge Orchestrator — Installer
#
# Usage:
#   curl -fsSL https://forge.dev/install.sh | sh
#   # or
#   sh install.sh
#
# This script is idempotent — safe to run multiple times.
# ──────────────────────────────────────────────────────────────────────
set -e

# ── Configuration ────────────────────────────────────────────────────
TOTAL_STEPS=4

# ── Color helpers ────────────────────────────────────────────────────
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    BOLD=''
    RESET=''
fi

info()    { printf "${BLUE}[info]${RESET}  %s\n" "$*"; }
warn()    { printf "${YELLOW}[warn]${RESET}  %s\n" "$*"; }
error()   { printf "${RED}[error]${RESET} %s\n" "$*" >&2; }
success() { printf "${GREEN}[ok]${RESET}    %s\n" "$*"; }

step() {
    _step_num="$1"; shift
    printf "\n${BOLD}[%s/%s]${RESET} %s\n" "$_step_num" "$TOTAL_STEPS" "$*"
}

# ══════════════════════════════════════════════════════════════════════
#  Step 1 — Install uv
# ══════════════════════════════════════════════════════════════════════
step 1 "Installing uv package manager..."

if command -v uv >/dev/null 2>&1; then
    success "uv already installed ($(uv --version))"
else
    info "uv not found — installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Source the env file so uv is available in this session
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    fi

    if ! command -v uv >/dev/null 2>&1; then
        error "uv installation failed. Please install manually: https://docs.astral.sh/uv/"
        exit 1
    fi
    success "uv installed ($(uv --version))"
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 2 — Install Forge
# ══════════════════════════════════════════════════════════════════════
step 2 "Installing forge-orchestrator..."

if uv tool list 2>/dev/null | grep -q forge-orchestrator; then
    info "forge-orchestrator already installed — upgrading..."
    uv tool upgrade forge-orchestrator
else
    info "Installing forge-orchestrator..."
    uv tool install forge-orchestrator
fi

FORGE_VERSION="$(forge --version 2>/dev/null || echo 'unknown')"
success "forge-orchestrator installed (${FORGE_VERSION})"

# ══════════════════════════════════════════════════════════════════════
#  Step 3 — Verify tools (non-blocking)
# ══════════════════════════════════════════════════════════════════════
step 3 "Checking recommended tools..."

if command -v git >/dev/null 2>&1; then
    GIT_VER="$(git --version | sed 's/git version //')"
    success "✓ git ${GIT_VER}"
else
    warn "⚠ git not found — required for forge to manage repositories"
fi

if command -v claude >/dev/null 2>&1; then
    success "✓ claude CLI found"
else
    warn "⚠ claude CLI not found — required for agent execution"
fi

if command -v gh >/dev/null 2>&1; then
    success "✓ gh CLI found"
else
    warn "⚠ gh CLI not found (optional) — install from https://cli.github.com"
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 4 — Quickstart
# ══════════════════════════════════════════════════════════════════════
step 4 "You're all set!"

printf "\n${GREEN}${BOLD}Forge installed successfully!${RESET}\n\n"
info "Get started:"
info "  forge tui          — launch the interactive TUI"
info "  forge doctor       — verify your environment"
info "  forge --help       — see all commands"
printf "\n"
info "You may need to open a new terminal or run:"
info "  source ~/.bashrc   (bash)"
info "  source ~/.zshrc    (zsh)"
