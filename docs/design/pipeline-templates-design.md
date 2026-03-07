# Pipeline Templates & Quality Presets — Design Document

**Date:** 2026-03-07
**Feature:** Pipeline templates with quality presets and saved configurations
**Status:** Draft

---

## 1. Template Data Structure

### TypeScript (Frontend)

```typescript
interface ReviewConfig {
  skip_l2: boolean;              // Skip LLM review gate
  extra_review_pass: boolean;    // Run L2 review twice (second pass checks first pass)
  custom_review_focus: string;   // Appended to REVIEW_SYSTEM_PROMPT
}

interface PipelineTemplate {
  id: string;                                       // "feature", "bugfix", etc. (built-in) or UUID (user)
  name: string;
  description: string;
  icon: string;                                     // emoji
  model_strategy: "auto" | "fast" | "quality";
  planner_prompt_modifier: string;                  // appended to PLANNER_SYSTEM_PROMPT
  agent_prompt_modifier: string;                    // appended to agent system prompt
  review_config: ReviewConfig;
  build_cmd?: string;                               // override default build command
  test_cmd?: string;                                // override default test command
  max_tasks?: number;                               // override default task limit
  default_complexity?: "low" | "medium" | "high";
  is_builtin: boolean;                              // false for user-created templates
  user_id?: string;                                 // only for user templates
  created_at?: string;                              // ISO timestamp, user templates only
}

// Quality presets (map to template + overrides)
type QualityPreset = "fast" | "balanced" | "thorough";

interface QualityPresetConfig {
  model_strategy: "auto" | "fast" | "quality";
  review_config: Partial<ReviewConfig>;
  require_approval: boolean;
}
```

### Python (Backend)

```python
# forge/core/templates.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ReviewConfig:
    """Controls how the review pipeline behaves for a template."""
    skip_l2: bool = False
    extra_review_pass: bool = False
    custom_review_focus: str = ""


@dataclass
class PipelineTemplate:
    """A pipeline template that configures planner, agent, and review behavior."""
    id: str
    name: str
    description: str
    icon: str
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
    created_at: str | None = None
```

---

## 2. Built-in Template Definitions

### Template 1: Feature (default)

```python
PipelineTemplate(
    id="feature",
    name="Feature",
    description="Standard plan → execute → review pipeline for new features.",
    icon="🚀",
    model_strategy="auto",
    planner_prompt_modifier="",  # No modification — use default planner behavior
    agent_prompt_modifier="",    # No modification — use default agent behavior
    review_config=ReviewConfig(
        skip_l2=False,
        extra_review_pass=False,
        custom_review_focus="",
    ),
)
```

### Template 2: Bug Fix

```python
PipelineTemplate(
    id="bugfix",
    name="Bug Fix",
    description="Reproduction-first debugging: reproduce, fix, then add regression test.",
    icon="🐛",
    model_strategy="auto",
    planner_prompt_modifier=(
        "\n\n## Bug Fix Template Instructions\n"
        "You are decomposing a BUG FIX task. Structure your task graph to follow "
        "this debugging methodology:\n"
        "1. **Reproduction first**: The first task should focus on understanding and "
        "reproducing the bug. Include writing a failing test that demonstrates the bug.\n"
        "2. **Targeted fix**: Subsequent tasks should apply the minimal fix needed. "
        "Avoid refactoring unrelated code.\n"
        "3. **Regression test**: Every fix task MUST include a test that:\n"
        "   - FAILS without the fix (demonstrates the bug existed)\n"
        "   - PASSES with the fix (proves it's resolved)\n"
        "4. Keep the task count small (2-3 tasks). Bug fixes should be surgical.\n"
        "5. Set complexity to 'low' or 'medium' — bug fixes should be focused."
    ),
    agent_prompt_modifier=(
        "\n\n## Bug Fix Guidelines\n"
        "You are fixing a bug. Follow this approach:\n"
        "1. First, understand the root cause by reading the relevant code\n"
        "2. Write a test that FAILS without your fix (regression test)\n"
        "3. Apply the minimal fix needed — do NOT refactor unrelated code\n"
        "4. Verify your test PASSES with the fix\n"
        "5. Check for similar bugs in related code paths"
    ),
    review_config=ReviewConfig(
        skip_l2=False,
        extra_review_pass=False,
        custom_review_focus=(
            "\nAdditional review focus for BUG FIX:\n"
            "- Does the fix include a regression test that would fail without the fix?\n"
            "- Is the fix minimal and targeted, or does it include unnecessary refactoring?\n"
            "- Could the fix introduce new bugs in related code paths?\n"
            "- Does the fix address the root cause, not just a symptom?"
        ),
    ),
)
```

