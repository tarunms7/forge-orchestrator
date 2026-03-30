#!/usr/bin/env bash
# scripts/smoke_test.sh
#
# End-to-end smoke test for Forge.
# Runs the LOCAL build of forge (not the globally installed one)
# against a small repo with a known bug, and verifies the fix lands.
#
# Usage:
#   ./scripts/smoke_test.sh
#
# What it tests (full pipeline):
#   clone repo → forge plans → agent implements fix → review gates →
#   tests pass on the pipeline branch → script pushes branch + opens PR
#
# Cost: ~$0.05–0.15 (haiku, one task)
# Time: ~3–6 minutes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 1. Pin to LOCAL forge — never the global install ──────────────────────

LOCAL_FORGE="$REPO_ROOT/.venv/bin/forge"

if [[ ! -f "$LOCAL_FORGE" ]]; then
    echo "ERROR: local forge not found at $LOCAL_FORGE"
    echo "Run:  cd $REPO_ROOT && uv sync"
    exit 1
fi

echo "============================================"
echo " Forge Smoke Test"
echo "============================================"
echo ""
echo "LOCAL forge : $LOCAL_FORGE"
echo "LOCAL version: $("$LOCAL_FORGE" --version 2>/dev/null || echo 'n/a')"
echo ""
# Make sure we're not accidentally using the global one
GLOBAL_FORGE="$(which forge 2>/dev/null || echo 'not in PATH')"
echo "GLOBAL forge : $GLOBAL_FORGE"
if [[ "$GLOBAL_FORGE" != "not in PATH" ]]; then
    echo "  (ignored — we use the local .venv/bin/forge above)"
fi
echo ""

# ── 2. Clone the smoke-test fixture repo ─────────────────────────────────

SMOKE_REPO_URL="https://github.com/tarunms7/forge-smoke-test"
WORK_DIR="$(mktemp -d)/forge-smoke-test"

echo "Cloning fixture repo..."
echo "  $SMOKE_REPO_URL -> $WORK_DIR"
git clone --quiet "$SMOKE_REPO_URL" "$WORK_DIR"
cd "$WORK_DIR"

# ── 3. Confirm the bug exists before we start ─────────────────────────────

echo ""
echo "Confirming bug exists on main..."
if python3 -m pytest test_calculator.py::test_divide_by_zero -q 2>&1 | grep -q "passed"; then
    echo ""
    echo "WARNING: test_divide_by_zero already passes on main."
    echo "The smoke-test repo's main branch should always have the bug."
    echo "Has a fix been accidentally merged to main?"
    echo ""
    echo "To reset: go to $SMOKE_REPO_URL and revert any merged fix PRs."
    exit 1
fi
echo "  Bug confirmed: test_divide_by_zero fails on main (as expected)"

# ── 4. Run LOCAL forge to fix it ──────────────────────────────────────────

echo ""
echo "Running forge (local build)..."
echo "  Task: Fix the divide-by-zero bug in calculator.py"
echo ""

"$LOCAL_FORGE" run \
    "Fix the divide-by-zero bug: calculator.divide() should return None when b is 0 instead of raising ZeroDivisionError. The test test_divide_by_zero in test_calculator.py shows the expected behaviour." \
    --project-dir "$WORK_DIR"

# ── 5. Verify: find the forge pipeline branch and check tests pass ────────
#
# forge run is headless — it merges tasks into a LOCAL pipeline branch
# (forge/<pipeline-id>) but does not push to remote automatically.
# We find the local branch, check tests pass, then push + open a PR so
# the result is visible on GitHub.

echo ""
echo "Verifying fix..."

# Find the forge pipeline branch (local) — most-recently-committed forge/* branch
FORGE_BRANCH="$(git branch --sort=-committerdate | grep "forge/" | head -1 | tr -d ' *')"

if [[ -z "$FORGE_BRANCH" ]]; then
    echo "ERROR: No local forge branch found."
    echo "Did forge complete successfully? Check the output above."
    exit 1
fi

echo "  Found pipeline branch: $FORGE_BRANCH"
git checkout --quiet "$FORGE_BRANCH"

echo ""
echo "Running full test suite on fixed code..."
python3 -m pytest test_calculator.py -v

# ── 5b. Push the pipeline branch and open a PR ────────────────────────────

echo ""
echo "Pushing pipeline branch and opening PR..."
git push --quiet origin "$FORGE_BRANCH"

PR_URL="$(gh pr create \
    --repo tarunms7/forge-smoke-test \
    --head "$FORGE_BRANCH" \
    --base main \
    --title "fix: divide-by-zero bug (smoke test)" \
    --body "Automated smoke test PR — created by \`scripts/smoke_test.sh\` running LOCAL forge.

**Do not merge.** Leave open or close without merging so \`main\` always has the bug for the next smoke test run." \
    2>&1)"

echo "  PR: $PR_URL"

echo ""
echo "============================================"
echo " SMOKE TEST PASSED"
echo "============================================"
echo ""
echo "Forge (local build at $LOCAL_FORGE)"
echo "successfully planned, implemented, and reviewed"
echo "a fix for the known bug."
echo ""
echo "PR: $PR_URL"
echo ""

# ── 6. Cleanup ────────────────────────────────────────────────────────────
cd /tmp
rm -rf "$WORK_DIR"
echo "Cleaned up temp dir."
