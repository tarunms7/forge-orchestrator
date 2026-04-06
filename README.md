<div align="center">

# FORGE

### Ship features, not prompts.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Claude Code](https://img.shields.io/badge/powered%20by-Claude%20Code-cc785c?logo=anthropic&logoColor=white)](https://docs.anthropic.com/en/docs/claude-code)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/tarunms7/forge-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/tarunms7/forge-orchestrator/actions/workflows/ci.yml)

Describe what you want. Forge plans it, runs parallel AI agents, reviews every line, and opens a pull request.

**Claude Code for thinking. Forge for shipping.**

[Why Forge?](#why-not-just-use-claude-code) · [Install](#install) · [How It Works](#how-it-works) · [In Action](#see-it-in-action) · [Multi-Repo](#multi-repo-workspaces) · [Configuration](#configuration) · [CLI Reference](#cli-reference)

</div>

<br/>

```bash
forge tui
```

<p align="center">
  <img src="docs/screenshots/forge_tui_dashboard.png" alt="Forge TUI — home screen" width="720" />
</p>

Type your task. Hit Ctrl+S. Walk away. Come back to a pull request.

---

## Why not just use Claude Code?

Claude Code is great. Forge is built on it. Claude remains the default provider, and Forge can now route selected stages through OpenAI when you explicitly enable it. So why not just use Claude Code directly?

**Because you're the bottleneck.**

Claude Code handles one task at a time. You prompt, you wait, you review, you prompt the next thing. For a 5-task feature, that's 5 rounds of your attention. Forge runs all 5 in parallel while you do something else.

| Scenario | Claude Code | Forge |
|---|---|---|
| Fix a typo | ✅ Perfect. One file, 10 seconds. | Overkill. Don't use Forge for this. |
| Add a button | ✅ Great. Quick and interactive. | Unnecessary. |
| "Build JWT auth with registration, email verification, password reset, and tests" | You prompt 4 times. Review 4 times. Hope they work together. | Plans all 4. Generates contracts so they agree on APIs. Runs in parallel. Reviews every diff. One PR. |
| "Migrate 15 endpoints from requests to httpx" | One by one. An hour of your time. | All 15 in parallel. 8 minutes. You weren't even watching. |
| "Fix issue #42" | You read the issue. Understand the code. Write the fix. | `forge fix 42`. Done. |
| Multi-repo backend + frontend change | Switch repos. Copy-paste API shapes. Hope they match. | Agents in both repos get the same contract. They match on first try. |

**Forge doesn't replace Claude Code. It removes the human scheduling layer.** You still approve the plan. You still review the PR. But the 45 minutes between "approve plan" and "PR ready" — that's Forge, not you.

### When to use what

- **Claude Code** → Debugging, exploration, quick fixes, learning a codebase
- **Forge** → Features, migrations, multi-file changes, anything with 2+ parallel tasks

---

## What makes Forge different

Forge isn't another chat wrapper. It's an orchestration engine with systems that compound over time.

| You're doing this today | Forge does this instead |
|---|---|
| Prompting one thing at a time | Decomposes into a **DAG** and runs tasks **in parallel** |
| Hoping AI-written files agree on interfaces | **Contract Builder** generates binding API specs *before* coding starts |
| Manually reviewing every AI change | **5-gate review pipeline**: build, lint, test, LLM review, contracts |
| Copy-pasting between chat and terminal | Each agent works in an **isolated git worktree** — zero conflicts |
| Losing context in long sessions | Each agent gets a **focused prompt** + contracts + project lessons |
| Merging by hand | Auto **rebase + fast-forward merge**, then `gh pr create` |
| No idea what the AI cost | **Real-time cost tracking** with budget limits |
| AI makes the same mistakes twice | **Self-evolving learning** — Forge learns from failures and gets smarter |
| Waiting for AI to ask obvious questions | **Planner asks you first** — unclear requirements get clarified before agents run |

---

## See it in action

### Planning — Forge reads your codebase and builds a task graph

<p align="center">
  <img src="docs/screenshots/forge_planning_screen.png" alt="Forge — planning phase" width="720" />
</p>

The planner has the same power as Claude Code — full access to your CLAUDE.md, skills, memory, and MCP servers. It explores your codebase deeply, asks clarifying questions if something is ambiguous, and produces a structured task graph.

### Plan review — Approve, edit, or reject before agents run

<p align="center">
  <img src="docs/screenshots/forge_plans_screen.png" alt="Forge — task plan with dependencies" width="720" />
</p>

Every plan shows tasks with complexity, dependencies, file ownership, and a cost estimate. You can edit the plan, reorder tasks, or reject and re-plan with different instructions.

### Execution — Parallel agents with real-time learning

<p align="center">
  <img src="docs/screenshots/forge_execution_with_learning.png" alt="Forge — parallel execution" width="720" />
</p>

Each agent runs in its own git worktree with a focused prompt. When an agent fails and succeeds on retry, Forge captures what it learned and applies it to future runs. A health monitor watches for stuck tasks and deadlocks.

### Code review — Inspect every diff before merge

<p align="center">
  <img src="docs/screenshots/forge_diff_screen.png" alt="Forge — diff review" width="720" />
</p>

### Done — PR created, cost tracked, ready to merge

<p align="center">
  <img src="docs/screenshots/forge_final_pr_screen.png" alt="Forge — pipeline complete" width="720" />
</p>

---

## Install

> **Prerequisites:** Git 2.20+ and [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude login`).
> Optional: [`gh` CLI](https://cli.github.com) for automatic PR creation.

```bash
curl -fsSL https://raw.githubusercontent.com/tarunms7/forge-orchestrator/main/install.sh | sh
```

The installer handles Python 3.12, [uv](https://docs.astral.sh/uv/), the Forge CLI, and all dependencies. Safe to re-run to upgrade.

To upgrade later:

```bash
forge upgrade
```

<details>
<summary><b>Manual install</b></summary>

```bash
git clone https://github.com/tarunms7/forge-orchestrator.git
cd forge-orchestrator
uv tool install --python 3.12 ".[web]"
forge doctor
```

</details>

---

## Quick start

```bash
cd your-project
forge tui
```

Type what you want. Hit Ctrl+S. Forge auto-creates `.forge/` on first run.

Or skip the TUI:

```bash
forge run "Add input validation to all API endpoints"
```

Fix a GitHub issue directly:

```bash
forge fix 42    # Fetches issue #42 and builds a pipeline to fix it
```

---

## How it works

```
You: "Build a REST API with JWT auth and tests"
                    │
              1. PRE-FLIGHT
              Validates tools, branches, disk space
              — catches problems before spending money
                    │
              2. PLAN
              Provider-aware planning — reads codebase,
              asks clarifying questions, builds task DAG
                    │
              3. CONTRACT
              Generates binding API & type specs so
              agents agree on interfaces before coding
                    │
              4. EXECUTE
              Parallel agents in isolated git worktrees
              with real-time health monitoring
                    │
              5. REVIEW
              5-gate pipeline per task:
              build › lint › test › LLM review › contracts
                    │
              6. FORMAT + MERGE
              Auto-format, rebase, fast-forward merge,
              then gh pr create
```

### The Contract Builder

Two agents writing a backend API and a frontend client will invent different field names and response shapes. Forge solves this with **binding contracts** generated before any code is written:

```
POST /api/templates
  Request:  { name: string, description: string, tasks: TaskConfig[] }
  Response: { id: string, name: string, created_at: string }
  Producer: task-1 (backend)  │  Consumer: task-2 (frontend)
```

Producers implement the spec. Consumers call the spec. The reviewer checks compliance.

### Self-evolving learning

Forge gets smarter with every pipeline it runs:

- **Adaptive timeouts** — ESLint took 180s and failed? Next run starts at 360s. Automatically.
- **Agent learnings** — When a task fails then succeeds on retry, Forge asks "what did you do differently?" and stores the answer as a lesson.
- **Noise filtering** — Server 503s and transient errors aren't stored. Only real pattern changes become lessons.
- **Cross-pipeline** — Lessons persist across projects. What Forge learns on one repo helps on the next.

```bash
forge lessons list    # See what Forge has learned
forge lessons add     # Manually teach Forge something
```

### Pre-flight validation

Before any pipeline starts, Forge runs fast checks:

- Required provider tooling available
- Claude CLI installed and authenticated when Claude models are selected
- Codex-backed OpenAI models authenticated via `codex login` or `CODEX_API_KEY`
- Responses API models authenticated via `OPENAI_API_KEY`
- Git repo valid, base branch exists
- `gh` CLI available (for PR creation)
- Sufficient disk space (>1 GB)
- No uncommitted changes that would interfere
- Build/test commands resolvable
- Requested models exist in the provider registry and are valid for their pipeline stages

If anything fails, you get a clear error with a fix command — not a cryptic failure 5 minutes into planning.

### Pipeline health monitor

During execution, a background watchdog detects:

- **Stuck tasks** — in_progress for >15 minutes with no activity
- **Stuck reviews** — review gate running for >10 minutes
- **Deadlocks** — all tasks blocked with no way to make progress
- **Dependency cascades** — failed task blocking downstream work

Problems are logged and surfaced immediately, not discovered after the pipeline "completes."

---

## Multi-repo workspaces

Forge natively supports monorepos and multi-repo setups. Point it at a directory with multiple git repos:

```
super-repo/
  backend/     ← git repo on 'develop' branch
  frontend/    ← git repo on 'main' branch
  shared-lib/  ← git repo on 'main' branch
```

```bash
cd super-repo
forge tui
```

Forge auto-detects the repos and uses **whatever branch each repo is currently checked out to** as the base. No config needed. The TUI shows branch selectors for each repo so you can override at submission time.

Agents get repo-aware prompts. The planner understands cross-repo dependencies. Contracts ensure APIs match across repos.

---

## Configuration

All settings use the `FORGE_` prefix. Build and test commands are **auto-detected** from your project.

| Setting | Default | What it does |
|---|---|---|
| `FORGE_MAX_AGENTS` | 5 | Max concurrent agents |
| `FORGE_AGENT_TIMEOUT_SECONDS` | 600 | Per-task timeout |
| `FORGE_MAX_RETRIES` | 5 | Retries per task on failure |
| `FORGE_BUDGET_LIMIT_USD` | 0 (unlimited) | Per-pipeline spend cap |
| `FORGE_MODEL_STRATEGY` | auto | `fast` / `auto` / `quality` |
| `FORGE_OPENAI_ENABLED` | false | Enables the OpenAI provider and catalog |
| `FORGE_CONTRACT_BUILDER_MODEL` | *(unset)* | Override contract-builder model |
| `FORGE_PLANNER_MODEL` | *(unset)* | Override planner model with `provider:model` |
| `FORGE_AGENT_MODEL_LOW` | *(unset)* | Override the low-complexity agent model |
| `FORGE_AGENT_MODEL_MEDIUM` | *(unset)* | Override the medium-complexity agent model |
| `FORGE_AGENT_MODEL_HIGH` | *(unset)* | Override the high-complexity agent model |
| `FORGE_REVIEWER_MODEL` | *(unset)* | Override reviewer model |
| `FORGE_CI_FIX_MODEL` | *(unset)* | Override CI fix model |
| `FORGE_REQUIRE_APPROVAL` | false | Human approval before merge |
| `FORGE_BUILD_CMD` | *(auto)* | Override build command |
| `FORGE_TEST_CMD` | *(auto)* | Override test command |

```bash
FORGE_BUDGET_LIMIT_USD=5 forge run "Refactor auth to OAuth2"
```

```bash
FORGE_OPENAI_ENABLED=true \
FORGE_AGENT_MODEL_MEDIUM=openai:gpt-5.4 \
forge run "Add audit logging to billing flows"
```

```bash
FORGE_OPENAI_ENABLED=true \
OPENAI_API_KEY=sk-... \
FORGE_PLANNER_MODEL=openai:o3 \
FORGE_AGENT_MODEL_MEDIUM=openai:gpt-5.4 \
forge run "Add audit logging to billing flows"
```

```bash
FORGE_OPENAI_ENABLED=true \
FORGE_PLANNER_MODEL=claude:opus \
FORGE_AGENT_MODEL_MEDIUM=claude:sonnet \
FORGE_REVIEWER_MODEL=openai:gpt-5.4-mini \
forge run "Audit provider routing regressions"
```

### Project config

Drop a `forge.toml` in your `.forge/` directory for per-project settings:

```toml
[agents]
max_parallel = 4
timeout_seconds = 300

[review]
max_retries = 3

[lint]
check_cmd = "npm run lint"
fix_cmd = "npm run lint:fix"

[routing]
planner = "claude:opus"
agent_medium = "openai:gpt-5.4"
reviewer = "openai:gpt-5.4"
reviewer_reasoning_effort = "high"

[[custom_models]]
alias = "sonnet-plus"
provider = "claude"
canonical_id = "claude-sonnet-plus-20260401"
backend = "claude-code-sdk"
```

### Model routing

Forge resolves models per pipeline stage. Built-in model specs are:

| Provider | Models |
|---|---|
| `claude` | `claude:opus`, `claude:sonnet`, `claude:haiku` |
| `openai` | `openai:gpt-5.4`, `openai:gpt-5.4-mini`, `openai:gpt-5.3-codex`, `openai:o3` |

| Strategy | Planner | Agents | Reviewer |
|---|---|---|---|
| `fast` | Sonnet | Haiku | Haiku |
| `auto` | Opus | Sonnet | Sonnet |
| `quality` | Opus | Opus | Sonnet |

Routing precedence:

1. Explicit per-stage overrides from the UI or environment
2. `.forge/forge.toml` `[routing]`
3. Strategy defaults

If you previously exported smoke-test overrides like `FORGE_AGENT_MODEL_LOW`,
`FORGE_AGENT_MODEL_MEDIUM`, `FORGE_AGENT_MODEL_HIGH`, or `FORGE_REVIEWER_MODEL`,
they will continue to win until you unset them. A quick reset looks like:

```bash
unset FORGE_AGENT_MODEL_LOW FORGE_AGENT_MODEL_MEDIUM FORGE_AGENT_MODEL_HIGH
unset FORGE_REVIEWER_MODEL FORGE_PLANNER_MODEL
unset FORGE_AGENT_MODEL_LOW_REASONING_EFFORT FORGE_AGENT_MODEL_MEDIUM_REASONING_EFFORT
unset FORGE_AGENT_MODEL_HIGH_REASONING_EFFORT FORGE_REVIEWER_REASONING_EFFORT
```

You can also override reasoning effort per stage in `.forge/forge.toml` using:

- `planner_reasoning_effort`
- `agent_low_reasoning_effort`
- `agent_medium_reasoning_effort`
- `agent_high_reasoning_effort`
- `reviewer_reasoning_effort`
- `contract_builder_reasoning_effort`
- `ci_fix_reasoning_effort`

Accepted values are `low`, `medium`, and `high`. OpenAI Codex/Responses models use native reasoning-effort controls. Claude stages also honor these settings, but Claude Code receives them as prompt-level effort guidance because the Claude SDK does not expose a native reasoning-effort parameter.

Forge persists the exact resolved provider snapshot in `pipelines.provider_config` when the pipeline is created. Restarts, retries, follow-up work, and webhook resumptions use that snapshot, not whatever your current settings happen to be later.

Any stage can be routed independently as long as the selected model has the required hard capabilities for that stage. Common examples:

- Claude planner + Claude agent + Codex reviewer
- O3 planner + Codex agent + Claude reviewer
- Claude planner + Codex CI fix + Claude merge/review flow

Helper intelligence calls also follow the active stage routing instead of hardcoding Claude:

- branch-name generation follows the planner model
- PR-title generation follows the reviewer model
- follow-up question classification follows the planner model

If any routed stage points at an OpenAI model, Forge automatically registers the OpenAI provider for that pipeline even when you did not explicitly set `FORGE_OPENAI_ENABLED=true`.

`[[custom_models]]` entries are validated against the active provider registry and then injected as experimental catalog entries for that project. That means custom aliases are now executable, not just parsed.

Codex-backed models use your existing `codex login` subscription session when available, and Forge only falls back to key auth for Codex if `CODEX_API_KEY` is explicitly set or no Codex login is present. Forge prefers your installed `codex` CLI when available so it shares the same subscription/auth state you already use manually. `openai:o3` stays on the Responses API path and requires `OPENAI_API_KEY`.

Codex-backed executions now run with the same high-power posture Forge already uses for Claude coding agents:

- full-access Codex sandbox mode
- automatic execution with no per-command approval prompts
- live network access and native web search enabled
- Forge-level safety policy still enforced for explicitly denied operations

If you need to point Forge at a specific Codex binary, set `FORGE_CODEX_PATH=/absolute/path/to/codex`.

Manual task reruns also refund one consumed retry slot before rescheduling the task, so abrupt provider failures or exhausted review loops do not leave a human-triggered recovery with zero room for error.

---

## CLI reference

| Command | What it does |
|---|---|
| `forge tui` | Launch the terminal UI |
| `forge run "task"` | Run a pipeline from the command line |
| `forge fix 42` | Fix GitHub issue #42 via pipeline |
| `forge status` | Show pipeline status |
| `forge stats` | Pipeline analytics and metrics |
| `forge logs <id>` | Event timeline for a pipeline |
| `forge lessons list` | Show learned lessons |
| `forge lessons add` | Manually add a lesson |
| `forge doctor` | Check environment health |
| `forge clean` | Remove stale worktrees and branches |
| `forge init` | Initialize Forge in a project |
| `forge serve` | Start the web dashboard |
| `forge upgrade` | Upgrade to latest version |
| `forge ping` | Verify Claude CLI is reachable |

---

## Web dashboard

```bash
forge serve   # Backend :8000 + Frontend :3000
```

Live pipeline progress via WebSocket, interactive plan editing, contract viewer, review gate results, and cost tracking. Set `FORGE_JWT_SECRET` for multi-user auth.

> Requires a [git clone install](#contributing), not the one-line installer.

---

## Architecture

```
forge/
  cli/           # Click CLI — 14 commands
  tui/           # Textual TUI — full terminal UI with live pipeline view
  core/          # Orchestration — daemon, planner, executor, scheduler, health monitor
  agents/        # Shared agent runtime + prompt building across providers
  merge/         # Git worktree lifecycle — create, merge, cleanup
  review/        # Multi-gate review — lint, build, test, LLM review
  learning/      # Self-evolving — lesson store, runtime guard, extractor
  storage/       # SQLite via SQLAlchemy async — tasks, pipelines, events, analytics
  config/        # Settings, project config, forge.toml, workspace.toml
  providers/     # Claude + OpenAI provider adapters, catalog, safety, costs
  api/           # FastAPI backend for web dashboard
web/             # Next.js frontend — TypeScript, Tailwind, Zustand
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `forge: command not found` | Re-run the installer, or add `~/.local/bin` to PATH |
| Claude CLI not authenticated | `claude login` |
| `gh: command not found` | Install [GitHub CLI](https://cli.github.com) and `gh auth login` |
| Pipeline stuck | Forge auto-detects stuck tasks. Check `.forge/forge.log` |
| Database issues | `forge doctor` |
| Upgrade fails | `forge upgrade` auto-handles Python 3.12 installation |

---

## Contributing

```bash
git clone https://github.com/tarunms7/forge-orchestrator.git
cd forge-orchestrator
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,web]'
python -m pytest forge/ -q
```

CI runs ruff lint + format + 2200+ tests on every PR.

---

## License

MIT
