"""Pipeline templates and quality presets for Forge.

Built-in templates define prompt modifiers, model strategies, and review
configurations for common pipeline archetypes (feature, bugfix, refactor,
test-coverage, docs).  Quality presets are shorthand for speed/quality
trade-offs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ReviewConfig:
    """Review pipeline overrides for a template."""

    skip_l2: bool = False
    extra_review_pass: bool = False
    custom_review_focus: str = ""


@dataclass
class PipelineTemplate:
    """A reusable pipeline configuration template.

    Built-in templates are code-defined constants.  User templates are
    stored in the DB with ``is_builtin=False`` and a ``user_id``.
    """

    id: str
    name: str
    description: str
    icon: str  # emoji
    model_strategy: Literal["auto", "fast", "quality"] = "auto"
    planner_prompt_modifier: str = ""
    agent_prompt_modifier: str = ""
    review_config: ReviewConfig = field(default_factory=ReviewConfig)
    build_cmd: str | None = None
    test_cmd: str | None = None
    max_tasks: int | None = None
    default_complexity: Literal["low", "medium", "high"] | None = None
    is_builtin: bool = True
    user_id: str | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Built-in template definitions
# ---------------------------------------------------------------------------

BUILTIN_TEMPLATES: dict[str, PipelineTemplate] = {
    "feature": PipelineTemplate(
        id="feature",
        name="Feature",
        description="Standard plan → execute → review pipeline for new features.",
        icon="🚀",
        model_strategy="auto",
        planner_prompt_modifier="",
        agent_prompt_modifier="",
        review_config=ReviewConfig(),
    ),
    "bugfix": PipelineTemplate(
        id="bugfix",
        name="Bug Fix",
        description="Reproduction-first debugging: failing test → fix → regression test.",
        icon="🐛",
        model_strategy="auto",
        planner_prompt_modifier=(
            "\n\nThis is a BUG FIX pipeline. Structure the plan as follows:\n"
            "1. First task: reproduce the bug by writing a FAILING test that demonstrates "
            "the broken behavior.\n"
            "2. Middle tasks: identify the root cause and implement the minimal fix.\n"
            "3. Final task: verify the failing test now passes and add any additional "
            "regression tests to prevent recurrence.\n"
            "Prioritize understanding the root cause over applying a surface-level patch."
        ),
        agent_prompt_modifier=(
            "\n\nBUG FIX mode: Before changing any production code, write a test that "
            "FAILS without your fix and PASSES with it. This proves you found the real "
            "root cause. Keep the fix minimal — do not refactor unrelated code."
        ),
        review_config=ReviewConfig(
            custom_review_focus=(
                "Verify: (1) a regression test exists that would have caught this bug, "
                "(2) the fix addresses the root cause rather than symptoms, "
                "(3) no unrelated changes are included."
            ),
        ),
    ),
    "refactor": PipelineTemplate(
        id="refactor",
        name="Refactor",
        description="Behavior-preserving code improvements with extra review scrutiny.",
        icon="♻️",
        model_strategy="quality",
        planner_prompt_modifier=(
            "\n\nThis is a REFACTOR pipeline. Plan small, incremental, "
            "behavior-preserving changes:\n"
            "- Each task should be a single refactoring step that keeps all existing "
            "tests passing.\n"
            "- Run the existing test suite after each change to confirm nothing breaks.\n"
            "- Prefer extracting functions/classes, renaming for clarity, and reducing "
            "duplication over rewriting from scratch.\n"
            "- Do NOT add new features or change external behavior."
        ),
        agent_prompt_modifier=(
            "\n\nREFACTOR mode: Your changes MUST NOT alter external behavior. "
            "After every change, run the existing tests to confirm they still pass. "
            "Make small, incremental improvements — one refactoring per commit. "
            "Do not add new features, change APIs, or modify test assertions."
        ),
        review_config=ReviewConfig(
            extra_review_pass=True,
            custom_review_focus=(
                "Strictly verify behavior preservation: (1) no public API signatures "
                "changed, (2) no test assertions modified, (3) all existing tests still "
                "pass, (4) changes are genuinely incremental and reversible."
            ),
        ),
    ),
    "test-coverage": PipelineTemplate(
        id="test-coverage",
        name="Test Coverage",
        description="Analyze untested code paths and generate comprehensive tests.",
        icon="🧪",
        model_strategy="auto",
        planner_prompt_modifier=(
            "\n\nThis is a TEST COVERAGE pipeline. Structure the plan as follows:\n"
            "1. Analyze the codebase for untested or under-tested code paths.\n"
            "2. Group test tasks by module or component — one task per module.\n"
            "3. Prioritize critical paths, error handling, and edge cases.\n"
            "4. Each task should list the source files being tested and the test "
            "file(s) to create or extend."
        ),
        agent_prompt_modifier=(
            "\n\nTEST WRITING mode: Follow these guidelines:\n"
            "- Use the Arrange-Act-Assert pattern for every test.\n"
            "- Cover happy paths, edge cases, error conditions, and boundary values.\n"
            "- Use mocks/stubs for external dependencies (I/O, network, DB).\n"
            "- Write descriptive test names that explain the scenario being tested.\n"
            "- Aim for meaningful coverage, not just line coverage — test behaviors, "
            "not implementation details."
        ),
        review_config=ReviewConfig(
            skip_l2=True,  # tests ARE the review
            custom_review_focus=(
                "Verify test quality: meaningful assertions, edge cases covered, "
                "proper use of mocks, no tests that just exercise code without "
                "asserting anything."
            ),
        ),
    ),
    "docs": PipelineTemplate(
        id="docs",
        name="Documentation",
        description="Generate or update project documentation.",
        icon="📝",
        model_strategy="auto",
        planner_prompt_modifier=(
            "\n\nThis is a DOCUMENTATION pipeline. Structure the plan as follows:\n"
            "1. Identify which docs need creating or updating (README, API docs, "
            "architecture guides, inline docstrings).\n"
            "2. One task per document or documentation section.\n"
            "3. Each task should reference the source code files that the docs describe.\n"
            "4. Prioritize accuracy over completeness — wrong docs are worse than "
            "missing docs."
        ),
        agent_prompt_modifier=(
            "\n\nDOCUMENTATION mode: Follow these guidelines:\n"
            "- Write for the target audience (developers, users, or both).\n"
            "- Include concrete code examples where appropriate.\n"
            "- Keep language clear, concise, and jargon-free.\n"
            "- Cross-reference related docs and source files.\n"
            "- Use consistent formatting (headings, code blocks, lists)."
        ),
        review_config=ReviewConfig(
            custom_review_focus=(
                "Verify documentation quality: (1) technical accuracy — do the docs "
                "match the actual code behavior? (2) clarity — is it understandable? "
                "(3) completeness — are important details missing? "
                "(4) no stale references to renamed or removed code."
            ),
        ),
        build_cmd="",   # skip build gate
        test_cmd="",    # skip test gate
    ),
}


# ---------------------------------------------------------------------------
# Quality presets
# ---------------------------------------------------------------------------

QUALITY_PRESETS: dict[str, dict] = {
    "fast": {
        "model_strategy": "fast",
        "review_config": {
            "skip_l2": False,
            "extra_review_pass": False,
            "custom_review_focus": "",
        },
        "require_approval": False,
    },
    "balanced": {
        "model_strategy": "auto",
        "review_config": {
            "skip_l2": False,
            "extra_review_pass": False,
            "custom_review_focus": "",
        },
        "require_approval": False,
    },
    "thorough": {
        "model_strategy": "quality",
        "review_config": {
            "skip_l2": False,
            "extra_review_pass": True,
            "custom_review_focus": "",
        },
        "require_approval": True,
    },
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_template(template_id: str) -> PipelineTemplate | None:
    """Look up a built-in template by ID.

    Returns ``None`` if no template matches.
    """
    return BUILTIN_TEMPLATES.get(template_id)


def get_quality_preset(preset_id: str) -> dict | None:
    """Look up a quality preset by ID.

    Returns ``None`` if no preset matches.
    """
    return QUALITY_PRESETS.get(preset_id)


def template_to_dict(template: PipelineTemplate) -> dict:
    """Serialize a PipelineTemplate to a plain dict for JSON storage."""
    data = asdict(template)
    # Convert datetime to ISO string for JSON compatibility
    if data.get("created_at") is not None:
        data["created_at"] = data["created_at"].isoformat()
    return data


def template_from_dict(data: dict) -> PipelineTemplate:
    """Deserialize a PipelineTemplate from a plain dict (e.g. JSON storage)."""
    data = dict(data)  # shallow copy to avoid mutating caller's dict

    # Reconstruct ReviewConfig from nested dict
    review_raw = data.pop("review_config", None)
    if isinstance(review_raw, dict):
        review_config = ReviewConfig(**review_raw)
    elif isinstance(review_raw, ReviewConfig):
        review_config = review_raw
    else:
        review_config = ReviewConfig()

    # Parse created_at from ISO string if present
    created_at = data.pop("created_at", None)
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)

    return PipelineTemplate(
        review_config=review_config,
        created_at=created_at,
        **data,
    )