### Template 3: Refactor

```python
PipelineTemplate(
    id="refactor",
    name="Refactor",
    description="Behavior-preserving refactoring with incremental changes and extra review.",
    icon="♻️",
    model_strategy="quality",
    planner_prompt_modifier=(
        "\n\n## Refactor Template Instructions\n"
        "You are decomposing a REFACTORING task. Key principles:\n"
        "1. **Behavior preservation is paramount**: No task should change external "
        "behavior. Every change must be verifiable by existing tests.\n"
        "2. **Small incremental steps**: Break the refactor into small, independently "
        "verifiable tasks. Each task should be a single refactoring step "
        "(extract method, rename, move, simplify, etc.).\n"
        "3. **Run existing tests**: Every task description should include "
        "'Run existing tests to verify behavior is preserved.'\n"
        "4. **Dependency chain**: Order tasks so each builds on the previous. "
        "Early tasks should be safe renames/extractions, later tasks can do "
        "structural changes.\n"
        "5. Set complexity to 'medium' — refactors need careful thought."
    ),
    agent_prompt_modifier=(
        "\n\n## Refactoring Guidelines\n"
        "You are performing a REFACTORING task. Critical rules:\n"
        "1. Do NOT change external behavior — this is a refactor, not a feature\n"
        "2. Make small, incremental changes\n"
        "3. Run existing tests after your changes to verify nothing broke\n"
        "4. If tests fail, your refactor broke something — fix it before committing\n"
        "5. Prefer well-known refactoring patterns: extract method, rename, "
        "move, inline, simplify conditional"
    ),
    review_config=ReviewConfig(
        skip_l2=False,
        extra_review_pass=True,  # Extra scrutiny for behavior preservation
        custom_review_focus=(
            "\nAdditional review focus for REFACTORING:\n"
            "- Does this change preserve existing behavior? Look for subtle semantic changes.\n"
            "- Are all existing tests still passing (check the test gate results)?\n"
            "- Is each change a clean, well-known refactoring pattern?\n"
            "- Could any change affect public API contracts or return types?\n"
            "- Second pass: re-check for any behavioral changes you might have missed."
        ),
    ),
)
```

### Template 4: Test Coverage

```python
PipelineTemplate(
    id="test-coverage",
    name="Test Coverage",
    description="Analyze untested code paths and generate comprehensive test suites.",
    icon="🧪",
    model_strategy="auto",
    planner_prompt_modifier=(
        "\n\n## Test Coverage Template Instructions\n"
        "You are decomposing a TEST COVERAGE task. Your job is to write tests, "
        "not implementation code.\n"
        "1. **Analyze coverage gaps**: Identify functions, branches, and edge cases "
        "that lack test coverage.\n"
        "2. **Group tests logically**: Each task should cover one module or one "
        "logical group of related functions.\n"
        "3. **Test strategy**: Include unit tests, edge cases, error paths, and "
        "integration tests where appropriate.\n"
        "4. **Test file naming**: Follow the project's existing test naming convention "
        "(e.g., `module_test.py` or `test_module.py`).\n"
        "5. **No implementation changes**: Tests should test the code AS-IS. "
        "Do not modify the source code being tested.\n"
        "6. Each task should create or extend test files — not modify source files."
    ),
    agent_prompt_modifier=(
        "\n\n## Test Writing Guidelines\n"
        "You are writing TESTS, not implementation code.\n"
        "1. Write thorough tests covering: happy path, edge cases, error handling, "
        "boundary values\n"
        "2. Use mocks/stubs for external dependencies (DB, API calls, file I/O)\n"
        "3. Follow the project's existing test patterns and naming conventions\n"
        "4. Each test should be independent and self-contained\n"
        "5. Do NOT modify the source code being tested — only create/modify test files\n"
        "6. Include docstrings explaining what each test verifies"
    ),
    review_config=ReviewConfig(
        skip_l2=True,   # Tests ARE the review — skip LLM review
        extra_review_pass=False,
        custom_review_focus="",  # Not used since L2 is skipped
    ),
)
```

