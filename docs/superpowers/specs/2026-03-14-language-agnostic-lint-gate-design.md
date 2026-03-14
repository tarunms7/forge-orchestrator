# Language-Agnostic Lint Gate

**Date:** 2026-03-14
**Status:** Approved

## Problem

The lint gate (`_gate1`) is hardcoded to Python/ruff. For non-Python projects, `py_files` is empty and the gate silently passes. Users working in JS, Go, Rust, Ruby, or mixed-language repos get zero lint coverage. Even Python projects using pre-commit (black, isort, flake8) instead of ruff get no benefit.

## Decision Summary

- **Approach:** Lint Strategy Pattern — a `LintStrategy` dataclass detected from project config, with language fallbacks
- **Auto-fix:** Two-pass model (fix + commit, then verify clean)
- **Scoping:** Changed files only for linters that support file args; whole project for linters that don't (`make lint`, etc.)
- **Config:** Auto-detect first, user can override via `lint_cmd` / `lint_fix_cmd` settings

## LintStrategy Dataclass

```python
@dataclass
class LintStrategy:
    name: str                        # "ruff", "eslint", "pre-commit", etc.
    check_cmd: list[str]             # Always required
    fix_cmd: list[str] | None        # None = skip fix pass (e.g. shellcheck)
    supports_file_args: bool         # True = append changed files to commands
    commit_msg: str                  # "fix: auto-fix lint issues ({name})"
    tool_check: str | None = None    # Binary to verify exists, None = skip
    check_via_output: bool = False   # True = non-empty stdout means failure (gofmt -l)
```

- `supports_file_args=True`: forge appends changed file list to both `fix_cmd` and `check_cmd`
- `supports_file_args=False`: commands run as-is (whole project)
- `fix_cmd=None`: fix pass is skipped, only verify pass runs
- `tool_check`: verify tool is installed via `shutil.which()` before using strategy — applies to ALL detection steps, not just language fallbacks
- `check_via_output=True`: verify pass checks stdout instead of exit code (for tools like `gofmt -l` that always exit 0)

## Detection Order

`detect_lint_strategy(worktree_path, changed_files, lint_cmd_override)` returns `LintStrategy | None`:

If `changed_files` is empty, return `None` immediately (no files to lint).

### 1. User override (`lint_cmd` / `lint_fix_cmd` settings)

If `lint_cmd` is set (via `FORGE_LINT_CMD` env var or template config), use it as check command. If `lint_fix_cmd` is also set, use it as fix command. `supports_file_args=False` (we can't know the user's tool). If only `lint_cmd` is set, fix pass is skipped.

### 2. `.pre-commit-config.yaml`

If present in worktree root AND `shutil.which("pre-commit")` succeeds:
- fix: `["pre-commit", "run", "--files"] + changed_files`
- check: same command (pre-commit exits non-zero if it modified anything)
- `supports_file_args=True`
- Two-pass: run once (it fixes), commit, run again (should exit 0)

### 3. `package.json` with lint script

