"""Reviewer-stage conformance tests.

Each test verifies a behavioral contract that any provider must satisfy
when running in the *reviewer* (intelligence) execution stage.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from forge.providers.base import (
    ExecutionMode,
    ModelSpec,
    OutputContract,
    ProviderEvent,
    ToolPolicy,
    WorkspaceRoots,
)
from forge.tests.conformance import ConformanceResult, ConformanceTest

if TYPE_CHECKING:
    from forge.providers.registry import ProviderRegistry


def _reviewer_tool_policy() -> ToolPolicy:
    """Reviewer: read-only, no shell or write."""
    return ToolPolicy(
        mode="allowlist",
        allowed_tools=["read", "glob", "grep"],
    )


def _reviewer_contract() -> OutputContract:
    return OutputContract(
        format="json",
        json_schema={
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["approve", "request_changes", "comment"],
                },
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string"},
                            "file": {"type": "string"},
                            "line": {"type": "integer"},
                            "message": {"type": "string"},
                        },
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["verdict"],
        },
    )


_CLEAN_DIFF = """\
diff --git a/api/handler.py b/api/handler.py
index 1234567..abcdefg 100644
--- a/api/handler.py
+++ b/api/handler.py
@@ -10,6 +10,8 @@ class Handler:
     def handle(self, request):
+        # Log the incoming request
+        logger.info("Handling request: %s", request.path)
         return self.process(request)
"""

_BUGGY_DIFF = """\
diff --git a/api/handler.py b/api/handler.py
index 1234567..abcdefg 100644
--- a/api/handler.py
+++ b/api/handler.py
@@ -10,8 +10,10 @@ class Handler:
     def handle(self, request):
-        if request.user is not None:
-            return self.process(request)
+        # Process all requests regardless of auth
+        return self.process(request)
+
+    def get_user_data(self, user_id):
+        data = db.query(f"SELECT * FROM users WHERE id = {user_id}")
+        return data
"""


class TestProducesValidVerdict(ConformanceTest):
    """Diff review produces valid JSON verdict."""

    stage = "reviewer"

    async def run(self, registry: ProviderRegistry) -> ConformanceResult:
        start = self._timer()
        spec = ModelSpec.parse(self.model) if self.model else ModelSpec.parse("sonnet")
        provider = registry.get_for_model(spec)
        entry = registry.get_catalog_entry(spec)

        events: list[ProviderEvent] = []

        handle = provider.start(
            prompt=f"Review this diff and produce a JSON verdict:\n\n{_CLEAN_DIFF}",
            system_prompt=(
                "You are a code reviewer. Analyze the diff and return a JSON object "
                "with keys: verdict (approve/request_changes/comment), issues (list), "
                "and summary (string)."
            ),
            catalog_entry=entry,
            execution_mode=ExecutionMode.INTELLIGENCE,
            tool_policy=_reviewer_tool_policy(),
            output_contract=_reviewer_contract(),
            workspace=WorkspaceRoots(primary_cwd="."),
            max_turns=3,
            on_event=lambda e: events.append(e),
        )
        result = await handle.result()

        if result.is_error:
            return self._fail(start, f"Reviewer execution error: {result.text[:200]}")

        text = result.text.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start_idx = text.find("{")
            end_idx = text.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                try:
                    parsed = json.loads(text[start_idx:end_idx])
                except json.JSONDecodeError:
                    return self._fail(start, "Output is not valid JSON")
            else:
                return self._fail(start, "No JSON object found in output")

        if "verdict" not in parsed:
            return self._fail(start, "JSON output missing 'verdict' key")
        valid_verdicts = {"approve", "request_changes", "comment"}
        if parsed["verdict"] not in valid_verdicts:
            return self._fail(
                start,
                f"Invalid verdict '{parsed['verdict']}' — expected one of {valid_verdicts}",
            )
        return self._pass(start, f"Valid verdict: {parsed['verdict']}")


class TestIdentifiesObviousBug(ConformanceTest):
    """Diff with SQL injection and auth bypass — verify reviewer catches it."""

    stage = "reviewer"

    async def run(self, registry: ProviderRegistry) -> ConformanceResult:
        start = self._timer()
        spec = ModelSpec.parse(self.model) if self.model else ModelSpec.parse("sonnet")
        provider = registry.get_for_model(spec)
        entry = registry.get_catalog_entry(spec)

        events: list[ProviderEvent] = []

        handle = provider.start(
            prompt=f"Review this diff for bugs and security issues:\n\n{_BUGGY_DIFF}",
            system_prompt=(
                "You are a security-focused code reviewer. Analyze the diff for "
                "bugs, security vulnerabilities, and logic errors. Return a JSON "
                "object with verdict, issues list, and summary."
            ),
            catalog_entry=entry,
            execution_mode=ExecutionMode.INTELLIGENCE,
            tool_policy=_reviewer_tool_policy(),
            output_contract=_reviewer_contract(),
            workspace=WorkspaceRoots(primary_cwd="."),
            max_turns=3,
            on_event=lambda e: events.append(e),
        )
        result = await handle.result()

        if result.is_error:
            return self._fail(start, f"Reviewer execution error: {result.text[:200]}")

        text = result.text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start_idx = text.find("{")
            end_idx = text.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                try:
                    parsed = json.loads(text[start_idx:end_idx])
                except json.JSONDecodeError:
                    return self._fail(start, "Output is not valid JSON")
            else:
                return self._fail(start, "No JSON object found in output")

        # Should flag issues — either request_changes or a non-empty issues list
        verdict = parsed.get("verdict", "")
        issues = parsed.get("issues", [])
        summary = parsed.get("summary", "")
        full_text = f"{verdict} {summary} {json.dumps(issues)}".lower()

        # Check that the reviewer flagged at least one of the bugs:
        # 1. SQL injection via f-string
        # 2. Auth bypass (removed None check)
        found_sql = any(
            kw in full_text for kw in ("sql injection", "sql", "injection", "f-string", "format")
        )
        found_auth = any(
            kw in full_text for kw in ("auth", "authentication", "none check", "null", "bypass")
        )

        if not found_sql and not found_auth:
            return self._fail(
                start,
                f"Reviewer did not identify SQL injection or auth bypass. Verdict: {verdict}",
            )

        bugs_found = []
        if found_sql:
            bugs_found.append("SQL injection")
        if found_auth:
            bugs_found.append("auth bypass")

        return self._pass(
            start,
            f"Bugs identified: {', '.join(bugs_found)}. Verdict: {verdict}",
        )


# ---------------------------------------------------------------------------
# Registry of all reviewer conformance tests
# ---------------------------------------------------------------------------

REVIEWER_CONFORMANCE_TESTS: list[type[ConformanceTest]] = [
    TestProducesValidVerdict,
    TestIdentifiesObviousBug,
]
