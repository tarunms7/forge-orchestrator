# Forge One-Day Reliability Release Plan

> **Mission:** Use Forge to make Forge materially more reliable and release-ready today. This is a war-room execution plan, not a brainstorming doc.

## Goal

Ship a **reliability-and-trust release** for Forge by the end of today.

Success means Forge is meaningfully better on:

- pipeline reliability
- resume / retry / cancel behavior
- multi-repo execution
- review reliability on real diffs
- CI auto-fix loop reliability
- smoke / gauntlet validation
- operator confidence in TUI and web surfaces

This is **not** a feature-expansion day. The focus is to make the current system dependable, measurable, and shippable.

---

## Non-Goals

Do **not** spend time today on:

- major new product features
- cosmetic-only redesigns
- broad refactors for elegance
- speculative architecture work with no impact on release gates
- polishing screenshots / marketing assets before the release gates are green

---

## Release Gates

By the end of today, all of these must be true:

1. Python lint is green.
2. Core Python test suite is green, or any remaining failures are isolated to a tiny explicit known-issues list created today.
3. Web lint/build is green.
4. Mock gauntlet passes.
5. At least one live gauntlet scenario works end to end, even if still marked experimental.
6. Smoke test passes end to end, or the current smoke script is repaired/replaced and then passes.
7. One multi-repo end-to-end flow succeeds.
8. One interrupt/resume flow succeeds.
9. One CI auto-fix loop succeeds.
10. A short release note exists summarizing what improved, how it was validated, and what still needs work next.

If a task does not help close one of these gates, it is low priority today.

---

## Execution Protocol For Forge

When executing this plan, Forge must follow these rules:

1. **Optimize for the release gates, not for elegance.**
2. **Start with red signals:** run the validation commands first, identify failures, then fix them in priority order.
3. **Work in tight loops:** after each fix, rerun the narrowest relevant test, then rerun the broader gate.
4. **Do not stop at code changes:** keep going through validation until the gate is green or a real blocker is found.
5. **Prefer changes in existing systems** over introducing new abstractions today.
6. **Preserve the product thesis:** planning, contracts, orchestration, review, merge, TUI, API, and gauntlet should become more trustworthy, not more fragmented.
7. **Write down outcomes:** when a major gate is fixed, update the release note before moving on.
8. **Do not silently skip hard validations.** If a command is flaky or broken, fix the command or the code around it.
9. **Keep scope frozen:** no unrelated cleanup.
10. **If blocked, degrade gracefully:** isolate the blocker, record it, and move to the next highest-value gate.

---

## Priority Order

Work these in order:

### P0 — Core Validation

- `ruff`
- core `pytest`
- web lint/build

### P1 — System Reliability

- resume / retry / interrupt lifecycle
- multi-repo flows
- review reliability
- CI auto-fix loop

### P2 — Evaluation And Trust

- gauntlet mock
- gauntlet live
- smoke test

### P3 — Operator Confidence

- TUI state clarity
- web task/history correctness
- release note

Do not invert this order unless a lower-priority item is the blocker for a higher-priority gate.

---

## Workstream A — Baseline And Failure Inventory

### Objective

Establish the real current state before making changes.

### Commands

Run these from repo root:

```bash
uv run ruff check
uv run pytest -q --maxfail=20
cd web && npm run lint && npm run build
cd ..
uv run forge gauntlet --format summary
```

If `forge gauntlet --live` is still not implemented, treat that as a release gap and fix it under Workstream D.

### Deliverable

Create a short release note file summarizing:

- failing gates
- exact failing tests / commands
- which release gates are currently blocked

Recommended file:

`docs/superpowers/releases/2026-04-04-reliability-release.md`

---

## Workstream B — Core Reliability Fixes

### Objective

Make the core Python system pass its validation gates.

### Focus Areas

- `forge/core/daemon.py`
- `forge/core/daemon_executor.py`
- `forge/core/daemon_review.py`
- `forge/core/daemon_helpers.py`
- `forge/core/preflight.py`
- `forge/core/integration.py`
- `forge/core/health_monitor.py`
- `forge/core/planning/*`
- any directly related failing tests

### Required Behaviors

- planning does not fail on partial / mocked snapshot metadata
- task dispatch remains stable under retries and active-task edge cases
- review diff generation is correct and deterministic
- preflight behaves correctly for single-repo and multi-repo workspaces
- health monitor does not misclassify blocked / waiting pipelines
- pause / resume / interrupt state transitions behave consistently

### Execution Rule

Always fix the highest-fanout failures first. If one failure cluster explains many test failures, prioritize that cluster.

### Validation Loop

After each fix:

```bash
uv run pytest -q <narrow failing slice>
uv run pytest -q --maxfail=20
```

Do not move on until the core suite is materially healthier.

---

## Workstream C — Web And API Release Gate

### Objective

Make the web surface trustworthy enough to support a release.

### Focus Areas

- `forge/api/app.py`
- `forge/api/routes/*`
- `web/src/app/*`
- `web/src/components/*`
- `web/src/lib/*`
- `web/src/stores/*`

### Required Behaviors

- dashboard loads health/stats/history correctly
- task view reflects live pipeline state correctly
- auth and task APIs don’t regress
- build and lint succeed

### Validation

```bash
cd web
npm run lint
npm run build
cd ..
```

