# Forge Feature Design Prompts

> **Workflow**: Copy each prompt into a separate Claude Code session.
> Review the output design doc. Once approved, give it to Forge as the implementation task.

---

## Batch 1: Foundation (Do First)

### Design 1A: Build & Test Verification Gates

```
You are designing a feature for Forge Orchestrator — a multi-agent pipeline tool at this repo.
Read the following files to understand the current architecture, then produce a design document.

FILES TO READ:
- forge/review/pipeline.py — GateResult and ReviewOutcome dataclasses
- forge/core/daemon_review.py — ReviewMixin with _run_review, _gate1 (lint check)
- forge/core/daemon_executor.py — _attempt_merge which calls _run_review
- forge/config/settings.py — ForgeSettings
- forge/core/daemon_helpers.py — _get_changed_files_vs_main
- forge/agents/adapter.py — agent system prompt and ClaudeAdapter
- web/src/stores/taskStore.ts — task:review_update event handler
- web/src/components/task/AgentCard.tsx — review gate dots rendering

FEATURE: Add configurable build and test verification gates to the review pipeline.

REQUIREMENTS:
1. Add Gate 0 (pre-review): runs BEFORE L1 lint. Executes a user-configured build command
   (e.g. "npm run build", "cargo build", "go build ./...") in the worktree.
   - If the build fails, the agent gets retried with the build error output as feedback.
   - Build command is configured via ForgeSettings (FORGE_BUILD_CMD env var) or per-pipeline
     via the web UI task form (new optional field "Build Command").
   - If no build command is configured, this gate auto-passes (skip silently).

2. Add Gate 1.5 (between lint and LLM review): runs the test command
   (e.g. "pytest", "npm test", "go test ./...") in the worktree.
   - Configured via FORGE_TEST_CMD env var or per-pipeline "Test Command" field.
   - On failure, agent retries with test output as feedback.
   - If no test command configured, auto-passes.
   - Capture test output (stdout+stderr, truncated to 5000 chars) in the GateResult.details
     so it's visible in the UI.

3. Gate 3 (merge readiness) currently auto-passes. Keep it that way for now but document
   how users could hook custom gates here via a plugin system (future).

4. Frontend changes:
   - TaskForm.tsx: add optional "Build Command" and "Test Command" text inputs.
   - CreateTaskRequest schema: add build_cmd and test_cmd optional string fields.
   - PipelineRow: add build_cmd and test_cmd columns.
   - AgentCard review dots: currently shows L1/L2. Should now show Build/Lint/Test/Review
     with appropriate labels on hover.

DESIGN OUTPUT FORMAT:
Produce a markdown document with:
- Data flow diagram (ASCII) showing the gate sequence
- Exact file changes with before/after code snippets
- DB schema changes (new columns)
- API schema changes (new request fields)
- Frontend component changes
- Settings/env var additions
- Error handling: what happens when build/test times out (use agent_timeout_seconds / 2 as gate timeout)
- Backward compatibility: everything auto-passes when commands aren't configured
```

### Design 1B: Token & Cost Tracking with Budget Limits

