# forge-smoke-test

Smoke test fixture for the [Forge Orchestrator](https://github.com/tarunms7/forge-orchestrator) pipeline.

## The bug

`calculator.py` has a known divide-by-zero bug:

```python
def divide(a, b):
    return a / b  # BUG: crashes when b is 0
```

`test_calculator.py::test_divide_by_zero` fails because of it.

## Purpose

`main` always has this bug. The Forge smoke test (`scripts/smoke_test.sh` in the main repo) clones this repo, runs the LOCAL build of forge to fix it, and verifies:

1. Forge completes the full pipeline (plan → agent → review → PR)
2. The fix branch passes all tests

## Important

**Never merge fix PRs to main.** Forge creates branches — leave them open or close without merging. `main` must always have the bug so the smoke test can run again.