### Template 5: Docs

```python
PipelineTemplate(
    id="docs",
    name="Docs",
    description="Generate or update documentation. Lighter review without build gates.",
    icon="📝",
    model_strategy="auto",
    planner_prompt_modifier=(
        "\n\n## Documentation Template Instructions\n"
        "You are decomposing a DOCUMENTATION task.\n"
        "1. **Identify what needs documenting**: API references, README updates, "
        "inline docstrings, architecture docs, usage guides.\n"
        "2. **Read existing docs**: Check for existing documentation to update "
        "rather than creating duplicates.\n"
        "3. **Follow project conventions**: Match the existing documentation style, "
        "format (markdown, RST, etc.), and structure.\n"
        "4. **Accuracy over completeness**: It's better to document 5 things "
        "accurately than 20 things superficially.\n"
        "5. Documentation files are typically: *.md, *.rst, docstrings in *.py, "
        "comments in config files."
    ),
    agent_prompt_modifier=(
        "\n\n## Documentation Guidelines\n"
        "You are writing DOCUMENTATION.\n"
        "1. Write clear, concise documentation with practical examples\n"
        "2. Use the project's existing documentation style and format\n"
        "3. Include code examples where appropriate\n"
        "4. Update existing docs rather than creating duplicates\n"
        "5. For API docs: include parameters, return types, exceptions, and examples\n"
        "6. For README/guides: focus on getting-started and common use cases"
    ),
    review_config=ReviewConfig(
        skip_l2=False,
        extra_review_pass=False,
        custom_review_focus=(
            "\nAdditional review focus for DOCUMENTATION:\n"
            "- Is the documentation accurate and up-to-date with the current code?\n"
            "- Are code examples correct and runnable?\n"
            "- Is the writing clear and free of jargon?\n"
            "- Do NOT check for build/test failures — documentation changes don't need to compile."
        ),
    ),
    build_cmd="",  # Explicitly skip build gate for docs
    test_cmd="",   # Explicitly skip test gate for docs
)
```

---

## 3. Quality Presets

Quality presets are a UX layer that maps to `model_strategy` + `review_config` + `require_approval`:

```python
QUALITY_PRESETS: dict[str, dict] = {
    "fast": {
        "model_strategy": "fast",
        "review_config": ReviewConfig(
            skip_l2=False,           # Still review, but with haiku
            extra_review_pass=False,
            custom_review_focus="",
        ),
        "require_approval": False,
    },
    "balanced": {
        "model_strategy": "auto",
        "review_config": ReviewConfig(
            skip_l2=False,
            extra_review_pass=False,
            custom_review_focus="",
        ),
        "require_approval": False,
    },
    "thorough": {
        "model_strategy": "quality",
        "review_config": ReviewConfig(
            skip_l2=False,
            extra_review_pass=True,
            custom_review_focus=(
                "\nThis pipeline uses THOROUGH quality mode. Be extra rigorous:\n"
                "- Check edge cases and error handling thoroughly\n"
                "- Verify test coverage is adequate\n"
                "- Look for performance implications\n"
                "- Check for security concerns"
            ),
        ),
        "require_approval": True,
    },
}
```

---

## 4. DB Schema for Custom Templates

### New Table: `user_templates`

```python
# forge/storage/db.py — add to existing models

class UserTemplateRow(Base):
    """User-created pipeline template stored as JSON config."""

    __tablename__ = "user_templates"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
```

