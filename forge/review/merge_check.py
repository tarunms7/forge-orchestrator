"""Gate 3: Merge readiness check. Programmatic — rebase + test post-rebase."""

import subprocess

from forge.review.pipeline import GateResult


async def gate3_merge_check(
    worktree_path: str,
    main_branch: str = "main",
    test_command: str | None = None,
) -> GateResult:
    """Check if a task branch is ready to merge: rebase clean + tests pass."""
    rebase_result = _try_rebase(worktree_path, main_branch)
    if not rebase_result.passed:
        return rebase_result

    if test_command:
        test_result = _run_tests(worktree_path, test_command)
        if not test_result.passed:
            return test_result

    return GateResult(passed=True, gate="gate3_merge_check", details="Rebase clean, tests pass")


def _try_rebase(worktree_path: str, main_branch: str) -> GateResult:
    """Attempt to rebase onto main. Abort on conflict."""
    result = subprocess.run(
        ["git", "fetch", "origin", main_branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        ["git", "rebase", f"origin/{main_branch}"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=worktree_path,
            capture_output=True,
        )
        conflicts = _find_conflicts(worktree_path)
        return GateResult(
            passed=False,
            gate="gate3_merge_check",
            details=f"Rebase conflict: {', '.join(conflicts) if conflicts else result.stderr}",
        )

    return GateResult(passed=True, gate="gate3_merge_check", details="Rebase clean")


def _run_tests(worktree_path: str, test_command: str) -> GateResult:
    """Run the test suite in the worktree."""
    result = subprocess.run(
        test_command.split(),
        cwd=worktree_path,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        output = result.stdout[-500:] if result.stdout else result.stderr[-500:]
        return GateResult(
            passed=False,
            gate="gate3_merge_check",
            details=f"Tests failed post-rebase:\n{output}",
        )

    return GateResult(passed=True, gate="gate3_merge_check", details="Tests pass post-rebase")


def _find_conflicts(worktree_path: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    return [f for f in result.stdout.strip().split("\n") if f]
