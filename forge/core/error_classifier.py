"""Classify agent and pipeline errors into actionable categories.

Helps the retry system and learning system understand WHY something failed,
not just THAT it failed. Categories:

- sdk_error: Claude SDK issue (auth, rate limit, network)
- agent_timeout: Agent hit max turns without completing
- agent_no_changes: Agent ran but produced no code changes
- agent_crash: Agent subprocess died unexpectedly
- build_failure: Build command failed
- test_failure: Test command failed
- lint_failure: Lint check failed
- review_rejection: LLM reviewer rejected the diff
- merge_conflict: Merge/rebase failed due to conflicts
- scope_violation: Agent modified files outside its scope
- dependency_failure: Task blocked by failed dependency
- budget_exceeded: Pipeline budget limit hit
- infra_error: Infrastructure issue (disk, permissions, git)
"""

from __future__ import annotations

from dataclasses import dataclass

# Pre-compiled pattern lists for classify_agent_error()
_SDK_AUTH_PATTERNS = ("authentication", "unauthorized", "403", "api key", "rate limit", "429")
_TIMEOUT_PATTERNS = ("timeout", "max turns", "timed out")
_GUARD_PATTERNS = ("guardtriggered", "retry loop")
_NETWORK_PATTERNS = ("connection", "network", "dns", "socket", "eof")
_NO_CHANGES_PATTERNS = ("no changes", "no diff")
_INFRA_TEST_PATTERNS = (
    "command not found",
    "no module named",
    "importerror",
    "modulenotfounderror",
    "filenotfounderror",
)


@dataclass
class ClassifiedError:
    """A classified error with category and structured details."""

    category: str
    message: str
    retriable: bool = True
    details: str = ""

    @property
    def short(self) -> str:
        """One-line summary for logging."""
        return f"[{self.category}] {self.message}"


def classify_agent_error(error: str | None, result=None) -> ClassifiedError:
    """Classify an agent execution error."""
    if not error:
        error = ""
    error_lower = error.lower()

    # SDK / auth errors
    if any(p in error_lower for p in _SDK_AUTH_PATTERNS):
        return ClassifiedError(
            category="sdk_error",
            message="Claude API authentication or rate limit error",
            retriable="rate limit" in error_lower or "429" in error_lower,
            details=error[:500],
        )

    # Timeout
    if any(p in error_lower for p in _TIMEOUT_PATTERNS):
        return ClassifiedError(
            category="agent_timeout",
            message="Agent exceeded time or turn limit",
            retriable=True,
            details=error[:500],
        )

    # Guard triggered (retry loop detection)
    if any(p in error_lower for p in _GUARD_PATTERNS):
        return ClassifiedError(
            category="agent_crash",
            message="Agent stuck in retry loop (RuntimeGuard triggered)",
            retriable=True,
            details=error[:500],
        )

    # Network errors
    if any(p in error_lower for p in _NETWORK_PATTERNS):
        return ClassifiedError(
            category="sdk_error",
            message="Network error communicating with Claude API",
            retriable=True,
            details=error[:500],
        )

    # No changes
    if any(p in error_lower for p in _NO_CHANGES_PATTERNS):
        return ClassifiedError(
            category="agent_no_changes",
            message="Agent ran but produced no code changes",
            retriable=True,
        )

    # Generic agent failure
    return ClassifiedError(
        category="agent_crash",
        message=error[:200] if error else "Unknown agent error",
        retriable=True,
        details=error[:500] if error else "",
    )


def classify_review_error(gate: str, details: str) -> ClassifiedError:
    """Classify a review gate failure."""
    details_lower = details.lower()

    if gate == "gate0_build":
        return ClassifiedError(
            category="build_failure",
            message=f"Build failed: {details[:150]}",
            retriable=True,
            details=details[:500],
        )

    if gate == "gate1_auto_check":
        if "timeout" in details_lower:
            return ClassifiedError(
                category="lint_failure",
                message="Lint command timed out",
                retriable=True,
                details=details[:500],
            )
        return ClassifiedError(
            category="lint_failure",
            message=f"Lint check failed: {details[:150]}",
            retriable=True,
            details=details[:500],
        )

    if gate == "gate1.5_test":
        # Check for infra errors vs real test failures
        is_infra = any(p in details_lower for p in _INFRA_TEST_PATTERNS)
        return ClassifiedError(
            category="infra_error" if is_infra else "test_failure",
            message=f"{'Infrastructure' if is_infra else 'Test'} failure: {details[:150]}",
            retriable=True,
            details=details[:500],
        )

    if gate == "gate2_llm_review":
        return ClassifiedError(
            category="review_rejection",
            message="LLM reviewer rejected the changes",
            retriable=True,
            details=details[:500],
        )

    return ClassifiedError(
        category="review_rejection",
        message=f"Review gate {gate} failed: {details[:150]}",
        retriable=True,
        details=details[:500],
    )


def classify_merge_error(error: str) -> ClassifiedError:
    """Classify a merge/rebase failure."""
    error_lower = error.lower()

    if "conflict" in error_lower:
        return ClassifiedError(
            category="merge_conflict",
            message="Merge conflict — files were modified by another task",
            retriable=True,
            details=error[:500],
        )

    if "not a fast-forward" in error_lower or "non-fast-forward" in error_lower:
        return ClassifiedError(
            category="merge_conflict",
            message="Branch has diverged — rebase needed",
            retriable=True,
            details=error[:500],
        )

    return ClassifiedError(
        category="merge_conflict",
        message=f"Merge failed: {error[:150]}",
        retriable=True,
        details=error[:500],
    )