```
You are designing a feature for Forge Orchestrator — a multi-agent pipeline tool at this repo.
Read the following files to understand the current architecture, then produce a design document.

FILES TO READ:
- forge/core/sdk_helpers.py — sdk_query() function, how it calls claude-code-sdk
- forge/agents/adapter.py — ClaudeAdapter.run(), AgentResult dataclass
- forge/core/daemon_executor.py — _stream_agent, cost_update event emission
- forge/review/llm_review.py — gate2_llm_review, how reviewer calls sdk_query
- forge/core/claude_planner.py — ClaudePlannerLLM.generate_plan
- forge/storage/db.py — TaskRow (cost_usd column), PipelineRow schema
- forge/config/settings.py — ForgeSettings
- web/src/components/task/CompletionSummary.tsx — cost display
- web/src/components/task/AgentCard.tsx — cost display per task
- web/src/stores/taskStore.ts — task:cost_update handler

FEATURE: Comprehensive token tracking, cost breakdown by stage, and budget limits.

REQUIREMENTS:
1. Token tracking at SDK level:
   - claude-code-sdk's ResultMessage has total_cost_usd. Check if it also has
     input_tokens and output_tokens (read the SDK source or docs).
   - If not available from SDK, parse the streaming messages for token usage events.
   - Add input_tokens and output_tokens fields to AgentResult.
   - Track tokens separately for: planner, each agent, each reviewer.

2. Cost breakdown stored in DB:
   - Add to PipelineRow: planner_cost_usd (float), total_cost_usd (float).
   - Add to TaskRow: agent_cost_usd (float), review_cost_usd (float) — separate from
     current cost_usd which lumps everything together.
   - Emit new event "pipeline:cost_update" with cumulative pipeline cost so the
     frontend can show a running total.

3. Budget limits:
   - New setting: FORGE_BUDGET_LIMIT_USD (float, default 0 = unlimited).
   - New per-pipeline field in CreateTaskRequest: budget_limit_usd (optional float).
   - Before each SDK call (agent, reviewer, planner), check if cumulative pipeline
     cost exceeds the budget. If so, emit "pipeline:budget_exceeded" event, cancel
     remaining tasks, and set pipeline phase to "cancelled".
   - Show budget usage in the UI: a progress bar or fraction (e.g., "$2.34 / $5.00").

4. Cost estimation before execution:
   - After planning, estimate cost based on: number of tasks, complexity tiers,
     model routing strategy, and historical averages (if available).
   - Show estimate in the PlanPanel before the user clicks "Execute Plan".
   - This can be a rough heuristic (e.g., opus agent ~$0.50-2.00 per task,
     sonnet ~$0.10-0.50, reviewer ~$0.05-0.20). Store the heuristic rates in settings.

5. Frontend:
   - CompletionSummary: show cost breakdown (planner + agents + reviewers).
   - AgentCard: show token counts (input/output) alongside cost.
   - PlanPanel: show estimated cost before execution.
   - New running cost indicator in the pipeline header during execution.

DESIGN OUTPUT FORMAT:
Produce a markdown document with:
- SDK integration: exactly how to get token counts from claude-code-sdk
- DB schema changes with migration strategy
- New events and their payloads
- Budget enforcement flow (ASCII diagram)
- API changes
- Frontend component mockups (ASCII)
- Settings additions
- Cost estimation formula
```

---

## Batch 2: Control & Intelligence (Do Second)

### Design 2A: Plan Editing & Pre-Merge Approval

