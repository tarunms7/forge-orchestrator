"""Pydantic models for the multi-pass planning pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class KeyModule(BaseModel):
    """A module relevant to the planning task."""

    path: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    key_interfaces: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    loc: int = 0


class RelevantInterface(BaseModel):
    """An existing interface the spec touches."""

    name: str = Field(min_length=1)
    file: str = Field(min_length=1)
    signature: str = ""
    notes: str = ""


class CodebaseMap(BaseModel):
    """Deep LLM-generated understanding of the codebase.

    Produced by the Scout stage. Consumed by Architect, Detailers,
    and task agents as shared context.
    """

    architecture_summary: str = Field(min_length=1)
    key_modules: list[KeyModule] = Field(default_factory=list)
    existing_patterns: dict[str, str] = Field(default_factory=dict)
    relevant_interfaces: list[RelevantInterface] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @staticmethod
    def _normalize_path(p: str) -> str:
        """Normalize a file path for comparison (strip ./, trailing /, collapse //)."""
        import posixpath

        p = p.replace("\\", "/")
        p = posixpath.normpath(p)
        if p.startswith("./"):
            p = p[2:]
        return p

    def slice_for_files(self, file_paths: list[str]) -> CodebaseMap:
        """Return a CodebaseMap containing only modules relevant to given files.

        Paths are normalized before comparison so that ``./src/foo.py`` and
        ``src/foo.py`` match correctly.
        """
        normalized_paths = {self._normalize_path(p) for p in file_paths}
        relevant = [m for m in self.key_modules if self._normalize_path(m.path) in normalized_paths]
        return CodebaseMap(
            architecture_summary=self.architecture_summary,
            key_modules=relevant,
            existing_patterns=self.existing_patterns,
            relevant_interfaces=[
                i
                for i in self.relevant_interfaces
                if self._normalize_path(i.file) in normalized_paths
            ],
            risks=[],
        )


class ValidationIssue(BaseModel):
    """An issue found by the validator."""

    severity: str = Field(pattern=r"^(minor|major|fatal)$")
    category: str = Field(min_length=1)
    affected_tasks: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)
    suggested_fix: str = ""


class MinorFix(BaseModel):
    """An auto-applied fix for a minor issue."""

    task_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    original_value: str | list[str]
    fixed_value: str | list[str]


class ValidationResult(BaseModel):
    """Output of the Validator stage."""

    status: str = Field(pattern=r"^(pass|fail)$")
    issues: list[ValidationIssue] = Field(default_factory=list)
    minor_fixes: list[MinorFix] = Field(default_factory=list)


class PlanFeedback(BaseModel):
    """Feedback from Validator to Architect for re-planning."""

    iteration: int = Field(ge=1, le=3)
    max_iterations: int = Field(default=3, ge=1, le=3)
    issues: list[ValidationIssue] = Field(default_factory=list)
    preserved_tasks: list[str] = Field(default_factory=list)
    replan_scope: str = Field(min_length=1)


class CodebaseMapMeta(BaseModel):
    """Metadata for incremental scouting cache."""

    created_at: str = Field(min_length=1)
    git_commit: str = Field(min_length=1)
    git_branch: str = Field(min_length=1)
    scout_model: str = Field(default="sonnet")
    file_hashes: dict[str, str] = Field(default_factory=dict)