If web issues are mostly state/contract mismatches, fix the API/state contracts instead of patching around symptoms in the UI.

---

## Workstream D — Evaluation Becomes Real

### Objective

Use gauntlet and smoke as real release gates, not side tools.

### Focus Areas

- `forge/gauntlet/*`
- `forge/cli/gauntlet.py`
- `docs/gauntlet.md`
- `scripts/smoke_test.sh`

### Required Behaviors

- mock gauntlet passes
- live gauntlet exists and can run at least one scenario
- smoke test uses the local Forge build correctly and passes

### Required Improvements

1. Implement or repair live mode in the gauntlet runner.
2. Ensure at least `happy_path` can run live.
3. Verify scenario selection, JSON output, and exit codes.
4. Repair the smoke script if it fails due to stale assumptions.

### Validation

```bash
uv run forge gauntlet --format summary
uv run forge gauntlet --live -s happy_path --format summary
./scripts/smoke_test.sh
```

If live gauntlet is too expensive to run broadly, keep it minimal but real.

---

## Workstream E — Multi-Repo And Lifecycle Confidence

### Objective

Prove that Forge handles the hard orchestration cases.

### Focus Areas

- multi-repo preflight and branch setup
- contract generation across repos
- merge and post-merge integration checks
- interrupted execution and resume
- retrying failed tasks without corrupting the pipeline state

### Reference Areas

- `forge/core/preflight.py`
- `forge/core/daemon.py`
- `forge/core/daemon_executor.py`
- `forge/core/integration.py`
- `forge/tui/app.py`
- `forge/tui/state.py`

### Validation

Run the relevant targeted tests first, then ensure at least one real or fixture-backed end-to-end path works.

Suggested commands:

```bash
uv run pytest -q forge/core/preflight_test.py forge/core/daemon_test.py forge/core/daemon_pool_test.py forge/core/planning_regression_test.py
uv run pytest -q forge/tui/app_handlers_test.py forge/tui/state_test.py forge/tui/screens/pipeline_test.py
uv run forge gauntlet -s multi_repo_contracts --format summary
uv run forge gauntlet -s resume_after_interrupt --format summary
```

---

## Workstream F — CI Auto-Fix Loop

### Objective

Make Forge capable of recovering from a failed PR CI run with confidence.

### Focus Areas

- `forge/core/ci_watcher.py`
- `forge/api/routes/tasks.py`
- related tests

### Required Behaviors

- correctly detects failed checks
- fetches actionable logs
- dispatches a fix agent
- retries within budget and max-attempt bounds
- exits cleanly on cancel / timeout / PR close

### Validation

```bash
uv run pytest -q forge/core/ci_watcher_test.py forge/api/routes/tasks_test.py
```

If a real end-to-end CI repair can be exercised today, do it once and record the result in the release note.

---

## Workstream G — TUI And Operator Clarity

### Objective

Make it obvious to the operator what Forge is doing and why.

### Focus Areas

- `forge/tui/app.py`
- `forge/tui/state.py`
- `forge/tui/screens/*`
- `forge/tui/widgets/*`

### Required Behaviors

- planning, execution, review, merge, paused, interrupted, partial-success states all render clearly
- pending human decisions are visible
- queue/scheduling state is legible
- history / replay / resume affordances are trustworthy

### Validation

```bash
uv run pytest -q forge/tui
```

Any TUI work today must improve trust, not aesthetics for aesthetics’ sake.

---

## Final Verification Matrix

Before stopping, run this full matrix:

```bash
uv run ruff check
uv run pytest -q
cd web && npm run lint && npm run build && cd ..
uv run forge gauntlet --format summary
uv run forge gauntlet --live -s happy_path --format summary
./scripts/smoke_test.sh
```

If a command cannot be made green today, record:

- exact command
- current failure
- why it is blocked
- what was tried
- next concrete step

in the release note.

---

## Required Final Deliverables

Before ending, Forge must produce:

1. Code changes that materially improve reliability.
2. Passing output for as many release gates as possible.
3. A release note at:

`docs/superpowers/releases/2026-04-04-reliability-release.md`

The release note must contain:

- what changed
- what was validated
- measured pass/fail status for each release gate
- remaining known limitations
- recommended next 3 follow-up items

---

## Launch Prompt For Forge

Use this prompt with Forge:

```text
Execute the one-day reliability release plan in docs/superpowers/plans/2026-04-04-reliability-release-war-room.md.

Your mission is to make Forge materially more reliable and release-ready today. Start by running the baseline validation commands, identify the failing gates, and then fix them in strict priority order. Do not add unrelated features. Persist through code changes, targeted reruns, and broad verification until the release gates are green or there is a real blocker. Keep the release note updated as you go.
```

Recommended command:

```bash
uv run forge run "Execute the one-day reliability release plan in docs/superpowers/plans/2026-04-04-reliability-release-war-room.md. Your mission is to make Forge materially more reliable and release-ready today. Start by running the baseline validation commands, identify the failing gates, and then fix them in strict priority order. Do not add unrelated features. Persist through code changes, targeted reruns, and broad verification until the release gates are green or there is a real blocker. Keep the release note updated as you go." --project-dir . --deep-plan --spec docs/superpowers/plans/2026-04-04-reliability-release-war-room.md
```

If using the TUI instead, open `forge tui` from repo root and paste the same prompt.
