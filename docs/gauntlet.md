# Forge Gauntlet — Self-Test Suite

The Gauntlet is Forge's built-in end-to-end test harness. It exercises the full pipeline lifecycle — from preflight checks through planning, contract generation, execution, review, and integration — using deterministic fixtures and a mock pipeline. This lets you validate that Forge's orchestration logic works correctly without invoking real Claude SDK calls or incurring API costs.

## Quick Start

```bash
# Run all scenarios
forge gauntlet

# Run a specific scenario
forge gauntlet -s happy_path

# Run with chaos timing jitter on compatible scenarios
forge gauntlet --chaos

# JSON output for CI
forge gauntlet --format json

# One-line summary
forge gauntlet --format summary
```

## Scenarios

| Scenario | What It Tests | Chaos Compatible |
|---|---|---|
| `happy_path` | Full pipeline success with no injected failures. All 6 stages run and pass. | No |
| `multi_repo_contracts` | Cross-repo contract generation. Validates API contracts link producers to consumers, type contracts reference shared types, and tasks have correct repo assignments. | No |
| `resume_after_interrupt` | Pipeline interrupt after execution, persist state to disk, then resume from that saved state. Verifies completed stages are preserved and only remaining stages run after restart. | Yes |
| `review_gate_failure` | Injected review failure. Validates pipeline stops at review, does not run integration, and correctly reports error state. | Yes |
| `integration_failure` | Injected integration failure. Validates all 6 stages run (integration is post-merge), earlier stages remain passed, and the failure is detected. | No |

## Report Format

The default Rich report shows:

- A summary panel with pass/fail count and total duration
- Per-scenario panels with stage results (pass/fail icons), assertion results, and cost
- Artifact listings and error details for failed scenarios

JSON output (`--format json`) returns only the full `GauntletResult` model serialized via Pydantic on stdout, suitable for CI parsing. Summary format (`--format summary`) returns a single line like:

```
Gauntlet: 5/5 passed in 12.3s
```

## Live Mode

```bash
forge gauntlet --live
```

Live mode uses the real `ForgeDaemon` and Claude SDK instead of `MockPipeline`. This runs actual agent executions against the fixture workspace, which:

- **Costs real money** — each scenario invokes Claude SDK calls
- **Takes longer** — minutes instead of seconds
- **Requires authentication** — `claude login` must be configured

Use live mode for pre-release validation or when you need to verify the full stack including SDK integration. For regular development and CI, mock mode (the default) is sufficient.

> **Note:** Live mode is not yet implemented. Running with `--live` will return an error indicating this.

## Adding New Scenarios

1. Create a new file in `forge/gauntlet/scenarios/`, e.g. `my_scenario.py`:

```python
from forge.gauntlet.mock_pipeline import MockPipeline
from forge.gauntlet.models import AssertionResult, ScenarioResult

async def run_my_scenario(
    pipeline: MockPipeline, workspace: dict[str, str]
) -> ScenarioResult:
    stages = await pipeline.run_full("task description")
    assertions = []

    # Add your assertions
    assertions.append(
        AssertionResult(
            name="my_check",
            passed=True,
            message="Everything looks good",
        )
    )

    return ScenarioResult(
        name="my_scenario",
        passed=all(a.passed for a in assertions),
        duration_s=0.0,  # filled by runner
        stages=stages,
        assertions=assertions,
    )
```

2. Register it in `forge/gauntlet/scenarios/__init__.py`:

```python
from .my_scenario import run_my_scenario

SCENARIO_REGISTRY["my_scenario"] = ScenarioConfig(
    name="my_scenario",
    description="What this scenario tests",
    tags=["smoke"],
    chaos_compatible=False,
)

SCENARIO_FUNCTIONS["my_scenario"] = run_my_scenario
```

3. Add a test in `forge/gauntlet/scenarios/scenarios_test.py` that calls the function directly with a `MockPipeline` and fixture workspace.

## Architecture

```
┌─────────────────┐
│  GauntletRunner  │  Orchestrates fixture creation + scenario execution
└────────┬────────┘
         │ creates
         ▼
┌─────────────────┐
│ Fixture Workspace│  3 git repos: backend, frontend, shared-types
│  (tmp directory) │  Each with intentional bugs for agents to find
└────────┬────────┘
         │ passed to
         ▼
┌─────────────────┐
│  MockPipeline    │  Deterministic stage simulation (no real SDK calls)
│                  │  Supports fail_at injection + chaos mode
└────────┬────────┘
         │ used by
         ▼
┌─────────────────┐
│   Scenarios      │  Each scenario calls MockPipeline methods,
│ (happy_path, …)  │  collects StageResults, runs assertions
└────────┬────────┘
         │ returns
         ▼
┌─────────────────┐
│ GauntletResult   │  Aggregate pass/fail + per-scenario details
└─────────────────┘
```

- **GauntletRunner** creates an isolated temp directory per scenario, builds the fixture workspace, instantiates a `MockPipeline`, and delegates to the scenario function. It collects `ScenarioResult` objects into a `GauntletResult`.

- **Fixture Workspace** (`create_fixture_workspace`) sets up 3 git repos with real files and intentional bugs — a backend Flask app with a division-by-zero bug, a frontend with a wrong import field, and shared Pydantic types.

- **MockPipeline** mirrors the `ForgeDaemon` stage sequence (preflight → planning → contracts → execution → review → integration) but uses deterministic task graphs and contract sets. It supports `fail_at` for injecting failures at specific stages, `chaos` for random delay jitter, and persisted resume state for interruption scenarios.

- **Scenarios** are async functions that exercise specific pipeline behaviors and return `ScenarioResult` with assertions about what happened. Each scenario is registered in `SCENARIO_REGISTRY` (metadata) and `SCENARIO_FUNCTIONS` (callable).
