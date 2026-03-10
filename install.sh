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
FORGE_REPO_URL="${FORGE_REPO_URL:-https://github.com/tarunms7/forge-orchestrator.git}"
FORGE_HOME="$HOME/.forge"
FORGE_VENV="$FORGE_HOME/venv"
FORGE_SRC="$FORGE_HOME/src"
REQUIRED_PYTHON_MAJOR=3
REQUIRED_PYTHON_MINOR=12
REQUIRED_NODE_MAJOR=18
TOTAL_STEPS=8

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

# ── Prompt helper ────────────────────────────────────────────────────
# If stdin is a terminal, ask the user. Otherwise auto-accept.
confirm_or_auto() {
    if [ -t 0 ]; then
        printf "%s [Y/n] " "$1"
        read -r answer </dev/tty
        case "$answer" in
            [nN]*) return 1 ;;
            *)     return 0 ;;
        esac
    else
        info "Non-interactive mode — auto-accepting: $1"
        return 0
    fi
}

# ── Parse version helpers ────────────────────────────────────────────
parse_python_version() {
    # Accepts "Python 3.12.1" → sets PY_MAJOR and PY_MINOR
    _ver="$(python3 --version 2>/dev/null | sed 's/Python //')" || return 1
    PY_MAJOR="$(echo "$_ver" | cut -d. -f1)"
    PY_MINOR="$(echo "$_ver" | cut -d. -f2)"
}

parse_node_version() {
    # Accepts "v18.17.0" → sets NODE_MAJOR
    _ver="$(node --version 2>/dev/null | sed 's/^v//')" || return 1
    NODE_MAJOR="$(echo "$_ver" | cut -d. -f1)"
}

# ══════════════════════════════════════════════════════════════════════
#  Step 1 — Detect OS
# ══════════════════════════════════════════════════════════════════════
step 1 "Detecting operating system..."

OS="$(uname -s)"
case "$OS" in
    Darwin) OS_NAME="macOS" ;;
    Linux)  OS_NAME="Linux" ;;
    *)
        error "Unsupported operating system: $OS"
        error "Forge currently supports macOS and Linux."
        exit 1
        ;;
esac
success "Detected $OS_NAME ($OS)"

# ══════════════════════════════════════════════════════════════════════
#  Step 2 — Python 3.12+ check
# ══════════════════════════════════════════════════════════════════════
step 2 "Checking Python ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}+..."

install_python() {
    case "$OS" in
        Darwin)
            if ! command -v brew >/dev/null 2>&1; then
                error "Homebrew is required to install Python on macOS."
                error "Install it from https://brew.sh and re-run this script."
                exit 1
            fi
            brew install "python@${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}"
            ;;
        Linux)
            info "Installing python${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR} via apt-get..."
            sudo apt-get update -y
            sudo apt-get install -y "python${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}" \
                "python${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}-venv"
            ;;
    esac
}

python_version_ok() {
    [ "$PY_MAJOR" -gt "$REQUIRED_PYTHON_MAJOR" ] || \
    { [ "$PY_MAJOR" -eq "$REQUIRED_PYTHON_MAJOR" ] && [ "$PY_MINOR" -ge "$REQUIRED_PYTHON_MINOR" ]; }
}

PYTHON_OK=false
if command -v python3 >/dev/null 2>&1; then
    if parse_python_version; then
        if python_version_ok; then
            PYTHON_OK=true
            success "Python ${PY_MAJOR}.${PY_MINOR} found"
        else
            warn "Python ${PY_MAJOR}.${PY_MINOR} found, but ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}+ is required"
        fi
    else
        warn "Could not determine Python version"
    fi
else
    warn "python3 not found"
fi

if [ "$PYTHON_OK" = false ]; then
    if confirm_or_auto "Install Python ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}?"; then
        install_python
        # Re-check after install
        if command -v python3 >/dev/null 2>&1 && parse_python_version; then
            if python_version_ok; then
                success "Python ${PY_MAJOR}.${PY_MINOR} installed successfully"
            else
                error "Installation succeeded but version ${PY_MAJOR}.${PY_MINOR} < ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}"
                exit 1
            fi
        else
            error "Python installation failed. Please install Python ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}+ manually."
            exit 1
        fi
    else
        error "Python ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}+ is required. Aborting."
        exit 1
    fi
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 3 — Node 18+ check
# ══════════════════════════════════════════════════════════════════════
step 3 "Checking Node.js ${REQUIRED_NODE_MAJOR}+..."

install_node() {
    case "$OS" in
        Darwin)
            if ! command -v brew >/dev/null 2>&1; then
                error "Homebrew is required to install Node.js on macOS."
                error "Install it from https://brew.sh and re-run this script."
                exit 1
            fi
            brew install node
            ;;
        Linux)
            info "Installing Node.js via NodeSource setup script..."
            curl -fsSL "https://deb.nodesource.com/setup_${REQUIRED_NODE_MAJOR}.x" | sudo -E bash -
            sudo apt-get install -y nodejs
            ;;
    esac
}

NODE_OK=false
if command -v node >/dev/null 2>&1; then
    if parse_node_version; then
        if [ "$NODE_MAJOR" -ge "$REQUIRED_NODE_MAJOR" ]; then
            NODE_OK=true
            success "Node.js v${NODE_MAJOR} found"
        else
            warn "Node.js v${NODE_MAJOR} found, but v${REQUIRED_NODE_MAJOR}+ is required"
        fi
    else
        warn "Could not determine Node.js version"
    fi
else
    warn "node not found"
fi