`config_json` stores the full `PipelineTemplate` as a JSON blob (minus `id`, `user_id`, `created_at`, `is_builtin`):

```json
{
  "name": "My Custom Template",
  "description": "Optimized for our React codebase",
  "icon": "⚛️",
  "model_strategy": "auto",
  "planner_prompt_modifier": "Focus on React component best practices...",
  "agent_prompt_modifier": "Use React hooks, avoid class components...",
  "review_config": {
    "skip_l2": false,
    "extra_review_pass": false,
    "custom_review_focus": "Check for React anti-patterns..."
  },
  "build_cmd": "npm run build",
  "test_cmd": "npm test",
  "max_tasks": 4,
  "default_complexity": "medium"
}
```

### Database Methods

```python
# Added to Database class in forge/storage/db.py

async def create_user_template(
    self, *, user_id: str, name: str, config_json: str,
) -> UserTemplateRow:
    ...

async def list_user_templates(self, user_id: str) -> list[UserTemplateRow]:
    ...

async def get_user_template(self, template_id: str) -> UserTemplateRow | None:
    ...

async def update_user_template(
    self, template_id: str, *, name: str | None = None, config_json: str | None = None,
) -> UserTemplateRow | None:
    ...

async def delete_user_template(self, template_id: str) -> bool:
    ...
```

---

## 5. API Endpoints

### Upgrade existing `/api/templates` routes

The existing `forge/api/routes/templates.py` stores simple `{name, description, category}` templates as JSON files. This will be **replaced** with a richer system that serves both built-in and user templates from the DB.

#### `GET /api/templates`

Returns all templates (built-in + user-owned). No auth required for built-in; user templates require auth.

```
Response: {
  "builtin": [PipelineTemplate, ...],    // 5 built-in templates
  "user": [PipelineTemplate, ...]        // user's saved templates (empty if not logged in)
}
```

#### `GET /api/templates/{template_id}`

Get a single template by ID. Works for both built-in and user-owned.

```
Response: PipelineTemplate
```

#### `POST /api/templates`   (auth required)

Create a new user template.

```
Request: {
  "name": "My Template",
  "description": "...",
  "icon": "⚛️",
  "model_strategy": "auto",
  "planner_prompt_modifier": "...",
  "agent_prompt_modifier": "...",
  "review_config": { "skip_l2": false, "extra_review_pass": false, "custom_review_focus": "" },
  "build_cmd": "npm run build",
  "test_cmd": "npm test",
  "max_tasks": null,
  "default_complexity": null
}

Response: PipelineTemplate (with generated id, user_id, created_at)
```

#### `PUT /api/templates/{template_id}`   (auth required)

Update a user-owned template. Cannot update built-in templates.

```
Request: Partial<PipelineTemplate>  (only fields to update)
Response: PipelineTemplate
```

#### `DELETE /api/templates/{template_id}`   (auth required)

Delete a user-owned template. Cannot delete built-in templates.

```
Response: 204 No Content
```

### Updated `POST /api/tasks` (CreateTaskRequest)

Add `template_id` and `quality_preset` fields:

```python
class CreateTaskRequest(BaseModel):
    description: str
    project_path: str
    extra_dirs: list[str] = Field(default_factory=list)
    model_strategy: str = "auto"
    images: list[str] = Field(default_factory=list)
    branch_name: str | None = None
    build_cmd: str | None = None
    test_cmd: str | None = None
    budget_limit_usd: float = 0.0
    require_approval: bool | None = None
    # NEW FIELDS:
    template_id: str | None = Field(
        default=None,
        description="Pipeline template ID (built-in or user-created). "
                    "Template settings are merged with explicit form values."
    )
    quality_preset: str | None = Field(
        default=None,
        description="Quality preset: 'fast', 'balanced', or 'thorough'. "
                    "Overrides model_strategy and review settings."
    )
```

### Template Merge Logic (in `create_task` endpoint)