```
You are designing a feature for Forge Orchestrator — a multi-agent pipeline tool at this repo.
Read the following files to understand the current architecture, then produce a design document.

FILES TO READ:
- forge/core/planner.py — Planner class, how TaskGraph is produced
- forge/core/models.py — TaskDefinition, TaskGraph, Complexity enum
- forge/core/validator.py — validate_task_graph (cycle detection, file conflict)
- forge/core/daemon.py — execute() method, how TaskGraph is consumed
- forge/api/routes/tasks.py — POST /tasks/{id}/execute endpoint, ExecuteRequest
- web/src/app/tasks/view/page.tsx — PlanPanel, "Execute Plan" button
- web/src/stores/taskStore.ts — pipeline:plan_ready event, hydrateFromRest
- forge/core/daemon_executor.py — _attempt_merge, how merge happens after review

FEATURE: Interactive plan editing and optional pre-merge human approval.

REQUIREMENTS:
1. Plan editing in the UI (before execution):
   - After planner produces the TaskGraph and frontend shows PlanPanel, allow users to:
     a. Edit task title, description, target files, and complexity.
     b. Delete tasks (with dependency cascade — warn if other tasks depend on it).
     c. Add new tasks (with ID auto-generation, dependency selection).
     d. Reorder tasks / change dependencies (drag-and-drop or dropdown).
   - Validate the edited graph client-side (cycle detection, no orphan dependencies)
     before sending to backend.
   - POST /tasks/{id}/execute already accepts an optional `tasks` override in
     ExecuteRequest — use this. Send the edited TaskGraph to the backend.
   - Backend re-validates with validate_task_graph() before execution.

2. Pre-merge approval mode (during execution):
   - New setting: FORGE_REQUIRE_APPROVAL (bool, default False).
   - New per-pipeline field: require_approval (bool).
   - When enabled, after L2 review passes but BEFORE merge, the pipeline pauses
     that task in a new state: "awaiting_approval".
   - Frontend shows a diff preview (use git diff from the worktree) and
     approve/reject buttons.
   - New API endpoints:
     - GET /tasks/{pipeline_id}/tasks/{task_id}/diff — returns the git diff
     - POST /tasks/{pipeline_id}/tasks/{task_id}/approve — proceeds to merge
     - POST /tasks/{pipeline_id}/tasks/{task_id}/reject — triggers retry with
       optional rejection reason as feedback
   - WebSocket event: "task:awaiting_approval" with {task_id, diff_preview (first 200 lines)}
   - Other tasks that don't depend on the paused task continue executing.

3. Pause/Resume pipeline:
   - POST /tasks/{id}/pause — sets a flag. The execution loop checks this flag
     before dispatching new tasks. Already-running tasks complete normally.
   - POST /tasks/{id}/resume — clears the flag, execution loop resumes dispatching.
   - New pipeline phases: add "paused" to the phase enum.
   - Frontend: pause/resume button in the pipeline header.

DESIGN OUTPUT FORMAT:
Produce a markdown document with:
- Plan editing UI wireframe (ASCII mockup of the editable PlanPanel)
- Client-side validation logic for edited graphs
- API contract for plan submission (modified ExecuteRequest)
- Pre-merge approval state machine (ASCII diagram)
- New API endpoints with request/response schemas
- DB changes (new task state, pipeline flag)
- WebSocket events
- Frontend component changes
- How pause/resume interacts with the asyncio execution loop
```

### Design 2B: Agent Context Sharing & Project Conventions

```
You are designing a feature for Forge Orchestrator — a multi-agent pipeline tool at this repo.
Read the following files to understand the current architecture, then produce a design document.

FILES TO READ:
- forge/core/context.py — gather_project_snapshot(), ProjectSnapshot
- forge/agents/adapter.py — agent system prompt, how project_context is injected
- forge/core/daemon_helpers.py — _build_agent_prompt, _build_retry_prompt
- forge/core/claude_planner.py — PLANNER_SYSTEM_PROMPT, how planner explores codebase
- forge/core/daemon_executor.py — _run_agent, how project_context flows to agents
- forge/config/settings.py — ForgeSettings

FEATURE: Smarter context sharing between agents and persistent project conventions.

REQUIREMENTS:
1. Planner-extracted conventions:
   - After the planner runs and produces the TaskGraph, also extract a "conventions"
     section from the planner's output. The planner already explores the codebase —
     have it also output a structured conventions block:
     ```json
     {
       "conventions": {
         "styling": "CSS variables via globals.css, Tailwind v4",
         "state_management": "Zustand stores in /stores/",
         "component_patterns": "React functional components, 'use client' directive",
         "naming": "camelCase for TS, snake_case for Python",
         "testing": "pytest for Python, no frontend tests yet",
         "imports": "absolute imports with @/ prefix for Next.js"
       }
     }
     ```
   - Store conventions in PipelineRow (conventions_json column).
   - Inject conventions into every agent's system prompt so they follow project patterns
     instead of reinventing them.

2. .forge/conventions.md file (persistent across pipelines):
   - If a `.forge/conventions.md` file exists in the project root, load it and
     append its contents to the agent system prompt.
   - Users can manually write/edit this file to guide all future Forge runs.
   - After each successful pipeline, optionally update this file with any new
     conventions discovered by the planner (with a merge strategy — don't overwrite
     user edits, only append new discoveries).
   - New setting: FORGE_AUTO_UPDATE_CONVENTIONS (bool, default False).

3. Inter-agent context sharing (within a pipeline):
   - When Agent 1 completes and its task is merged, extract a brief "implementation
     summary" from its output (last few lines or the commit message).
   - Store this summary in the DB (new column on TaskRow: implementation_summary).
   - When Agent 2 starts (and depends on Agent 1), include Agent 1's implementation
     summary in Agent 2's prompt: "Task '{title}' was completed by another agent.
     Summary of what was done: {summary}. The files are available in your worktree."
   - This gives dependent agents KNOWLEDGE about what was done, not just the files.

4. Shared file registry:
   - Track which files each agent created/modified (already in files_changed).
   - Before an agent starts, inject a "Files modified by completed dependencies"
     section listing the exact files and which task modified them.
   - This helps agents know exactly where to import from or what interfaces exist.

DESIGN OUTPUT FORMAT:
Produce a markdown document with:
- Modified planner system prompt (showing the conventions extraction addition)
- conventions.md file format specification
- Agent system prompt modifications (showing injected context)
- DB schema changes
- Data flow diagram showing how conventions and summaries flow between agents
- Settings additions
- How implementation summaries are extracted (parsing strategy)
```

