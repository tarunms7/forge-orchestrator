"""ReviewMixin — extracted review pipeline methods for ForgeDaemon."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from forge.config.project_config import CMD_DISABLED
from forge.core import model_router as _legacy_model_router
from forge.core.daemon_helpers import (
    _extract_activity,
    _extract_text,
    _find_related_test_files,
    _get_changed_files_vs_main,
    _humanize_model_spec,
    _is_pytest_cmd,
    _run_git,
    async_subprocess,
)
from forge.core.logging_config import make_console
from forge.review.llm_review import gate2_llm_review
from forge.review.pipeline import GateResult

logger = logging.getLogger("forge.daemon")
console = make_console()

# Backward-compatible module export for tests and call sites that still patch
# forge.core.daemon_review.select_model directly.
select_model = _legacy_model_router.select_model


# ---------------------------------------------------------------------------
# Shell command helpers
# ---------------------------------------------------------------------------


async def _async_shell(
    cmd: str,
    cwd: str,
    *,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command string (supports &&, ||, pipes, redirects).

    Unlike async_subprocess which uses create_subprocess_exec (no shell),
    this uses create_subprocess_shell for commands with shell operators.
    """
    import asyncio

    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise TimeoutError(f"Command timed out after {timeout}s: {cmd}")

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
        stderr=stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
    )


# ---------------------------------------------------------------------------
# LintStrategy — language-agnostic lint gate
# ---------------------------------------------------------------------------


@dataclass
class LintStrategy:
    """Describes how to lint a project or set of changed files."""

    name: str  # "ruff", "eslint", "pre-commit", etc.
    check_cmd: list[str]  # Always required
    fix_cmd: list[str] | None = None  # None = skip fix pass
    supports_file_args: bool = False  # True = append changed files to commands
    commit_msg: str = "fix: auto-fix lint issues"
    tool_check: str | None = None  # Binary to verify exists via shutil.which()
    check_via_output: bool = False  # True = non-empty stdout means failure
    extensions: set[str] | None = None  # File extensions this linter handles (e.g. {".py"})


# Language fallbacks — ordered by priority for tiebreaking
_LANGUAGE_FALLBACKS: list[tuple[set[str], LintStrategy]] = [
    (
        {".py"},
        LintStrategy(
            name="ruff",
            check_cmd=[sys.executable, "-m", "ruff", "check"],
            fix_cmd=[sys.executable, "-m", "ruff", "check", "--fix"],
            supports_file_args=True,
            commit_msg="fix: auto-fix lint issues (ruff)",
            extensions={".py", ".pyi"},
            # ruff is a core dependency — no tool_check needed
        ),
    ),
    (
        {".js", ".jsx", ".ts", ".tsx"},
        LintStrategy(
            name="eslint",
            check_cmd=["npx", "eslint", "--no-error-on-unmatched-pattern"],
            fix_cmd=["npx", "eslint", "--fix", "--no-error-on-unmatched-pattern"],
            supports_file_args=True,
            commit_msg="fix: auto-fix lint issues (eslint)",
            tool_check="npx",
            extensions={".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"},
        ),
    ),
    (
        {".go"},
        LintStrategy(
            name="gofmt",
            check_cmd=["gofmt", "-l"],
            fix_cmd=["gofmt", "-w"],
            supports_file_args=True,
            commit_msg="fix: auto-fix lint issues (gofmt)",
            tool_check="gofmt",
            check_via_output=True,
            extensions={".go"},
        ),
    ),
    (
        {".rs"},
        LintStrategy(
            name="cargo-clippy",
            check_cmd=["cargo", "clippy", "--", "-D", "warnings"],
            fix_cmd=["cargo", "clippy", "--fix", "--allow-dirty"],
            supports_file_args=False,
            commit_msg="fix: auto-fix lint issues (clippy)",
            tool_check="cargo",
            extensions={".rs"},
        ),
    ),
    (
        {".rb"},
        LintStrategy(
            name="rubocop",
            check_cmd=["rubocop", "--format", "simple"],
            fix_cmd=["rubocop", "-a"],
            supports_file_args=True,
            commit_msg="fix: auto-fix lint issues (rubocop)",
            tool_check="rubocop",
            extensions={".rb"},
        ),
    ),
    (
        {".kt"},
        LintStrategy(
            name="ktlint",
            check_cmd=["ktlint"],
            fix_cmd=["ktlint", "-F"],
            supports_file_args=True,
            commit_msg="fix: auto-fix lint issues (ktlint)",
            tool_check="ktlint",
            extensions={".kt", ".kts"},
        ),
    ),
    (
        {".swift"},
        LintStrategy(
            name="swiftlint",
            check_cmd=["swiftlint", "lint", "--quiet"],
            fix_cmd=["swiftlint", "lint", "--fix", "--quiet"],
            supports_file_args=True,
            commit_msg="fix: auto-fix lint issues (swiftlint)",
            tool_check="swiftlint",
            extensions={".swift"},
        ),
    ),
    (
        {".sh", ".bash"},
        LintStrategy(
            name="shellcheck",
            check_cmd=["shellcheck"],
            fix_cmd=None,
            supports_file_args=True,
            commit_msg="fix: auto-fix lint issues (shellcheck)",
            tool_check="shellcheck",
            extensions={".sh", ".bash"},
        ),
    ),
]