```python
# Precedence: explicit form values > quality_preset > template > defaults
async def _resolve_pipeline_config(body: CreateTaskRequest, forge_db) -> dict:
    config = {
        "model_strategy": "auto",
        "planner_prompt_modifier": "",
        "agent_prompt_modifier": "",
        "review_config": ReviewConfig(),
        "build_cmd": None,
        "test_cmd": None,
        "require_approval": False,
    }

    # 1. Apply template if provided
    if body.template_id:
        template = get_template(body.template_id)  # built-in or from DB
        if template:
            config["model_strategy"] = template.model_strategy
            config["planner_prompt_modifier"] = template.planner_prompt_modifier
            config["agent_prompt_modifier"] = template.agent_prompt_modifier
            config["review_config"] = template.review_config
            if template.build_cmd is not None:
                config["build_cmd"] = template.build_cmd
            if template.test_cmd is not None:
                config["test_cmd"] = template.test_cmd

    # 2. Apply quality preset (overrides template's model_strategy + review)
    if body.quality_preset and body.quality_preset in QUALITY_PRESETS:
        preset = QUALITY_PRESETS[body.quality_preset]
        config["model_strategy"] = preset["model_strategy"]
        config["review_config"] = preset["review_config"]
        config["require_approval"] = preset["require_approval"]

    # 3. Apply explicit form values (highest priority)
    if body.model_strategy != "auto":  # user explicitly changed it
        config["model_strategy"] = body.model_strategy
    if body.build_cmd is not None:
        config["build_cmd"] = body.build_cmd
    if body.test_cmd is not None:
        config["test_cmd"] = body.test_cmd
    if body.require_approval is not None:
        config["require_approval"] = body.require_approval

    return config
```

---

## 6. How Template Modifiers Are Injected Into Prompts

### Planner Prompt Injection

In `forge/core/claude_planner.py`, the `PLANNER_SYSTEM_PROMPT` is modified:

```python
class ClaudePlannerLLM(PlannerLLM):
    def __init__(self, model: str = "sonnet", cwd: str | None = None,
                 prompt_modifier: str = "") -> None:
        self._model = model
        self._cwd = cwd
        self._prompt_modifier = prompt_modifier

    async def generate_plan(self, ...) -> str:
        system_prompt = PLANNER_SYSTEM_PROMPT
        if self._prompt_modifier:
            system_prompt += self._prompt_modifier  # Append template instructions
        options = ClaudeCodeOptions(system_prompt=system_prompt, ...)
        ...
```

**Flow**: `create_task` → stores `planner_prompt_modifier` in pipeline row → `daemon.plan()` reads it → passes to `ClaudePlannerLLM`.

### Agent Prompt Injection

In `forge/core/daemon_helpers.py`, `_build_agent_prompt` gains an optional modifier:

```python
def _build_agent_prompt(title: str, description: str, files: list[str],
                         prompt_modifier: str = "") -> str:
    base = (
        f"Task: {title}\n\n"
        f"Description: {description}\n\n"
        f"Files you MUST ONLY modify: {', '.join(files)}\n\n"
        "Instructions:\n"
        "1. Implement this task completely\n"
        ...
    )
    if prompt_modifier:
        base += prompt_modifier
    return base
```

**Flow**: `create_task` → stores `agent_prompt_modifier` in pipeline row → `_execute_task` reads from pipeline → passes to `_build_agent_prompt`.

### Review Prompt Injection

In `forge/review/llm_review.py`, `gate2_llm_review` accepts an optional `custom_review_focus`:

```python
async def gate2_llm_review(
    ...,
    custom_review_focus: str = "",  # NEW param
) -> tuple[GateResult, ReviewCostInfo]:
    system_prompt = REVIEW_SYSTEM_PROMPT
    if custom_review_focus:
        system_prompt += custom_review_focus
    options = ClaudeCodeOptions(system_prompt=system_prompt, ...)
    ...
```

### Review Config Flow Through Daemon

In `forge/core/daemon_review.py`, `_run_review` respects `skip_l2` and `extra_review_pass`:

```python
async def _run_review(self, task, worktree_path, diff, *, db, pipeline_id, ...):
    review_config = self._get_review_config(pipeline_id)  # Load from pipeline

    # ... Gate 0 (build) — skip if review_config.build_cmd == ""
    # ... Gate 1 (lint) — always run
    # ... Gate 1.5 (test) — skip if review_config.test_cmd == ""

    # L2: LLM review — skip if review_config.skip_l2 is True
    if not review_config.skip_l2:
        gate2_result, cost = await gate2_llm_review(
            ...,
            custom_review_focus=review_config.custom_review_focus,
        )
        if not gate2_result.passed:
            return False, feedback

        # Extra review pass if configured
        if review_config.extra_review_pass:
            gate2_extra, cost2 = await gate2_llm_review(
                ...,
                custom_review_focus=(
                    review_config.custom_review_focus +
                    "\n\nThis is a SECOND REVIEW PASS. A previous reviewer already "
                    "approved this code. Your job is to catch anything they missed. "
                    "Focus on subtle bugs, edge cases, and security issues."
                ),
            )
            if not gate2_extra.passed:
                return False, feedback

    return True, None
```

### Pipeline Row Changes

Add two new columns to `PipelineRow`:

```python
class PipelineRow(Base):
    ...
    # NEW: Template configuration stored as JSON
    template_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    template_config_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
```

`template_config_json` stores the resolved template config (after merge with quality preset and form overrides), so the daemon can read it during execution without needing to re-resolve.

---

## 7. Frontend: Template Picker (ASCII Mockup)

This replaces the current `TemplatePicker.tsx` component. Shows in Step 2 of the task creation flow, above the task form.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Pipeline Template                                                  │
│  Choose a template to configure the pipeline behavior.              │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ 🚀           │  │ 🐛           │  │ ♻️            │              │
│  │ Feature      │  │ Bug Fix      │  │ Refactor     │              │
│  │              │  │              │  │              │              │
│  │ Standard     │  │ Reproduce →  │  │ Behavior-    │              │
│  │ plan →       │  │ fix →        │  │ preserving   │              │
│  │ execute →    │  │ regression   │  │ incremental  │              │
│  │ review       │  │ test         │  │ changes      │              │
│  │         [✓]  │  │              │  │              │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐   │
│  │ 🧪           │  │ 📝           │  │ + Save Current Config   │   │
│  │ Test         │  │ Docs         │  │   as Template           │   │
│  │ Coverage     │  │              │  │                         │   │
│  │ Analyze      │  │ Generate/    │  │  ┌───────────────────┐  │   │
│  │ gaps →       │  │ update docs  │  │  │ My Templates  ▼   │  │   │
│  │ generate     │  │ Lighter      │  │  ├───────────────────┤  │   │
│  │ tests        │  │ review       │  │  │ ⚛️ React Frontend │  │   │
│  │              │  │              │  │  │ 🔧 API Backend    │  │   │
│  └──────────────┘  └──────────────┘  │  └───────────────────┘  │   │
│                                       └─────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

**Behavior:**
- Templates are displayed as clickable cards in a 3-column grid
- Selected template has a highlighted border and checkmark
- Selecting a template pre-fills: `model_strategy`, `build_cmd`, `test_cmd`, and adds the template's prompt modifiers
- "Save Current Config as Template" button appears after user has modified any settings
- User templates are shown in a dropdown under the save button
- The description field is NOT pre-filled (unlike the current TemplatePicker which pre-fills description)

---

## 8. Frontend: Quality Preset Selector (ASCII Mockup)

