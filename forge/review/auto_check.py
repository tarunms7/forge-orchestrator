"""Gate 1: Programmatic auto-checks. Fast, deterministic, no LLM."""

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    """Outcome of Gate 1 checks."""

    passed: bool
    failures: list[str] = field(default_factory=list)


class AutoCheck:
    """Runs all programmatic checks and returns a unified result."""

    @staticmethod
    def run_all(
        test_passed: bool,
        lint_clean: bool,
        build_ok: bool,
        file_conflicts: list[str],
    ) -> CheckResult:
        failures: list[str] = []

        if not test_passed:
            failures.append("Tests failed")

        if not lint_clean:
            failures.append("Lint errors found")

        if not build_ok:
            failures.append("Build failed")

        if file_conflicts:
            failures.append(
                f"File conflicts with other agents: {', '.join(file_conflicts)}"
            )

        return CheckResult(passed=len(failures) == 0, failures=failures)