---

## Batch 3: Integration & Community (Do Third)

### Design 3A: GitHub-Native Integration

```
You are designing a feature for Forge Orchestrator — a multi-agent pipeline tool at this repo.
Read the following files to understand the current architecture, then produce a design document.

FILES TO READ:
- forge/api/routes/tasks.py — POST /tasks endpoint, _auto_create_pr function
- forge/api/app.py — FastAPI app setup, router registration
- forge/core/daemon.py — ForgeDaemon.run(), execute()
- forge/storage/db.py — PipelineRow schema
- forge/config/settings.py — ForgeSettings

FEATURE: GitHub webhook integration — trigger pipelines from GitHub issues.

REQUIREMENTS:
1. GitHub webhook endpoint:
   - POST /api/webhooks/github — receives GitHub webhook payloads.
   - Verifies webhook signature using FORGE_GITHUB_WEBHOOK_SECRET env var.
   - Triggers on: issue_comment events where comment body starts with "/forge".
   - Extracts the issue title + body + comment text as the task description.
   - Auto-creates a pipeline linked to the issue.

2. Issue-linked pipelines:
   - New fields on PipelineRow: github_issue_url (str), github_issue_number (int).
   - When auto-PR is created, include "Closes #N" in the PR body to auto-close
     the issue on merge.
   - Post progress updates as issue comments:
     a. "Planning started..." when planning begins.
     b. "Plan ready: N tasks" with task list when plan completes.
     c. "Pipeline complete. PR: #M" when done.
     d. "Pipeline failed: {error}" if it fails.

3. GitHub App authentication (optional, future):
   - For now, rely on `gh` CLI being authenticated (existing pattern).
   - Document how a GitHub App would work for production deployments.

4. Security:
   - Only trigger for authenticated webhook signatures.
   - Only trigger for repository collaborators (check comment author permissions
     via `gh api repos/{owner}/{repo}/collaborators/{user}` or webhook payload).
   - Rate limit: max 1 pipeline per issue per 5 minutes.

5. Frontend:
   - Show GitHub issue link on the pipeline view page (next to description).
   - History page: show issue icon + number for issue-triggered pipelines.

DESIGN OUTPUT FORMAT:
Produce a markdown document with:
- Webhook endpoint implementation (request validation, payload parsing)
- Sequence diagram: GitHub event -> webhook -> pipeline creation -> progress comments
- Security model (signature verification, permission checks)
- API additions
- DB schema changes
- Settings additions (webhook secret, allowed repos)
- Frontend changes (issue link display)
- Example webhook payload handling
```

### Design 3B: Pipeline Templates & Quality Presets