Replaces the model_strategy dropdown in the task form. Shown between the template picker and the description field.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Quality                                                            │
│                                                                     │
│  ┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐ │
│  │    ⚡ Fast         │ │   ⚖️ Balanced     │ │   🔬 Thorough    │ │
│  │                   │ │  ─────────────    │ │                   │ │
│  │  Haiku agents     │ │  Auto strategy    │ │  Opus agents      │ │
│  │  Standard review  │ │  Standard review  │ │  Extra review     │ │
│  │  No approval      │ │  No approval      │ │  Approval req'd   │ │
│  │                   │ │                   │ │                   │ │
│  │  ~$0.10-0.50      │ │  ~$0.50-2.00      │ │  ~$2.00-8.00      │ │
│  │  per pipeline     │ │  per pipeline     │ │  per pipeline     │ │
│  └───────────────────┘ └───────────────────┘ └───────────────────┘ │
│                          ▲ selected                                 │
└─────────────────────────────────────────────────────────────────────┘
```

**Behavior:**
- Three visual cards (horizontal radio buttons with descriptions)
- "Balanced" is selected by default
- Selected card has accent border + glow
- Cost estimates are based on `cost_estimator.py` with the preset's model_strategy
- Selecting a preset updates `model_strategy`, `review_config`, and `require_approval` in the form state
- Users can still override individual settings in the advanced options below

---

## 9. Settings Page: Template Manager (ASCII Wireframe)

Added as a new settings group in `web/src/app/settings/page.tsx`:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Settings                                                           │
│  Configure your Forge pipeline preferences                          │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ 📋 Pipeline Templates                                         │  │
│  │                                                               │  │
│  │  Your saved templates                                         │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ ⚛️ React Frontend                                       │  │  │
│  │  │ Optimized for our React codebase with ESLint + Vitest   │  │  │
│  │  │ Strategy: auto │ Build: npm run build │ Test: npm test   │  │  │
│  │  │                                          [Edit] [Delete] │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │ 🔧 API Backend                                          │  │  │
│  │  │ Python FastAPI with pytest and ruff                      │  │  │
│  │  │ Strategy: quality │ Build: — │ Test: pytest              │  │  │
│  │  │                                          [Edit] [Delete] │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │                 + Create New Template                    │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ ⚙️ Pipeline Defaults                                         │  │
│  │ ...existing settings...                                       │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Edit Template Modal:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Edit Template: ⚛️ React Frontend                         [✕ Close] │
│  ─────────────────────────────────────────────────────────────────  │
│                                                                     │
│  Name           [⚛️ React Frontend                            ]     │
│  Icon           [⚛️                                          ]     │
│  Description    [Optimized for our React codebase             ]     │
│                                                                     │
│  Model Strategy [Auto ▼]                                            │
│  Build Command  [npm run build                                ]     │
│  Test Command   [npm test                                     ]     │
│                                                                     │
│  Planner Instructions (appended to planner prompt)                  │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ Focus on React component best practices. Use functional     │    │
│  │ components with hooks. Follow the existing component        │    │
│  │ structure in src/components/.                               │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  Agent Instructions (appended to each agent's prompt)               │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ Use React hooks, avoid class components. Use Tailwind for   │    │
│  │ styling. Follow the project's existing patterns.            │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  Review Focus (appended to reviewer prompt)                         │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ Check for React anti-patterns: unnecessary re-renders,      │    │
│  │ missing dependency arrays, prop drilling.                   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  ☐ Skip LLM review     ☐ Extra review pass                         │
│                                                                     │
│                                    [Cancel]  [Save Template]        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. Data Flow Summary

```
User selects template + quality preset in TaskForm
         │
         ▼
POST /api/tasks  { template_id: "bugfix", quality_preset: "thorough", ... }
         │
         ▼
_resolve_pipeline_config()
  1. Load template "bugfix" → get planner/agent/review modifiers
  2. Apply "thorough" preset → override model_strategy=quality, extra_review_pass=true
  3. Apply explicit form overrides (build_cmd, test_cmd, etc.)
         │
         ▼
Store resolved config in PipelineRow.template_config_json
         │
         ▼
daemon.plan()
  └─ ClaudePlannerLLM(prompt_modifier=template.planner_prompt_modifier)
     └─ system_prompt = PLANNER_SYSTEM_PROMPT + planner_prompt_modifier
         │
         ▼
daemon.execute() → _execute_task()
  └─ _build_agent_prompt(prompt_modifier=template.agent_prompt_modifier)
     └─ prompt = base_prompt + agent_prompt_modifier
         │
         ▼