if [ "$NODE_OK" = false ]; then
    if confirm_or_auto "Install Node.js ${REQUIRED_NODE_MAJOR}+?"; then
        install_node
        # Re-check after install
        if command -v node >/dev/null 2>&1 && parse_node_version; then
            if [ "$NODE_MAJOR" -ge "$REQUIRED_NODE_MAJOR" ]; then
                success "Node.js v${NODE_MAJOR} installed successfully"
            else
                error "Installation succeeded but version v${NODE_MAJOR} < v${REQUIRED_NODE_MAJOR}"
                exit 1
            fi
        else
            error "Node.js installation failed. Please install Node.js ${REQUIRED_NODE_MAJOR}+ manually."
            exit 1
        fi
    else
        error "Node.js ${REQUIRED_NODE_MAJOR}+ is required. Aborting."
        exit 1
    fi
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 4 — Git check
# ══════════════════════════════════════════════════════════════════════
step 4 "Checking Git..."

if command -v git >/dev/null 2>&1; then
    GIT_VER="$(git --version | sed 's/git version //')"
    success "Git ${GIT_VER} found"
else
    error "Git is not installed."
    case "$OS" in
        Darwin) error "Install via: xcode-select --install  or  brew install git" ;;
        Linux)  error "Install via: sudo apt-get install git" ;;
    esac
    exit 1
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 5 — Create virtualenv
# ══════════════════════════════════════════════════════════════════════
step 5 "Setting up virtualenv at ${FORGE_VENV}..."

mkdir -p "$FORGE_HOME"

if [ -d "$FORGE_VENV" ] && [ -f "$FORGE_VENV/bin/activate" ]; then
    info "Virtualenv already exists — reusing"
else
    info "Creating virtualenv..."
    python3 -m venv "$FORGE_VENV"
    success "Virtualenv created"
fi

# Activate (for subsequent pip/forge commands)
# shellcheck disable=SC1091
. "$FORGE_VENV/bin/activate"
success "Virtualenv activated"

# ══════════════════════════════════════════════════════════════════════
#  Step 6 — Clone / update repo & pip install
# ══════════════════════════════════════════════════════════════════════
step 6 "Installing Forge from ${FORGE_REPO_URL}..."

if [ -d "$FORGE_SRC/.git" ]; then
    info "Source already cloned — pulling latest..."
    git -C "$FORGE_SRC" pull --ff-only || {
        warn "Fast-forward pull failed; trying rebase..."
        git -C "$FORGE_SRC" pull --rebase
    }
else
    info "Cloning repository..."
    git clone "$FORGE_REPO_URL" "$FORGE_SRC"
fi

info "Installing Python package (editable)..."
pip install --upgrade pip >/dev/null 2>&1 || true
pip install -e "$FORGE_SRC"
success "Forge Python package installed"

# ══════════════════════════════════════════════════════════════════════
#  Step 7 — Frontend dependencies & build
# ══════════════════════════════════════════════════════════════════════
step 7 "Building frontend..."

FORGE_WEB="$FORGE_SRC/web"
if [ -d "$FORGE_WEB" ]; then
    info "Installing frontend dependencies..."
    (cd "$FORGE_WEB" && npm install)
    info "Building frontend..."
    (cd "$FORGE_WEB" && npm run build)
    success "Frontend built"
else
    warn "Web directory not found at ${FORGE_WEB} — skipping frontend build"
fi

# ══════════════════════════════════════════════════════════════════════
#  Step 8 — Symlink & doctor
# ══════════════════════════════════════════════════════════════════════
step 8 "Creating symlink & verifying installation..."

FORGE_BIN="$FORGE_VENV/bin/forge"
SYMLINK_TARGET=""

if [ -f "$FORGE_BIN" ]; then
    # Try /usr/local/bin first, fall back to ~/.local/bin
    if [ -w "/usr/local/bin" ] || [ -w "/usr/local" ]; then
        SYMLINK_TARGET="/usr/local/bin/forge"
    else
        SYMLINK_TARGET="$HOME/.local/bin/forge"
        mkdir -p "$HOME/.local/bin"
    fi

    if [ -L "$SYMLINK_TARGET" ] && [ "$(readlink "$SYMLINK_TARGET")" = "$FORGE_BIN" ]; then
        info "Symlink already correct: ${SYMLINK_TARGET} -> ${FORGE_BIN}"
    else
        # Remove stale symlink or file if present
        if [ -e "$SYMLINK_TARGET" ] || [ -L "$SYMLINK_TARGET" ]; then
            info "Updating existing symlink at ${SYMLINK_TARGET}"
            rm -f "$SYMLINK_TARGET"
        fi
        ln -s "$FORGE_BIN" "$SYMLINK_TARGET"
        success "Symlinked ${SYMLINK_TARGET} -> ${FORGE_BIN}"
    fi

    # Check if symlink dir is in PATH
    SYMLINK_DIR="$(dirname "$SYMLINK_TARGET")"
    case ":${PATH}:" in
        *":${SYMLINK_DIR}:"*) ;;
        *)
            warn "${SYMLINK_DIR} is not in your PATH."
            warn "Add this to your shell profile:"
            warn "  export PATH=\"${SYMLINK_DIR}:\$PATH\""
            ;;
    esac
else
    warn "Forge binary not found at ${FORGE_BIN} — skipping symlink"
fi

# Run forge doctor
info "Running 'forge doctor' to verify installation..."
if forge doctor; then
    success "'forge doctor' passed"
else
    warn "'forge doctor' reported issues — see output above"
fi

# ── Done ─────────────────────────────────────────────────────────────
printf "\n${GREEN}${BOLD}Forge installed successfully!${RESET}\n"
info "Run 'forge --help' to get started."
info "Source: ${FORGE_SRC}"
info "Venv:   ${FORGE_VENV}"