def _detect_makefile_target(makefile_path: str, target: str) -> bool:
    """Check if a Makefile contains a given target (e.g. 'lint:')."""
    try:
        with open(makefile_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith(f"{target}:"):
                    return True
    except OSError:
        pass
    return False


def detect_lint_strategy(
    worktree_path: str,
    changed_files: list[str],
    lint_cmd_override: str | None = None,
    lint_fix_cmd_override: str | None = None,
) -> LintStrategy | None:
    """Detect the best lint strategy for the given worktree and changed files.

    Detection order:
    1. User override (lint_cmd / lint_fix_cmd settings)
    2. .pre-commit-config.yaml
    3. package.json with lint script
    4. Makefile with lint target
    5. Language fallback based on file extensions
    6. None (no linter found)
    """
    if not changed_files:
        return None

    # 1. User override
    if lint_cmd_override:
        fix = shlex.split(lint_fix_cmd_override) if lint_fix_cmd_override else None
        return LintStrategy(
            name="custom",
            check_cmd=shlex.split(lint_cmd_override),
            fix_cmd=fix,
            supports_file_args=False,
            commit_msg="fix: auto-fix lint issues (custom)",
        )

    # 2. .pre-commit-config.yaml
    pre_commit_cfg = os.path.join(worktree_path, ".pre-commit-config.yaml")
    if os.path.isfile(pre_commit_cfg) and shutil.which("pre-commit"):
        return LintStrategy(
            name="pre-commit",
            check_cmd=["pre-commit", "run", "--files"],
            fix_cmd=["pre-commit", "run", "--files"],
            supports_file_args=True,
            commit_msg="fix: auto-fix lint issues (pre-commit)",
            tool_check="pre-commit",
        )

    # 3. package.json with lint script
    pkg_json_path = os.path.join(worktree_path, "package.json")
    if os.path.isfile(pkg_json_path) and shutil.which("npm"):
        try:
            with open(pkg_json_path, encoding="utf-8") as f:
                pkg = json.load(f)
            scripts = pkg.get("scripts", {})
            if "lint" in scripts:
                fix = ["npm", "run", "lint:fix"] if "lint:fix" in scripts else None
                return LintStrategy(
                    name="npm-lint",
                    check_cmd=["npm", "run", "lint"],
                    fix_cmd=fix,
                    supports_file_args=False,
                    commit_msg="fix: auto-fix lint issues (npm lint)",
                    tool_check="npm",
                )
        except (json.JSONDecodeError, OSError):
            pass

    # 4. Makefile with lint target
    makefile_path = os.path.join(worktree_path, "Makefile")
    if os.path.isfile(makefile_path):
        if _detect_makefile_target(makefile_path, "lint"):
            fix = (
                ["make", "lint-fix"] if _detect_makefile_target(makefile_path, "lint-fix") else None
            )
            return LintStrategy(
                name="make-lint",
                check_cmd=["make", "lint"],
                fix_cmd=fix,
                supports_file_args=False,
                commit_msg="fix: auto-fix lint issues (make lint)",
            )

    # 5. Language fallback — pick dominant language
    ext_counts: dict[str, int] = {}
    for f in changed_files:
        _, ext = os.path.splitext(f)
        if ext:
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

    for extensions, strategy in _LANGUAGE_FALLBACKS:
        count = sum(ext_counts.get(ext, 0) for ext in extensions)
        if count > 0:
            if strategy.tool_check and not shutil.which(strategy.tool_check):
                logger.info(
                    "No linter available for %s (install %s)",
                    strategy.name,
                    strategy.tool_check,
                )
                continue
            return strategy

    # 6. No linter found
    return None


def detect_all_lint_strategies(
    worktree_path: str,
    changed_files: list[str],
    lint_cmd_override: str | None = None,
    lint_fix_cmd_override: str | None = None,
) -> list[LintStrategy]:
    """Detect ALL applicable lint strategies for mixed-language changes.

    Unlike detect_lint_strategy (returns first match), this returns a strategy
    for each language that has changed files. Useful for repos with Python + TS + Go.
    """
    # User override or project-level config takes precedence (single strategy)
    single = detect_lint_strategy(
        worktree_path, changed_files, lint_cmd_override, lint_fix_cmd_override
    )
    if single and single.name in ("custom", "pre-commit", "npm-lint", "make-lint"):
        return [single] if single else []

    # Language fallback — return ALL matching strategies
    ext_counts: dict[str, int] = {}
    for f in changed_files:
        _, ext = os.path.splitext(f)
        if ext:
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

    strategies: list[LintStrategy] = []
    for extensions, strategy in _LANGUAGE_FALLBACKS:
        count = sum(ext_counts.get(ext, 0) for ext in extensions)
        if count > 0:
            if strategy.tool_check and not shutil.which(strategy.tool_check):
                continue
            strategies.append(strategy)

    return strategies


def _summarize_auto_fix(diff_text: str) -> str:
    """Produce a brief human-readable summary of an auto-fix diff.

    Counts removed imports and other changed lines to give the agent
    context about what ruff auto-fixed.
    """
    removed_imports = 0
    added_lines = 0
    removed_lines = 0
    for line in diff_text.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            removed_lines += 1
            if "import " in line:
                removed_imports += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines += 1

    parts: list[str] = []
    if removed_imports:
        parts.append(
            f"removed {removed_imports} unused import{'s' if removed_imports != 1 else ''}"
        )
    other_removed = removed_lines - removed_imports
    if other_removed > 0 or added_lines > 0:
        parts.append(
            f"{removed_lines + added_lines} line{'s' if (removed_lines + added_lines) != 1 else ''} changed"
        )
    return "; ".join(parts) if parts else "minor fixes applied"


class ReviewMixin:
    """Review pipeline methods mixed into ForgeDaemon.

    Expects the host class to provide:
        self._strategy   — model routing strategy (str)
        self._snapshot   — ProjectSnapshot | None
        self._emit       — async event emitter
        self._settings   — ForgeSettings
    """

    # -- template review config --------------------------------------------

    def _get_review_config(self) -> dict:
        """Load review config from self._template_config.

        Returns a dict with keys: skip_l2, extra_review_pass,
        custom_review_focus.  All default to safe values when no
        template config is set.
        """
        template_config = getattr(self, "_template_config", None)
        if not template_config:
            return {"skip_l2": False, "extra_review_pass": False, "custom_review_focus": ""}
        review_raw = template_config.get("review_config", {})
        if not isinstance(review_raw, dict):
            return {"skip_l2": False, "extra_review_pass": False, "custom_review_focus": ""}
        return {
            "skip_l2": bool(review_raw.get("skip_l2", False)),
            "extra_review_pass": bool(review_raw.get("extra_review_pass", False)),
            "custom_review_focus": review_raw.get("custom_review_focus", "") or "",
        }

    def _record_health_activity(self, task_id: str) -> None:
        """Notify the pipeline health monitor that review work is still active."""
        health = getattr(self, "_health_monitor", None)
        if health:
            health.record_task_activity(task_id)

    def _select_model(
        self,
        stage: str,
        complexity: str = "medium",
        *,
        retry_count: int = 0,
    ):
        """Fallback model resolver for isolated ReviewMixin tests.

        ForgeDaemon overrides this with the provider-aware implementation.
        """
        settings = getattr(self, "_settings", None)
        overrides = None
        build_overrides = getattr(settings, "build_routing_overrides", None)
        if callable(build_overrides):
            overrides = build_overrides()
        strategy = getattr(self, "_strategy", None) or getattr(settings, "model_strategy", "auto")
        registry = getattr(self, "_registry", None)
        return select_model(
            strategy,
            stage,
            complexity,
            overrides=overrides,
            retry_count=retry_count,
            registry=registry,
        )

    # -- command resolution ------------------------------------------------

    def _resolve_build_cmd(self, *, repo_id: str | None = None) -> str | None:
        """Return the build command: per-repo → template override → pipeline override → settings fallback.

        If the template sets build_cmd to '' (empty string), the build gate
        is explicitly skipped. Returns None if disabled via forge.toml.
        """
        if repo_id:
            repo_configs = getattr(self, "_repo_configs", {})
            if repo_id in repo_configs:
                cfg = repo_configs[repo_id]
                if cfg.build.cmd:
                    return cfg.build.cmd
                if not cfg.build.enabled:
                    return None
        template_config = getattr(self, "_template_config", None)
        if template_config and "build_cmd" in template_config:
            val = template_config["build_cmd"]
            return val if val else None
        result = getattr(self, "_pipeline_build_cmd", None) or getattr(
            self._settings, "build_cmd", None
        )
        return None if result == CMD_DISABLED else result

    def _resolve_test_cmd(self, *, repo_id: str | None = None) -> str | None:
        """Return the test command: per-repo → template override → pipeline override → settings fallback.

        If the template sets test_cmd to '' (empty string), the test gate
        is explicitly skipped. Returns None if disabled via forge.toml.
        """
        if repo_id:
            repo_configs = getattr(self, "_repo_configs", {})
            if repo_id in repo_configs:
                cfg = repo_configs[repo_id]
                if cfg.tests.cmd:
                    return cfg.tests.cmd
                if not cfg.tests.enabled:
                    return None
        template_config = getattr(self, "_template_config", None)
        if template_config and "test_cmd" in template_config:
            val = template_config["test_cmd"]
            return val if val else None
        result = getattr(self, "_pipeline_test_cmd", None) or getattr(
            self._settings, "test_cmd", None
        )
        return None if result == CMD_DISABLED else result

    def _resolve_lint_cmd(self, *, repo_id: str | None = None) -> str | None:
        """Return the lint check command: per-repo → template override → settings fallback.

        Returns None if disabled via forge.toml.
        """
        if repo_id:
            repo_configs = getattr(self, "_repo_configs", {})
            if repo_id in repo_configs:
                cfg = repo_configs[repo_id]
                if cfg.lint.check_cmd:
                    return cfg.lint.check_cmd
                if not cfg.lint.enabled:
                    return None
        template_config = getattr(self, "_template_config", None)
        if template_config and "lint_cmd" in template_config:
            val = template_config["lint_cmd"]
            return val if val else None
        result = getattr(self._settings, "lint_cmd", None)
        return None if result == CMD_DISABLED else result

    def _resolve_lint_fix_cmd(self, *, repo_id: str | None = None) -> str | None:
        """Return the lint fix command: per-repo → template override → settings fallback.

        Returns None if disabled via forge.toml.
        """
        if repo_id:
            repo_configs = getattr(self, "_repo_configs", {})
            if repo_id in repo_configs:
                cfg = repo_configs[repo_id]
                if cfg.lint.fix_cmd:
                    return cfg.lint.fix_cmd
                if not cfg.lint.enabled:
                    return None
        template_config = getattr(self, "_template_config", None)
        if template_config and "lint_fix_cmd" in template_config:
            val = template_config["lint_fix_cmd"]
            return val if val else None
        result = getattr(self._settings, "lint_fix_cmd", None)
        return None if result == CMD_DISABLED else result

    # -- shell gate helpers ------------------------------------------------

    async def _gate_build(self, worktree_path: str, build_cmd: str, timeout: int) -> GateResult:
        """Gate 0: Build gate — run the project build command."""
        return await self._run_shell_gate(
            worktree_path, build_cmd, timeout, gate_name="gate0_build"
        )

    async def _gate_test(
        self,
        worktree_path: str,
        test_cmd: str,
        timeout: int,
        *,
        changed_files: list[str] | None = None,
        allowed_files: list[str] | None = None,
        pipeline_branch: str | None = None,
    ) -> GateResult:
        """Gate 1.5: Test gate — run the project test command.

        When *allowed_files* is provided, only in-scope tests are run as blocking.
        Out-of-scope tests are logged and skipped.
        """
        if changed_files and _is_pytest_cmd(test_cmd):
            if allowed_files is not None:
                result = await _find_related_test_files(
                    worktree_path,
                    changed_files,
                    allowed_files=allowed_files,
                    base_ref=pipeline_branch or "main",
                )
                if isinstance(result, tuple):
                    in_scope, out_of_scope = result
                else:
                    in_scope, out_of_scope = result, []

                for skipped in out_of_scope:
                    logger.info(
                        "Skipping out-of-scope test: %s (not in task files)",
                        skipped,
                    )

                if not in_scope:
                    return GateResult(
                        passed=True,
                        gate="gate1_5_test",
                        details="No in-scope test files found — skipped"
                        + (f" (skipped {len(out_of_scope)} out-of-scope)" if out_of_scope else ""),
                    )
                test_files = in_scope
            else:
                test_files = await _find_related_test_files(worktree_path, changed_files)
                if not test_files:
                    return GateResult(
                        passed=True,
                        gate="gate1_5_test",
                        details="No test files found for changed files — skipped",
                    )

            scoped_cmd = f"{test_cmd} {' '.join(test_files)}"
            logger.info(
                "Test gate scoped to %d test file(s): %s",
                len(test_files),
                ", ".join(test_files),
            )
            return await self._run_shell_gate(
                worktree_path,
                scoped_cmd,
                timeout,
                gate_name="gate1_5_test",
            )
        return await self._run_shell_gate(
            worktree_path, test_cmd, timeout, gate_name="gate1_5_test"
        )

    # Patterns that indicate environment/infra problems, not code problems.
    # If a gate fails with one of these, it's not the agent's fault.
    _INFRA_ERROR_PATTERNS = (
        "ModuleNotFoundError",
        "ImportError",
        "No module named",
        "command not found",
        "No such file or directory",
        "Permission denied",
        "not recognized as an internal or external command",
        "SyntaxError: future feature annotations",  # Python version mismatch
        "ERROR collecting",  # pytest collection error (usually import issue)
    )

    async def _run_shell_gate(
        self,
        worktree_path: str,
        cmd: str,
        timeout: int,
        *,
        gate_name: str,
    ) -> GateResult:
        """Execute a shell command as a review gate.

        Runs *cmd* inside *worktree_path* with a timeout.  Uses the gate
        semaphore to limit concurrent subprocess-heavy operations.

        If the failure output matches known infrastructure error patterns
        (missing modules, wrong Python version, command not found), the result
        is marked with ``infra_error=True`` so callers can skip the gate
        instead of consuming a retry.
        """
        gate_sem = getattr(self, "_gate_semaphore", None)
        if gate_sem:
            await gate_sem.acquire()
        try:
            try:
                # Use shell=True for commands with shell operators (&&, ||, ;, |)
                # so they execute correctly. Plain commands use exec for safety.
                # Use space-padded operators to avoid false matches in file paths
                # or arguments (e.g., "echo hello|world" shouldn't trigger shell).
                _shell_operators = (" && ", " || ", " ; ", " | ", " > ", " >> ", " < ")
                if any(op in f" {cmd} " for op in _shell_operators):
                    proc = await _async_shell(cmd, cwd=worktree_path, timeout=timeout)
                else:
                    parts = shlex.split(cmd)
                    proc = await async_subprocess(parts, cwd=worktree_path, timeout=timeout)
            except TimeoutError:
                return GateResult(
                    passed=False,
                    gate=gate_name,
                    details=f"Command timed out after {timeout}s: {cmd}",
                    retriable=True,
                )
            except FileNotFoundError:
                # The command binary itself doesn't exist (e.g. ruff not installed)
                return GateResult(
                    passed=False,
                    gate=gate_name,
                    details=f"Command not found: {cmd}",
                    infra_error=True,
                )

            combined = (proc.stdout or "") + (proc.stderr or "")
            # Keep last 5000 chars so we see the tail of build/test output
            truncated = combined[-5000:] if len(combined) > 5000 else combined

            if proc.returncode == 0:
                return GateResult(passed=True, gate=gate_name, details="OK")

            # Check if this is an infrastructure failure, not a code problem
            is_infra = any(pattern in combined for pattern in self._INFRA_ERROR_PATTERNS)

            return GateResult(
                passed=False,
                gate=gate_name,
                details=f"Exit code {proc.returncode}:\n{truncated}",
                infra_error=is_infra,
            )
        finally:
            if gate_sem:
                gate_sem.release()

    # -- streaming helpers --------------------------------------------------

    def _make_review_on_message(self, task_id: str, db, pipeline_id: str):
        """Build a batched on_message callback for LLM review streaming.

        Returns (callback, flush) where *flush* drains any remaining
        buffered lines.  The pattern mirrors ``_stream_agent`` in
        daemon_executor.

        Accepts both legacy SDK messages and ProviderEvent objects
        (via _extract_text which handles both).
        """
        _last_flush = [time.monotonic()]
        _batch: list[str] = []
        _MAX_BATCH_SIZE = 50

        async def _on_msg(msg):
            # _extract_activity handles text, tool-use, and provider status updates.
            text = _extract_activity(msg) or _extract_text(msg)
            if not text:
                return
            self._record_health_activity(task_id)
            _batch.append(text)
            now = time.monotonic()
            if now - _last_flush[0] >= 0.1 or len(_batch) >= _MAX_BATCH_SIZE:
                for line in _batch:
                    await self._emit(
                        "review:llm_output",
                        {"task_id": task_id, "line": line},
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                _batch.clear()
                _last_flush[0] = now

        async def _flush():
            for line in _batch:
                await self._emit(
                    "review:llm_output",
                    {"task_id": task_id, "line": line},
                    db=db,
                    pipeline_id=pipeline_id,
                )
            _batch.clear()

        return _on_msg, _flush

    def _make_review_event_callback(self, task_id: str, db, pipeline_id: str):
        """Build an on_review_event callback that translates review progress events
        into WebSocket events for the TUI.

        This decouples llm_review.py from the WebSocket/DB layer.
        """

        async def _on_review_event(event_name: str, payload: dict) -> None:
            # Inject task_id into every event payload
            self._record_health_activity(task_id)
            full_payload = {"task_id": task_id, **payload}
            await self._emit(event_name, full_payload, db=db, pipeline_id=pipeline_id)
            progress_line = self._format_review_progress_line(event_name, payload)
            if progress_line:
                await self._emit(
                    "review:llm_output",
                    {"task_id": task_id, "line": progress_line},
                    db=db,
                    pipeline_id=pipeline_id,
                )

        return _on_review_event

    @staticmethod
    def _format_review_progress_line(event_name: str, payload: dict) -> str | None:
        """Render review progress events as user-visible activity lines."""
        if event_name == "review:strategy_selected":
            strategy = payload.get("strategy", "unknown")
            chunk_count = payload.get("chunk_count")
            diff_lines = payload.get("diff_lines")
            details: list[str] = [f"strategy={strategy}"]
            if chunk_count is not None:
                details.append(f"chunks={chunk_count}")
            if diff_lines is not None:
                details.append(f"diff_lines={diff_lines}")
            return "Review strategy selected: " + ", ".join(details)

        if event_name == "review:chunk_started":
            idx = payload.get("chunk_index")
            total = payload.get("chunk_total")
            risk = payload.get("risk_label")
            files = payload.get("files") or []
            suffix: list[str] = []
            if risk:
                suffix.append(f"risk={risk}")
            if files:
                suffix.append(f"files={len(files)}")
            suffix_str = f" ({', '.join(suffix)})" if suffix else ""
            return f"Review chunk {idx}/{total} started{suffix_str}"

        if event_name == "review:chunk_complete":
            idx = payload.get("chunk_index")
            total = payload.get("chunk_total")
            verdict = payload.get("verdict", "UNKNOWN")
            issues = payload.get("issue_count")
            issues_str = f", issues={issues}" if issues is not None else ""
            return f"Review chunk {idx}/{total} complete: {verdict}{issues_str}"

        if event_name == "review:synthesis_started":
            return "Review synthesis started"

        if event_name == "review:timeout":
            timeout_seconds = payload.get("timeout_seconds", "?")
            attempt = payload["attempt"]
            max_attempts = payload["max_attempts"]
            return (
                f"L2 review timed out after {timeout_seconds}s (attempt {attempt}/{max_attempts})"
            )

        if event_name == "review:retry":
            attempt = payload["attempt"]
            max_attempts = payload["max_attempts"]
            return f"Retrying review attempt {attempt}/{max_attempts}"

        return None

    # -- main review pipeline ----------------------------------------------

    async def _build_sibling_context(self, task, db, pipeline_id: str) -> str | None:
        """Build a context section describing sibling tasks in the same pipeline.

        Gives the reviewer awareness of the DAG so it doesn't fail reviews
        for cross-task concerns that another task handles (e.g. route
        registration in app.py when app.py belongs to a sibling task).
        """
        if not pipeline_id:
            return None

        all_tasks = await db.list_tasks_by_pipeline(pipeline_id)
        if len(all_tasks) <= 1:
            return None  # Solo task, no siblings

        lines = [
            "## Pipeline Task Context (DAG Awareness)",
            "",
            "This task is part of a multi-task pipeline. Other sibling tasks handle "
            "different parts of the implementation. Do NOT fail this review for "
            "missing functionality that belongs to another task's scope.",
            "",
        ]

        for sibling in all_tasks:
            if sibling.id == task.id:
                continue
            files_str = ", ".join((sibling.files or [])[:5])
            if sibling.files and len(sibling.files) > 5:
                files_str += f"... (+{len(sibling.files) - 5} more)"
            if not files_str:
                files_str = "(none)"

            # Show dependency relationship to help reviewer understand task boundaries
            dep_info = ""
            task_deps = getattr(task, "depends_on", None) or []
            sibling_deps = getattr(sibling, "depends_on", None) or []
            if sibling.id in task_deps:
                dep_info = " ← THIS TASK DEPENDS ON IT"
            if task.id in sibling_deps:
                dep_info = " ← DEPENDS ON THIS TASK (will wire later)"

            lines.append(
                f"- **{sibling.id}** ({sibling.title}): "
                f"files=[{files_str}], state={sibling.state}{dep_info}"
            )

            # Show what the sibling does so reviewer understands scope boundaries
            desc = getattr(sibling, "description", None)
            if desc:
                preview = desc[:200].replace("\n", " ")
                if len(desc) > 200:
                    preview += "..."
                lines.append(f"  What it does: {preview}")

        lines.append("")
        lines.append(
            "IMPORTANT: If the task description mentions wiring/integration that touches files "
            "owned by a sibling task, do NOT fail the review for that missing wiring. "
            "Specifically, if a sibling task DEPENDS ON this task (marked above), it will "
            "handle integration into its own files AFTER this task completes. "
            "Only review code changes within this task's allowed files."
        )

        return "\n".join(lines)

    async def _run_review(
        self,
        task,
        worktree_path: str,
        diff: str,
        *,
        db,
        pipeline_id: str,
        pipeline_branch: str | None = None,
        delta_diff: str | None = None,
        repo_id: str | None = None,
    ) -> tuple[bool, str | None, bool]:
        """Run the 3-gate review pipeline.

        Args:
            pipeline_branch: The pipeline branch ref used as the diff base
                for ``_get_changed_files_vs_main`` in the lint gate.
            delta_diff: On retry, the diff of only what the retry agent
                changed (pre_retry_ref..HEAD). Helps the reviewer focus
                on the fix rather than re-reading the full accumulated diff.

        Returns:
            (passed, feedback, needs_human) — feedback is a string with
            failure details if any gate failed, None if all passed.
            needs_human is True when the review could not complete and
            should be escalated to human decision.
        """
        feedback_parts: list[str] = []
        gate_timeout = self._settings.agent_timeout_seconds // 2
        review_t0 = time.monotonic()
        build_validation_line = "- Build gate: SKIPPED — no build command configured"
        lint_validation_line = "- Lint gate: SKIPPED — lint gate not run"
        test_validation_line = "- Test gate: SKIPPED — no test command configured"
        prefer_deep_review = False

        await self._emit("review:started", {"task_id": task.id}, db=db, pipeline_id=pipeline_id)

        # Gate 0: Build gate (skip silently if no command configured)
        build_cmd = self._resolve_build_cmd(repo_id=repo_id)
        if build_cmd:
            console.print(f"[blue]  Gate 0 (build): Running build for {task.id}...[/blue]")
            await self._emit(
                "review:gate_started",
                {
                    "task_id": task.id,
                    "gate": "gate0_build",
                },
                db=db,
                pipeline_id=pipeline_id,
            )
            build_result = await self._gate_build(worktree_path, build_cmd, gate_timeout)
            await self._emit(
                "review:gate_passed" if build_result.passed else "review:gate_failed",
                {"task_id": task.id, "gate": "gate0_build", "details": build_result.details},
                db=db,
                pipeline_id=pipeline_id,
            )
            await self._emit(
                "task:review_update",
                {
                    "task_id": task.id,
                    "gate": "Gate0_Build",
                    "passed": build_result.passed,
                    "details": build_result.details,
                },
                db=db,
                pipeline_id=pipeline_id,
            )
            if not build_result.passed:
                if build_result.infra_error:
                    build_validation_line = (
                        f"- Build gate: SKIPPED (infra error) — {build_result.details[:200]}"
                    )
                    prefer_deep_review = True
                    console.print(
                        f"[yellow]  Gate 0 (build) skipped — environment error: {build_result.details[:200]}[/yellow]"
                    )
                    await self._emit(
                        "task:review_update",
                        {
                            "task_id": task.id,
                            "gate": "gate0_build",
                            "passed": True,
                            "skipped": True,
                            "details": f"Skipped (infra error): {build_result.details[:200]}",
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                else:
                    console.print(f"[red]  Gate 0 (build) failed: {build_result.details}[/red]")
                    feedback_parts.append(f"Gate 0 (build) FAILED:\n{build_result.details}")
                    try:
                        await db.set_task_timing(
                            task.id, review_duration_s=time.monotonic() - review_t0
                        )
                    except Exception:
                        pass
                    await self._emit(
                        "review:failed",
                        {
                            "task_id": task.id,
                            "gate": "gate0_build",
                            "details": build_result.details,
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                    return False, "\n\n".join(feedback_parts), False
            else:
                build_validation_line = f"- Build gate: PASSED — {build_result.details}"
                console.print("[green]  Gate 0 (build) passed[/green]")
        else:
            build_validation_line = "- Build gate: SKIPPED — no build command configured"
            console.print("[dim]  Gate 0 (build): skipped — no build command configured[/dim]")
            await self._emit(
                "task:review_update",
                {
                    "task_id": task.id,
                    "gate": "gate0_build",
                    "passed": True,
                    "skipped": True,
                    "details": "No build command configured",
                },
                db=db,
                pipeline_id=pipeline_id,
            )

        # Compute changed files once — used by both lint (L1) and test (Gate 1.5) gates
        changed_files = await _get_changed_files_vs_main(worktree_path, base_ref=pipeline_branch)

        # L1: lint only the changed files (not full test suite)
        console.print(f"[blue]  L1 (general): Auto-checks for {task.id}...[/blue]")
        await self._emit(
            "review:gate_started",
            {
                "task_id": task.id,
                "gate": "gate1_lint",
            },
            db=db,
            pipeline_id=pipeline_id,
        )
        lint_t0 = time.monotonic()
        gate1_result = await self._run_lint_gate(
            worktree_path, pipeline_branch=pipeline_branch, repo_id=repo_id, db=db
        )
        lint_elapsed = time.monotonic() - lint_t0
        try:
            await db.set_task_timing(task.id, lint_duration_s=lint_elapsed)
        except Exception:
            logger.debug("Failed to record lint_duration_s for %s", task.id, exc_info=True)
        await self._emit(
            "review:gate_passed" if gate1_result.passed else "review:gate_failed",
            {"task_id": task.id, "gate": "gate1_lint", "details": gate1_result.details},
            db=db,
            pipeline_id=pipeline_id,
        )
        await self._emit(
            "task:review_update",
            {
                "task_id": task.id,
                "gate": "L1",
                "passed": gate1_result.passed,
                "details": gate1_result.details,
            },
            db=db,
            pipeline_id=pipeline_id,
        )
        if not gate1_result.passed:
            if gate1_result.infra_error:
                lint_validation_line = (
                    f"- Lint gate: SKIPPED (infra error) — {gate1_result.details[:200]}"
                )
                prefer_deep_review = True
                console.print(
                    f"[yellow]  L1 (lint) skipped — environment error: {gate1_result.details[:200]}[/yellow]"
                )
                await self._emit(
                    "task:review_update",
                    {
                        "task_id": task.id,
                        "gate": "L1",
                        "passed": True,
                        "skipped": True,
                        "details": f"Skipped (infra error): {gate1_result.details[:200]}",
                    },
                    db=db,
                    pipeline_id=pipeline_id,
                )
            else:
                console.print(f"[red]  L1 failed: {gate1_result.details}[/red]")
                prefix = "[RETRIABLE] " if gate1_result.retriable else ""
                feedback_parts.append(f"{prefix}L1 (lint) FAILED:\n{gate1_result.details}")
                try:
                    await db.set_task_timing(
                        task.id, review_duration_s=time.monotonic() - review_t0
                    )
                except Exception:
                    pass
                await self._emit(
                    "review:failed",
                    {"task_id": task.id, "gate": "gate1_lint", "details": gate1_result.details},
                    db=db,
                    pipeline_id=pipeline_id,
                )
                return False, "\n\n".join(feedback_parts), False
        else:
            lint_validation_line = f"- Lint gate: PASSED — {gate1_result.details}"
            console.print("[green]  L1 passed[/green]")

        # L1 may have auto-fixed lint issues (unused imports, etc.) and
        # committed the result.  Recompute the diff so L2 reviews the
        # post-fix code rather than the stale pre-fix diff the caller
        # captured before the review pipeline ran.
        if pipeline_branch:
            from forge.core.daemon_helpers import _get_diff_vs_main

            diff = await _get_diff_vs_main(worktree_path, base_ref=pipeline_branch)

        # Gate 1.5: Test gate — scoped to changed files when possible
        test_cmd = self._resolve_test_cmd(repo_id=repo_id)
        if test_cmd:
            console.print(f"[blue]  Gate 1.5 (test): Running tests for {task.id}...[/blue]")
            await self._emit(
                "review:gate_started",
                {
                    "task_id": task.id,
                    "gate": "gate1_5_test",
                },
                db=db,
                pipeline_id=pipeline_id,
            )
            test_result = await self._gate_test(
                worktree_path,
                test_cmd,
                gate_timeout,
                changed_files=changed_files,
                allowed_files=getattr(task, "files", None),
                pipeline_branch=pipeline_branch,
            )
            await self._emit(
                "review:gate_passed" if test_result.passed else "review:gate_failed",
                {"task_id": task.id, "gate": "gate1_5_test", "details": test_result.details},
                db=db,
                pipeline_id=pipeline_id,
            )
            await self._emit(
                "task:review_update",
                {
                    "task_id": task.id,
                    "gate": "Gate1_5_Test",
                    "passed": test_result.passed,
                    "details": test_result.details,
                },
                db=db,
                pipeline_id=pipeline_id,
            )
            if not test_result.passed:
                if test_result.infra_error:
                    test_validation_line = (
                        f"- Test gate: SKIPPED (infra error) — {test_result.details[:200]}"
                    )
                    prefer_deep_review = True
                    console.print(
                        f"[yellow]  Gate 1.5 (test) skipped — environment error: {test_result.details[:200]}[/yellow]"
                    )
                    await self._emit(
                        "task:review_update",
                        {
                            "task_id": task.id,
                            "gate": "gate1_5_test",
                            "passed": True,
                            "skipped": True,
                            "details": f"Skipped (infra error): {test_result.details[:200]}",
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                else:
                    console.print(f"[red]  Gate 1.5 (test) failed: {test_result.details}[/red]")
                    feedback_parts.append(f"Gate 1.5 (test) FAILED:\n{test_result.details}")
                    try:
                        await db.set_task_timing(
                            task.id, review_duration_s=time.monotonic() - review_t0
                        )
                    except Exception:
                        pass
                    await self._emit(
                        "review:failed",
                        {
                            "task_id": task.id,
                            "gate": "gate1_5_test",
                            "details": test_result.details,
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                    return False, "\n\n".join(feedback_parts), False
            else:
                test_validation_line = f"- Test gate: PASSED — {test_result.details}"
            console.print("[green]  Gate 1.5 (test) passed[/green]")
        else:
            test_validation_line = "- Test gate: SKIPPED — no test command configured"
            prefer_deep_review = True
            console.print("[dim]  Gate 1.5 (test): skipped — no test command configured[/dim]")
            await self._emit(
                "task:review_update",
                {
                    "task_id": task.id,
                    "gate": "gate1_5_test",
                    "passed": True,
                    "skipped": True,
                    "details": "No test command configured",
                },
                db=db,
                pipeline_id=pipeline_id,
            )

        # L2: LLM review
        # Load review config from template
        review_config = self._get_review_config()

        if review_config["skip_l2"]:
            console.print("[yellow]  L2 skipped by template[/yellow]")
            await self._emit(
                "task:review_update",
                {
                    "task_id": task.id,
                    "gate": "L2",
                    "passed": True,
                    "details": "Skipped by template configuration",
                },
                db=db,
                pipeline_id=pipeline_id,
            )
        else:
            # Pass prior feedback + prior diff so the reviewer focuses on
            # verifying fixes instead of inventing new complaints on every retry.
            prior_feedback = (
                getattr(task, "review_feedback", None) if task.retry_count > 0 else None
            )
            prior_diff = getattr(task, "prior_diff", None) if task.retry_count > 0 else None
            validation_context = (
                "## Validation Context\n"
                f"{build_validation_line}\n"
                f"{lint_validation_line}\n"
                f"{test_validation_line}\n\n"
                "Treat skipped or infra-errored validation as reduced coverage: inspect the current code"
                " more deeply and use focused tools/commands where that helps confirm correctness."
            )
            console.print(
                f"[blue]  L2 (LLM): Code review for {task.id}"
                f"{'  (re-review)' if prior_feedback else ''}...[/blue]"
            )
            reviewer_model = self._select_model("reviewer", task.complexity or "medium")
            reviewer_effort = None
            resolve_effort = getattr(self._settings, "resolve_reasoning_effort", None)
            if callable(resolve_effort):
                reviewer_effort = resolve_effort("reviewer", task.complexity or "medium")
            # Build sibling context so the reviewer knows about the DAG
            sibling_context = await self._build_sibling_context(task, db, pipeline_id)
            custom_review_focus = review_config["custom_review_focus"]
            # Inject contract compliance into review focus
            if pipeline_id:
                contracts_json = await db.get_pipeline_contracts(pipeline_id)
                if contracts_json:
                    from forge.core.contracts import ContractSet as CS

                    try:
                        contract_set = CS.model_validate_json(contracts_json)
                        task_contracts = contract_set.contracts_for_task(task.id)
                        contract_review = task_contracts.format_for_reviewer()
                        if contract_review:
                            if custom_review_focus:
                                custom_review_focus = f"{custom_review_focus}\n\n{contract_review}"
                            else:
                                custom_review_focus = contract_review
                    except Exception:
                        logger.warning("Failed to parse contracts for review of task %s", task.id)
                        await self._emit(
                            "task:review_update",
                            {
                                "task_id": task.id,
                                "gate": "contract_loading",
                                "passed": True,
                                "details": "Contract loading failed — reviewing without contract compliance checks",
                            },
                            db=db,
                            pipeline_id=pipeline_id,
                        )
            await self._emit(
                "review:gate_started",
                {
                    "task_id": task.id,
                    "gate": "gate2_llm_review",
                },
                db=db,
                pipeline_id=pipeline_id,
            )
            review_start_line = f"Starting review ({_humanize_model_spec(reviewer_model)}"
            if reviewer_effort:
                review_start_line += f", {reviewer_effort} reasoning"
            review_start_line += ")…"
            await self._emit(
                "review:llm_output",
                {"task_id": task.id, "line": review_start_line},
                db=db,
                pipeline_id=pipeline_id,
            )
            on_message, flush_review = self._make_review_on_message(
                task.id,
                db,
                pipeline_id,
            )
            # Build on_review_event callback for review progress events
            on_review_event = self._make_review_event_callback(task.id, db, pipeline_id)

            # Load adaptive review settings from project config
            _review_cfg = getattr(getattr(self, "_project_config", None), "review", None)
            gate2_result, review_cost_info = await gate2_llm_review(
                task.title,
                task.description,
                diff,
                worktree_path,
                model=reviewer_model,
                prior_feedback=prior_feedback,
                prior_diff=prior_diff,
                project_context=self._snapshot.format_for_reviewer() if self._snapshot else "",
                allowed_files=task.files,
                delta_diff=delta_diff,
                sibling_context=sibling_context,
                validation_context=validation_context,
                custom_review_focus=custom_review_focus,
                prefer_deep_review=prefer_deep_review or bool(prior_feedback),
                on_message=on_message,
                on_review_event=on_review_event,
                adaptive_review=_review_cfg.adaptive_review if _review_cfg else True,
                medium_diff_threshold=_review_cfg.medium_diff_threshold if _review_cfg else 400,
                large_diff_threshold=_review_cfg.large_diff_threshold if _review_cfg else 2000,
                max_chunk_lines=_review_cfg.max_chunk_lines if _review_cfg else 600,
                registry=getattr(self, "_registry", None),
            )
            await flush_review()
            # Emit LLM feedback so the TUI can display reviewer comments
            await self._emit(
                "review:llm_feedback",
                {
                    "task_id": task.id,
                    "feedback": gate2_result.details,
                },
                db=db,
                pipeline_id=pipeline_id,
            )
            # Track review cost
            if review_cost_info.cost_usd > 0:
                await db.add_task_review_cost(task.id, review_cost_info.cost_usd)
                await db.add_pipeline_cost(pipeline_id, review_cost_info.cost_usd)
                await self._emit(
                    "task:cost_update",
                    {
                        "task_id": task.id,
                        "review_cost_usd": review_cost_info.cost_usd,
                        "input_tokens": review_cost_info.input_tokens,
                        "output_tokens": review_cost_info.output_tokens,
                    },
                    db=db,
                    pipeline_id=pipeline_id,
                )
                total_cost = await db.get_pipeline_cost(pipeline_id)
                await self._emit(
                    "pipeline:cost_update",
                    {
                        "total_cost_usd": total_cost,
                    },
                    db=db,
                    pipeline_id=pipeline_id,
                )
            await self._emit(
                "review:gate_passed" if gate2_result.passed else "review:gate_failed",
                {"task_id": task.id, "gate": "gate2_llm_review", "details": gate2_result.details},
                db=db,
                pipeline_id=pipeline_id,
            )
            await self._emit(
                "task:review_update",
                {
                    "task_id": task.id,
                    "gate": "L2",
                    "passed": gate2_result.passed,
                    "details": gate2_result.details,
                },
                db=db,
                pipeline_id=pipeline_id,
            )
            if gate2_result.needs_human:
                console.print(
                    f"[yellow]  L2: escalating to human — {gate2_result.details[:100]}[/yellow]"
                )
                try:
                    await db.set_task_timing(
                        task.id, review_duration_s=time.monotonic() - review_t0
                    )
                except Exception:
                    pass
                await self._emit(
                    "review:failed",
                    {
                        "task_id": task.id,
                        "gate": "gate2_llm_review",
                        "details": gate2_result.details,
                    },
                    db=db,
                    pipeline_id=pipeline_id,
                )
                return False, gate2_result.details, True
            if not gate2_result.passed:
                console.print(f"[red]  L2 failed: {gate2_result.details}[/red]")
                prefix = "[RETRIABLE] " if gate2_result.retriable else ""
                feedback_parts.append(
                    f"{prefix}L2 (LLM code review) FAILED:\n{gate2_result.details}"
                )
                try:
                    await db.set_task_timing(
                        task.id, review_duration_s=time.monotonic() - review_t0
                    )
                except Exception:
                    pass
                await self._emit(
                    "review:failed",
                    {
                        "task_id": task.id,
                        "gate": "gate2_llm_review",
                        "details": gate2_result.details,
                    },
                    db=db,
                    pipeline_id=pipeline_id,
                )
                return False, "\n\n".join(feedback_parts), False
            console.print("[green]  L2 passed[/green]")

            # Extra review pass: run L2 a second time if configured
            if review_config["extra_review_pass"]:
                console.print(f"[blue]  L2 (extra pass): Second review for {task.id}...[/blue]")
                extra_focus = custom_review_focus
                if extra_focus:
                    extra_focus += "\n\n"
                extra_focus += (
                    "This is a SECOND REVIEW PASS. A previous reviewer already "
                    "approved. Catch anything they missed."
                )
                extra_start_line = f"Starting extra review ({_humanize_model_spec(reviewer_model)}"
                if reviewer_effort:
                    extra_start_line += f", {reviewer_effort} reasoning"
                extra_start_line += ")…"
                await self._emit(
                    "review:llm_output",
                    {"task_id": task.id, "line": extra_start_line},
                    db=db,
                    pipeline_id=pipeline_id,
                )
                gate2_extra, extra_cost_info = await gate2_llm_review(
                    task.title,
                    task.description,
                    diff,
                    worktree_path,
                    model=reviewer_model,
                    project_context=self._snapshot.format_for_reviewer() if self._snapshot else "",
                    allowed_files=task.files,
                    sibling_context=sibling_context,
                    validation_context=validation_context,
                    custom_review_focus=extra_focus,
                    prefer_deep_review=True,
                    on_review_event=on_review_event,
                    adaptive_review=_review_cfg.adaptive_review if _review_cfg else True,
                    medium_diff_threshold=_review_cfg.medium_diff_threshold if _review_cfg else 400,
                    large_diff_threshold=_review_cfg.large_diff_threshold if _review_cfg else 2000,
                    max_chunk_lines=_review_cfg.max_chunk_lines if _review_cfg else 600,
                    registry=getattr(self, "_registry", None),
                )
                # Track extra review cost
                if extra_cost_info.cost_usd > 0:
                    await db.add_task_review_cost(task.id, extra_cost_info.cost_usd)
                    await db.add_pipeline_cost(pipeline_id, extra_cost_info.cost_usd)
                    await self._emit(
                        "task:cost_update",
                        {
                            "task_id": task.id,
                            "review_cost_usd": extra_cost_info.cost_usd,
                            "input_tokens": extra_cost_info.input_tokens,
                            "output_tokens": extra_cost_info.output_tokens,
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                    total_cost = await db.get_pipeline_cost(pipeline_id)
                    await self._emit(
                        "pipeline:cost_update",
                        {
                            "total_cost_usd": total_cost,
                        },
                        db=db,
                        pipeline_id=pipeline_id,
                    )
                await self._emit(
                    "task:review_update",
                    {
                        "task_id": task.id,
                        "gate": "L2_extra",
                        "passed": gate2_extra.passed,
                        "details": gate2_extra.details,
                    },
                    db=db,
                    pipeline_id=pipeline_id,
                )
                if not gate2_extra.passed:
                    if gate2_extra.retriable:
                        # Transient failure (empty response, SDK timeout) on the
                        # extra pass — the primary L2 already approved.  Don't
                        # waste retries re-running the whole pipeline; treat as pass.
                        console.print(
                            "[yellow]  L2 (extra pass) transient failure — skipping (primary L2 passed)[/yellow]"
                        )
                        await self._emit(
                            "task:review_update",
                            {
                                "task_id": task.id,
                                "gate": "L2_extra",
                                "passed": True,
                                "skipped": True,
                                "details": "Transient failure — skipped (primary L2 passed)",
                            },
                            db=db,
                            pipeline_id=pipeline_id,
                        )
                    else:
                        console.print(f"[red]  L2 (extra pass) failed: {gate2_extra.details}[/red]")
                        feedback_parts.append(f"L2 extra pass FAILED:\n{gate2_extra.details}")
                        try:
                            await db.set_task_timing(
                                task.id, review_duration_s=time.monotonic() - review_t0
                            )
                        except Exception:
                            pass
                        await self._emit(
                            "review:failed",
                            {
                                "task_id": task.id,
                                "gate": "gate2_llm_review_extra",
                                "details": gate2_extra.details,
                            },
                            db=db,
                            pipeline_id=pipeline_id,
                        )
                        return False, "\n\n".join(feedback_parts), False
                else:
                    console.print("[green]  L2 (extra pass) passed[/green]")

        # Gate 3: skip for now — merge check is handled by merge_worker
        console.print("[dim]  Gate 3 (merge readiness): deferred to merge step[/dim]")
        # Record review duration
        try:
            review_elapsed = time.monotonic() - review_t0
            await db.set_task_timing(task.id, review_duration_s=review_elapsed)
        except Exception:
            logger.debug("Failed to record review_duration_s for %s", task.id, exc_info=True)
        await self._emit("review:passed", {"task_id": task.id}, db=db, pipeline_id=pipeline_id)
        return True, None, False

    async def _run_lint_gate(
        self,
        worktree_path: str,
        *,
        pipeline_branch: str | None = None,
        repo_id: str | None = None,
        db=None,
    ) -> GateResult:
        """Gate 1: Language-agnostic lint check on the worktree.

        Uses LintStrategy detection to support any language/toolchain.
        Two-pass model: fix + commit, then verify clean.
        """
        # Get changed files and filter out deleted ones
        changed = await _get_changed_files_vs_main(worktree_path, base_ref=pipeline_branch)
        changed_files = [f for f in changed if os.path.isfile(os.path.join(worktree_path, f))]

        if not changed_files:
            return GateResult(passed=True, gate="gate1_auto_check", details="No files changed")

        # Resolve overrides
        lint_cmd_override = self._resolve_lint_cmd(repo_id=repo_id)
        lint_fix_cmd_override = self._resolve_lint_fix_cmd(repo_id=repo_id)

        strategies = detect_all_lint_strategies(
            worktree_path,
            changed_files,
            lint_cmd_override=lint_cmd_override,
            lint_fix_cmd_override=lint_fix_cmd_override,
        )

        if not strategies:
            return GateResult(passed=True, gate="gate1_auto_check", details="No linter detected")

        # Run each strategy against its matching files. Fail if ANY strategy fails.
        all_passed = True
        all_details: list[str] = []
        for strategy in strategies:
            result = await self._run_single_lint(
                worktree_path, strategy, changed_files, pipeline_branch, db
            )
            if not result.passed:
                all_passed = False
            all_details.append(f"{strategy.name}: {result.details}")

        if all_passed:
            return GateResult(
                passed=True,
                gate="gate1_auto_check",
                details="; ".join(all_details),
            )
        return GateResult(
            passed=False,
            gate="gate1_auto_check",
            details="\n".join(all_details),
            retriable=True,
        )

    async def _run_single_lint(
        self,
        worktree_path: str,
        strategy: LintStrategy,
        changed_files: list[str],
        pipeline_branch: str | None,
        db=None,
    ) -> GateResult:
        """Run a single lint strategy against its matching files."""
        lint_cwd = worktree_path
        # Filter files to only those matching the linter's supported extensions.
        if strategy.extensions and strategy.supports_file_args:
            lint_files = [
                f for f in changed_files if os.path.splitext(f)[1].lower() in strategy.extensions
            ]
            if not lint_files:
                return GateResult(
                    passed=True,
                    gate="gate1_auto_check",
                    details=f"No {strategy.name}-eligible files changed",
                )
        else:
            lint_files = list(changed_files)
        if strategy.supports_file_args and lint_files:
            common_prefix = (
                os.path.commonpath(lint_files)
                if len(lint_files) > 1
                else os.path.dirname(lint_files[0])
            )
            # Walk up from common prefix to find a directory with package.json
            # (for JS/TS tools) or pyproject.toml (for Python tools)
            candidate = common_prefix
            while candidate:
                candidate_abs = os.path.join(worktree_path, candidate)
                has_pkg = os.path.isfile(os.path.join(candidate_abs, "package.json"))
                has_lint_config = any(
                    os.path.isfile(os.path.join(candidate_abs, cfg))
                    for cfg in (
                        "eslint.config.mjs",
                        "eslint.config.js",
                        ".eslintrc.js",
                        ".eslintrc.json",
                        "pyproject.toml",
                    )
                )
                if has_pkg or has_lint_config:
                    lint_cwd = candidate_abs
                    prefix = candidate + "/"
                    lint_files = [
                        f[len(prefix) :] if f.startswith(prefix) else f for f in lint_files
                    ]
                    logger.info("Lint cwd adjusted to %s (found config in subdirectory)", candidate)
                    break
                parent = os.path.dirname(candidate)
                if parent == candidate:
                    break
                candidate = parent

        # Build final commands
        fix_cmd = list(strategy.fix_cmd) if strategy.fix_cmd else None
        check_cmd = list(strategy.check_cmd)
        if strategy.supports_file_args:
            if fix_cmd is not None:
                fix_cmd += lint_files
            check_cmd += lint_files

        # Acquire gate semaphore to limit concurrent subprocess-heavy operations
        gate_sem = getattr(self, "_gate_semaphore", None)
        if gate_sem:
            await gate_sem.acquire()

        try:
            # Resolve timeout: use setting, but check lessons for adaptive override.
            lint_timeout = getattr(self, "_settings", None)
            lint_timeout = (
                lint_timeout.lint_timeout
                if lint_timeout and hasattr(lint_timeout, "lint_timeout")
                else 180
            )
            # Check if we have a learned timeout for this linter
            learned_timeout = await self._get_learned_lint_timeout(strategy.name, db=db)
            if learned_timeout and learned_timeout > lint_timeout:
                logger.info(
                    "Using learned lint timeout of %ds for %s (default: %ds)",
                    learned_timeout,
                    strategy.name,
                    lint_timeout,
                )
                lint_timeout = learned_timeout

            # PASS 1: Fix
            auto_fix_diff = ""
            if fix_cmd is not None:
                try:
                    await async_subprocess(fix_cmd, cwd=lint_cwd, timeout=lint_timeout)
                except TimeoutError:
                    logger.warning(
                        "Lint fix command timed out after %ds: %s",
                        lint_timeout,
                        " ".join(fix_cmd),
                    )
                    await self._learn_lint_timeout(strategy.name, lint_timeout, db=db)
                    return GateResult(
                        passed=False,
                        gate="gate1_auto_check",
                        details=f"Lint fix timed out after {lint_timeout}s: {' '.join(fix_cmd[:3])}...",
                        retriable=True,
                    )
                # Capture what changed
                diff_result = await _run_git(
                    ["diff"],
                    cwd=worktree_path,
                    check=False,
                    description=f"capture {strategy.name} auto-fix diff",
                )
                if diff_result.stdout.strip():
                    diff_lines = diff_result.stdout.splitlines()
                    if len(diff_lines) > 30:
                        auto_fix_diff = "\n".join(diff_lines[:30]) + "\n... (truncated)"
                    else:
                        auto_fix_diff = diff_result.stdout.strip()

                # Stage ONLY in-scope files — lint may have touched inherited
                # files from other pipeline tasks via the worktree base.
                # Using `git add -A` here would stage out-of-scope changes,
                # contaminating this task's diff with other tasks' files.
                modified_result = await _run_git(
                    ["diff", "--name-only"],
                    cwd=worktree_path,
                    check=False,
                    description="list lint-modified files",
                )
                modified_by_lint = {
                    f.strip() for f in modified_result.stdout.strip().split("\n") if f.strip()
                }
                # Only stage files that overlap with this task's changed_files
                in_scope_lint_fixes = sorted(modified_by_lint & set(changed_files))
                if in_scope_lint_fixes:
                    await _run_git(
                        ["add", "--"] + in_scope_lint_fixes,
                        cwd=worktree_path,
                        check=False,
                        description="stage scoped lint fixes",
                    )
                    # Discard any out-of-scope lint modifications
                    out_of_scope_lint = sorted(modified_by_lint - set(changed_files))
                    if out_of_scope_lint:
                        logger.info(
                            "Lint scope: discarding %d out-of-scope fix(es): %s",
                            len(out_of_scope_lint),
                            ", ".join(out_of_scope_lint[:5])
                            + ("..." if len(out_of_scope_lint) > 5 else ""),
                        )
                        await _run_git(
                            ["checkout", "--"] + out_of_scope_lint,
                            cwd=worktree_path,
                            check=False,
                            description="discard out-of-scope lint fixes",
                        )
                    await _run_git(
                        ["commit", "-m", strategy.commit_msg],
                        cwd=worktree_path,
                        check=False,
                        description="commit lint fixes",
                    )

            # PASS 2: Verify
            try:
                lint_result = await async_subprocess(check_cmd, cwd=lint_cwd, timeout=lint_timeout)
            except TimeoutError:
                logger.warning(
                    "Lint check command timed out after %ds: %s",
                    lint_timeout,
                    " ".join(check_cmd),
                )
                await self._learn_lint_timeout(strategy.name, lint_timeout, db=db)
                return GateResult(
                    passed=False,
                    gate="gate1_auto_check",
                    details=f"Lint check timed out after {lint_timeout}s: {' '.join(check_cmd[:3])}...",
                    retriable=True,
                )

            # Determine pass/fail
            if strategy.check_via_output:
                lint_clean = not lint_result.stdout.strip()
            else:
                lint_clean = lint_result.returncode == 0

            if lint_clean:
                if auto_fix_diff:
                    summary = _summarize_auto_fix(auto_fix_diff)
                    return GateResult(
                        passed=True,
                        gate="gate1_auto_check",
                        details=f"Lint clean (auto-fixed: {summary})",
                    )
                return GateResult(passed=True, gate="gate1_auto_check", details="Lint clean")

            # Failed — check if errors are only in files this task didn't modify.
            combined_output = (lint_result.stdout or "") + (lint_result.stderr or "")
            if changed_files:
                changed_basenames = {os.path.basename(f) for f in changed_files}
                changed_set = set(changed_files)
                relevant_lines = []
                for line in combined_output.splitlines():
                    is_relevant = any(f in line for f in changed_set) or any(
                        b in line for b in changed_basenames
                    )
                    if is_relevant:
                        relevant_lines.append(line)
                if not relevant_lines:
                    logger.info(
                        "Lint errors found but none in task's changed files — passing. "
                        "Pre-existing errors in: %s",
                        combined_output[:200],
                    )
                    return GateResult(
                        passed=True,
                        gate="gate1_auto_check",
                        details="Lint clean (pre-existing errors in unchanged files ignored)",
                    )
                combined_output = "\n".join(relevant_lines)

            output = (combined_output or "Unknown error")[:500]
            is_infra = any(pattern in combined_output for pattern in self._INFRA_ERROR_PATTERNS)
            return GateResult(
                passed=False,
                gate="gate1_auto_check",
                details=f"Lint errors:\n{output}",
                infra_error=is_infra,
            )
        finally:
            if gate_sem:
                gate_sem.release()

    # -- Adaptive lint timeout via learning system ----------------------------

    async def _get_learned_lint_timeout(self, linter_name: str, db=None) -> int | None:
        """Check if the learning system has a timeout override for this linter.

        Looks for lessons with category 'infra_timeout' whose trigger matches
        the linter name.  The lesson's resolution contains the recommended
        timeout in seconds (e.g. "timeout:360").
        """
        try:
            if db is None:
                return None
            trigger = f"lint_timeout:{linter_name}"
            lesson = await db.find_matching_lesson(
                trigger, project_dir=getattr(self, "_project_dir", None)
            )
            if lesson and lesson.resolution:
                # Parse "timeout:NNN" from resolution
                for part in lesson.resolution.split():
                    if part.startswith("timeout:"):
                        try:
                            val = int(part.split(":")[1])
                            await db.bump_lesson_hit(lesson.id)
                            return val
                        except (ValueError, IndexError):
                            pass
        except Exception:
            logger.debug("Failed to check learned lint timeout", exc_info=True)
        return None

    async def _learn_lint_timeout(self, linter_name: str, failed_timeout: int, db=None) -> None:
        """Record a lint timeout lesson so future runs use a longer timeout.

        Doubles the failed timeout value.  If a lesson already exists,
        bumps its hit count and updates the resolution with the new value.
        """
        new_timeout = failed_timeout * 2
        trigger = f"lint_timeout:{linter_name}"
        title = f"{linter_name} lint timed out at {failed_timeout}s"
        resolution = f"timeout:{new_timeout}"
        content = (
            f"The {linter_name} linter timed out after {failed_timeout}s. "
            f"Learned: use {new_timeout}s for future runs."
        )
        try:
            if db is None:
                return
            project_dir = getattr(self, "_project_dir", None)
            existing = await db.find_matching_lesson(trigger, project_dir=project_dir)
            if existing:
                await db.bump_lesson_hit(existing.id)
                # Update the resolution with the new (larger) timeout
                # Only if the new timeout is larger than what's already stored
                for part in existing.resolution.split():
                    if part.startswith("timeout:"):
                        try:
                            old_val = int(part.split(":")[1])
                            if new_timeout <= old_val:
                                return  # Already learned a sufficient timeout
                        except (ValueError, IndexError):
                            pass
                # Update the lesson with the new timeout
                async with db._session_factory() as session:
                    from forge.storage.db import LessonRow

                    row = await session.get(LessonRow, existing.id)
                    if row:
                        row.resolution = resolution
                        row.content = content
                        await session.commit()
                logger.info("Updated lint timeout lesson: %s → %ds", linter_name, new_timeout)
            else:
                await db.add_lesson(
                    scope="project",
                    category="infra_timeout",
                    title=title,
                    content=content,
                    trigger=trigger,
                    resolution=resolution,
                    project_dir=project_dir,
                )
                logger.info("Created lint timeout lesson: %s → %ds", linter_name, new_timeout)
        except Exception:
            logger.warning("Failed to record lint timeout lesson", exc_info=True)