_run_review()
  ├─ Gate 0 (build): skip if template.build_cmd == ""
  ├─ Gate 1 (lint): always run
  ├─ Gate 1.5 (test): skip if template.test_cmd == ""
  ├─ Gate 2 (L2 LLM): skip if review_config.skip_l2
  │   └─ system_prompt = REVIEW_SYSTEM_PROMPT + custom_review_focus
  └─ Gate 2b (extra pass): only if review_config.extra_review_pass
```

---

## 11. Migration Path

### Phase 1: Core template engine (backend)
1. Create `forge/core/templates.py` with `PipelineTemplate`, `ReviewConfig`, `BUILTIN_TEMPLATES`, `QUALITY_PRESETS`
2. Add `UserTemplateRow` to `forge/storage/db.py` + DB methods
3. Add `template_config_json` and `template_id` columns to `PipelineRow`

### Phase 2: API + prompt injection
4. Rewrite `forge/api/routes/templates.py` with full CRUD for user templates
5. Add `template_id` and `quality_preset` to `CreateTaskRequest`
6. Implement `_resolve_pipeline_config()` merge logic in `tasks.py`
7. Wire planner_prompt_modifier into `ClaudePlannerLLM`
8. Wire agent_prompt_modifier into `_build_agent_prompt`
9. Wire review_config into `_run_review` (skip_l2, extra_review_pass, custom_review_focus)

### Phase 3: Frontend
10. Rewrite `TemplatePicker.tsx` to show built-in + user templates as cards
11. Add `QualityPresetSelector.tsx` component
12. Update `TaskForm.tsx` to include quality preset + template state
13. Update `page.tsx` (new task) to pass template_id + quality_preset in API call
14. Add template manager section to Settings page

### Backward Compatibility
- `template_id` and `quality_preset` are optional — existing pipelines work unchanged
- Default behavior (no template, no preset) is identical to current behavior
- CLI `forge run` is unaffected initially; template support via `--template` flag can be added later
- The existing `TemplateService` (file-based) is fully replaced; no migration needed since it stores only simple name/description/category

---

## 12. Files Changed

### New Files
- `forge/core/templates.py` — PipelineTemplate, ReviewConfig, BUILTIN_TEMPLATES, QUALITY_PRESETS, get_template()
- `web/src/components/task/QualityPresetSelector.tsx` — Quality preset visual selector

### Modified Files (Backend)
- `forge/storage/db.py` — UserTemplateRow model + CRUD methods + PipelineRow new columns
- `forge/api/routes/templates.py` — Full rewrite: CRUD for user templates + serve built-ins
- `forge/api/models/schemas.py` — Add template_id, quality_preset to CreateTaskRequest
- `forge/api/routes/tasks.py` — Template resolution in create_task endpoint
- `forge/core/claude_planner.py` — Accept + apply planner_prompt_modifier
- `forge/core/daemon_helpers.py` — Accept + apply agent_prompt_modifier in _build_agent_prompt
- `forge/core/daemon.py` — Pass template config to planner + executor
- `forge/core/daemon_executor.py` — Read template config, pass agent_prompt_modifier
- `forge/core/daemon_review.py` — Read review_config: skip_l2, extra_review_pass, custom_review_focus
- `forge/review/llm_review.py` — Accept custom_review_focus param

### Modified Files (Frontend)
- `web/src/components/task/TemplatePicker.tsx` — Full rewrite: template cards + user templates
- `web/src/components/task/TaskForm.tsx` — Add quality preset state, update TaskFormData
- `web/src/app/tasks/new/page.tsx` — Pass template_id + quality_preset in API call
- `web/src/app/settings/page.tsx` — Add template manager section
- `web/src/lib/api.ts` — Add template CRUD API helpers (if not already generic)

### Test Files
- `forge/core/templates_test.py` — Test built-in templates, QUALITY_PRESETS, get_template()
- `forge/api/routes/templates_test.py` — Update existing tests for new CRUD endpoints
- `forge/api/services/template_service_test.py` — Update or remove (service being replaced)