If `package.json` exists, has a `"lint"` script, AND `shutil.which("npm")` succeeds:
- fix: `["npm", "run", "lint:fix"]` if `"lint:fix"` script exists, else skip fix pass
- check: `["npm", "run", "lint"]`
- `supports_file_args=False` (npm scripts don't reliably accept file args)

### 4. `Makefile` with lint target

If `Makefile` exists and contains a `lint` target (detected via `grep -q '^lint:' Makefile`):
- fix: `["make", "lint-fix"]` if `lint-fix` target exists, else skip fix pass
- check: `["make", "lint"]`
- `supports_file_args=False`

### 5. Language fallback

Based on file extensions of changed files. Pick the dominant language (most changed files; tiebreaker: first in table order below). Verify tool is installed via `shutil.which()`.

| Language | Extensions | Check | Fix | File Args | Tool Check | Notes |
|----------|-----------|-------|-----|-----------|------------|-------|
| Python | `.py` | `sys.executable -m ruff check` | `sys.executable -m ruff check --fix` | Yes | None (bundled) | Uses `sys.executable -m` to respect venv |
| JS/TS | `.js .jsx .ts .tsx` | `npx eslint --no-error-on-unmatched-pattern` | `npx eslint --fix --no-error-on-unmatched-pattern` | Yes | `npx` | |
| Go | `.go` | `gofmt -l` | `gofmt -w` | Yes | `gofmt` | `check_via_output=True` (gofmt -l always exits 0; non-empty stdout = unformatted files) |
| Rust | `.rs` | `cargo clippy -- -D warnings` | `cargo clippy --fix --allow-dirty` | No | `cargo` | Runs on whole crate |
| Ruby | `.rb` | `rubocop --format simple` | `rubocop -a` | Yes | `rubocop` | |
| Kotlin | `.kt` | `ktlint` | `ktlint -F` | Yes | `ktlint` | |
| Swift | `.swift` | `swiftlint lint --quiet` | `swiftlint lint --fix --quiet` | Yes | `swiftlint` | |
| Shell | `.sh .bash` | `shellcheck` | None (no auto-fix) | Yes | `shellcheck` | |

Java is excluded from defaults — no widely-used CLI formatter works reliably without project-specific config. Java projects should use a `Makefile` lint target or `lint_cmd` override.

If tool is not installed: skip with "No linter available for {language} (install {tool})".

### 6. No linter found

Return `None`. Gate passes with "No linter detected".

**Known limitation:** For multi-language projects using the fallback path, only the dominant language is linted. Projects needing multi-language lint should use pre-commit, a Makefile target, or the `lint_cmd` override.

## Gate Runner Flow

`_run_lint_gate` is an `async def` method on `ReviewMixin` (replacing `_gate1`). Lint command resolution uses `_resolve_lint_cmd()` following the same pattern as `_resolve_build_cmd()` / `_resolve_test_cmd()` (template → pipeline → settings fallback).

```
1. changed_files = _get_changed_files_vs_main(worktree_path, base_ref=pipeline_branch)
2. Filter out deleted files: keep only files where os.path.isfile(worktree_path / f)
3. lint_cmd_override = _resolve_lint_cmd(template_config, pipeline_config)
4. strategy = detect_lint_strategy(worktree_path, changed_files, lint_cmd_override)
5. If None → GateResult(passed=True, "No linter detected")
6. If strategy.tool_check and not shutil.which(strategy.tool_check):
   → GateResult(passed=True, "No linter available for {name} (install {tool_check})")
7. Build final commands:
   - If supports_file_args: append changed_files to fix_cmd/check_cmd
   - Else: use commands as-is
8. PASS 1 (fix): if fix_cmd is not None:
   - Run fix_cmd in worktree via subprocess.run
   - Capture diff via `git diff`
   - Stage + commit if changes (message: strategy.commit_msg)
9. PASS 2 (verify): Run check_cmd in worktree
   - If check_via_output: failure = non-empty stdout (regardless of exit code)
   - Else: failure = non-zero exit code
10. If passed → GateResult(passed=True, details with auto-fix summary via _summarize_auto_fix)
11. If failed → GateResult(passed=False, truncated output, first 500 chars)
```

Event emissions (`review:gate_started`, `review:gate_passed`/`review:gate_failed`, `task:review_update`) remain unchanged in the calling code in `_run_review`.

## Config Surface

Two new fields in `ForgeSettings`:

```python
lint_cmd: str | None = None      # env: FORGE_LINT_CMD
lint_fix_cmd: str | None = None  # env: FORGE_LINT_FIX_CMD
```

Also overridable via template config. Resolution via `_resolve_lint_cmd()` / `_resolve_lint_fix_cmd()` following the same pattern as existing `_resolve_build_cmd()` / `_resolve_test_cmd()`.

## Files Changed

- `forge/core/daemon_review.py` — `LintStrategy` dataclass, `detect_lint_strategy()`, `_run_lint_gate()` replacing `_gate1()`, `_resolve_lint_cmd()` / `_resolve_lint_fix_cmd()`
- `forge/config/settings.py` — add `lint_cmd` and `lint_fix_cmd` fields

No new files. Detection + strategy + runner all live in `daemon_review.py` (estimated ~170 lines total).

## What This Enables

- Python projects with pre-commit get black/isort/flake8 running automatically
- JS/TS projects get eslint
- Go projects get gofmt
- Any project with a `Makefile` lint target or `package.json` lint script works out of the box
- Users can always override with `FORGE_LINT_CMD` / `FORGE_LINT_FIX_CMD` for unusual setups
- Auto-fix + commit pattern works for all linters, not just ruff