```
You are designing a feature for Forge Orchestrator — a multi-agent pipeline tool at this repo.
Read the following files to understand the current architecture, then produce a design document.

FILES TO READ:
- forge/config/settings.py — ForgeSettings, model strategies
- forge/core/model_router.py — select_model, strategy routing table
- forge/core/claude_planner.py — PLANNER_SYSTEM_PROMPT
- forge/core/daemon.py — execute(), how settings flow to agents
- forge/review/llm_review.py — REVIEW_SYSTEM_PROMPT
- web/src/components/task/TaskForm.tsx — TaskFormData, template picker
- web/src/app/tasks/new/page.tsx — task creation flow

FEATURE: Pipeline templates with quality presets and saved configurations.

REQUIREMENTS:
1. Built-in templates:
   - "Feature" (default): standard plan -> execute -> review. Uses auto model strategy.
   - "Bug Fix": planner prompt emphasizes reproduction first, then fix, then regression
     test. Adds instruction to write a test that fails without the fix.
   - "Refactor": planner prompt emphasizes maintaining behavior, running existing tests,
     smaller incremental changes. Uses quality model strategy. Extra review pass.
   - "Test Coverage": planner analyzes untested code paths, generates comprehensive tests.
     Planner prompt focused on test strategy. Skips L2 review (tests ARE the review).
   - "Docs": planner generates/updates documentation. Lighter review (no build gate needed).

2. Template structure:
   ```typescript
   interface PipelineTemplate {
     id: string;
     name: string;
     description: string;
     icon: string;                          // emoji
     model_strategy: "auto" | "fast" | "quality";
     planner_prompt_modifier: string;       // appended to PLANNER_SYSTEM_PROMPT
     agent_prompt_modifier: string;         // appended to agent system prompt
     review_config: {
       skip_l2: boolean;
       extra_review_pass: boolean;
       custom_review_focus: string;         // appended to REVIEW_SYSTEM_PROMPT
     };
     build_cmd?: string;
     test_cmd?: string;
     max_tasks?: number;                    // override default task limit
     default_complexity?: "low" | "medium" | "high";
   }
   ```

3. Custom user templates:
   - Users can save their current task configuration as a named template.
   - Stored in DB: new TemplateRow table (id, user_id, name, config_json, created_at).
   - API: CRUD endpoints for /api/templates.
   - Frontend: template manager in Settings page. Template picker in TaskForm
     shows built-in + user templates.

4. Quality presets on the task form:
   - Replace the current "model_strategy" dropdown with a visual preset selector:
     - "Fast" — haiku agents, minimal review, optimized for speed.
     - "Balanced" (default) — auto strategy, standard review.
     - "Thorough" — opus agents, extra review pass, require approval before merge.
   - Each preset maps to a combination of model_strategy + review_config + template modifiers.

5. Backend:
   - New API field in CreateTaskRequest: template_id (optional string).
   - When template_id is provided, load the template config and merge with
     any user overrides from the form.
   - Pass template modifiers to planner and agent system prompts.
   - Pass review_config to the review pipeline.

DESIGN OUTPUT FORMAT:
Produce a markdown document with:
- Template data structure (TypeScript + Python)
- Built-in template definitions (all 5 with exact prompt modifiers)
- DB schema for custom templates
- API endpoints (CRUD)
- Frontend mockup of template picker (ASCII)
- Frontend mockup of quality preset selector (ASCII)
- How template modifiers are injected into prompts
- Settings page template manager wireframe
```

---

## Execution Order

```
Phase 1: Give Design 1A and 1B to two separate Claude sessions (parallel)
         Review and approve both designs
         Give BOTH approved designs to Forge as ONE task (they touch different files)

Phase 2: Give Design 2A and 2B to two separate Claude sessions (parallel)
         Review and approve both designs
         Give BOTH to Forge as ONE task

Phase 3: Give Design 3A and 3B to two separate Claude sessions (parallel)
         Review and approve both designs
         Give BOTH to Forge as ONE task
```

## Tips for Design Sessions

- Tell the Claude session: "You are ONLY producing a design document, NOT writing code."
- Ask it to read the specific files listed before designing.
- If the design is too vague, ask: "Show me the exact function signatures and data types."
- If it misses edge cases, ask: "What happens when [X fails / is missing / times out]?"
- The design doc should be specific enough that Forge's planner can decompose it into tasks.
