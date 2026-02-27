# Forge Coding Standards

## Architecture
- SOLID principles: every class has one responsibility
- No function longer than 30 lines (extract helpers)
- No file longer than 300 lines (split into modules)
- Dependency injection over hard-coded dependencies

## Reuse-First
- Before writing ANY new function, search existing codebase
- Common patterns live in forge/core/utils/
- If 3+ lines of logic appear twice, extract to shared function

## Modularity
- One module = one concern
- Public API at top of file, private helpers below
- No circular imports (enforced by validator)
- Type hints on all public functions

## Error Handling
- Custom exception hierarchy (ForgeError base)
- Never bare except
- Errors carry context (what failed, why, what to do)

## Testing
- Every public function has at least one test
- Tests live next to code: module.py -> module_test.py
- No test depends on another test's state
