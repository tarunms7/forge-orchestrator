# Architectural Decisions

## 2026-02-27: Initial Decisions
- Hybrid orchestration: LLM plans, code enforces
- Claude Code primary backend, adapter interface for others
- SQLite default, Postgres optional (SQLAlchemy abstracts)
- psutil for resource monitoring, dynamic throttle
- Mandatory 3-gate review pipeline
- Max ~4 concurrent agents (research-backed)
- TDD throughout, pytest + pytest-asyncio
