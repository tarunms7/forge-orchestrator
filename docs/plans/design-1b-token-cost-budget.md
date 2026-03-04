# Design 1B: Token & Cost Tracking with Budget Limits

> Status: DRAFT
> Author: Claude
> Date: 2026-03-04

---

## 1. SDK Integration: Getting Token Counts from claude-code-sdk

### ResultMessage Fields (source: `.venv/.../claude_code_sdk/types.py`)

```python
@dataclass
class ResultMessage:
    subtype: str
    duration_ms: int
    duration_api_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None   # <-- token data lives here
    result: str | None = None
```

**Key finding:** `ResultMessage` has no dedicated `input_tokens` / `output_tokens` fields.
Token data is in the **`usage` dict** — an opaque `dict[str, Any]` populated from the
Claude CLI's JSON output. Expected keys (from Anthropic API convention):

```python
{
    "input_tokens": 12345,
    "output_tokens": 6789,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}
```

### Extraction Strategy

Modify `sdk_query()` to return a richer result, and add a helper to safely extract tokens:

```python
# forge/core/sdk_helpers.py — new dataclass

@dataclass
class SdkResult:
    """Enriched result from an SDK query with token + cost data."""
    result_text: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    duration_ms: int
    num_turns: int
    is_error: bool

    @classmethod
    def from_result_message(cls, msg: ResultMessage | None) -> "SdkResult":
        if msg is None:
            return cls(
                result_text="", cost_usd=0.0, input_tokens=0,
                output_tokens=0, duration_ms=0, num_turns=0, is_error=True,
            )
        usage = msg.usage or {}
        return cls(
            result_text=msg.result or "",
            cost_usd=msg.total_cost_usd or 0.0,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            duration_ms=msg.duration_ms,
            num_turns=msg.num_turns,
            is_error=msg.is_error,
        )
```

`sdk_query()` continues to return `ResultMessage | None`. Callers wrap with
`SdkResult.from_result_message()` when they need token data.

### Where to Extract

| Call site | File | Currently returns | Change |
|-----------|------|-------------------|--------|
| Agent execution | `adapter.py:ClaudeAdapter.run()` | `AgentResult(cost_usd=...)` | Add `input_tokens`, `output_tokens` to `AgentResult` |
| LLM review | `llm_review.py:gate2_llm_review()` | `GateResult` | Return cost + tokens via new `ReviewCostInfo` alongside `GateResult` |
| Planner | `claude_planner.py:generate_plan()` | `str` (JSON) | Return `(str, SdkResult)` tuple or store cost on instance |

---

## 2. DB Schema Changes

### TaskRow — new columns

```python
class TaskRow(Base):
    __tablename__ = "tasks"
    # ... existing columns ...

    cost_usd: Mapped[float] = mapped_column(default=0.0)          # EXISTING — keeps total
    agent_cost_usd: Mapped[float] = mapped_column(default=0.0)    # NEW — agent-only cost
    review_cost_usd: Mapped[float] = mapped_column(default=0.0)   # NEW — reviewer-only cost
    input_tokens: Mapped[int] = mapped_column(default=0)           # NEW — cumulative input
    output_tokens: Mapped[int] = mapped_column(default=0)          # NEW — cumulative output
```

### PipelineRow — new columns

```python
class PipelineRow(Base):
    __tablename__ = "pipelines"
    # ... existing columns ...

    planner_cost_usd: Mapped[float] = mapped_column(default=0.0)   # NEW
    total_cost_usd: Mapped[float] = mapped_column(default=0.0)     # NEW — running total
    budget_limit_usd: Mapped[float] = mapped_column(default=0.0)   # NEW — 0 = unlimited
    estimated_cost_usd: Mapped[float] = mapped_column(default=0.0) # NEW — pre-execution estimate
```

### Migration Strategy

The existing `_add_missing_columns()` in `Database.initialize()` handles this automatically.
It inspects ORM columns vs actual DB columns and runs `ALTER TABLE ... ADD COLUMN` for any
missing ones. **No manual migration needed** — just add the columns to the ORM models.

Backward compatibility: all new columns default to `0` / `0.0`, so existing rows are valid.

### New DB Methods

```python
# forge/storage/db.py — additions to Database class

async def add_task_agent_cost(self, task_id: str, cost: float, input_tokens: int, output_tokens: int) -> None:
    """Add agent execution cost and tokens to a task."""
    async with self._session_factory() as session:
        task = await session.get(TaskRow, task_id)
        if task:
            task.agent_cost_usd = (task.agent_cost_usd or 0) + cost
            task.cost_usd = (task.cost_usd or 0) + cost
            task.input_tokens = (task.input_tokens or 0) + input_tokens
            task.output_tokens = (task.output_tokens or 0) + output_tokens
            await session.commit()

async def add_task_review_cost(self, task_id: str, cost: float, input_tokens: int, output_tokens: int) -> None:
    """Add review cost and tokens to a task."""
    async with self._session_factory() as session:
        task = await session.get(TaskRow, task_id)
        if task:
            task.review_cost_usd = (task.review_cost_usd or 0) + cost
            task.cost_usd = (task.cost_usd or 0) + cost
            task.input_tokens = (task.input_tokens or 0) + input_tokens
            task.output_tokens = (task.output_tokens or 0) + output_tokens
            await session.commit()

async def add_pipeline_cost(self, pipeline_id: str, cost: float, category: str = "agent") -> None:
    """Increment pipeline running total. category: 'planner' | 'agent' | 'review'."""
    async with self._session_factory() as session:
        result = await session.execute(
            select(PipelineRow).where(PipelineRow.id == pipeline_id)
        )
        row = result.scalar_one_or_none()
        if row:
            row.total_cost_usd = (row.total_cost_usd or 0) + cost
            if category == "planner":
                row.planner_cost_usd = (row.planner_cost_usd or 0) + cost
            await session.commit()

async def get_pipeline_cost(self, pipeline_id: str) -> float:
    """Get current cumulative pipeline cost."""
    async with self._session_factory() as session:
        result = await session.execute(
            select(PipelineRow).where(PipelineRow.id == pipeline_id)
        )
        row = result.scalar_one_or_none()
        return (row.total_cost_usd or 0.0) if row else 0.0

async def get_pipeline_budget(self, pipeline_id: str) -> float:
    """Get pipeline budget limit (0 = unlimited)."""
    async with self._session_factory() as session:
        result = await session.execute(
            select(PipelineRow).where(PipelineRow.id == pipeline_id)
        )
        row = result.scalar_one_or_none()
        return (row.budget_limit_usd or 0.0) if row else 0.0
```

---

## 3. AgentResult Changes

```python
# forge/agents/adapter.py

@dataclass
class AgentResult:
    """Outcome of an agent task execution."""
    success: bool
    files_changed: list[str]
    summary: str
    cost_usd: float = 0.0
    error: str | None = None
    input_tokens: int = 0       # NEW
    output_tokens: int = 0      # NEW
```

Update `ClaudeAdapter.run()` to populate these:

```python
# In ClaudeAdapter.run(), after getting result from sdk_query:
sdk_result = SdkResult.from_result_message(result)

return AgentResult(
    success=True,
    files_changed=files_changed,
    summary=result_text[:500] if result_text else "Task completed",
    cost_usd=sdk_result.cost_usd,
    input_tokens=sdk_result.input_tokens,
    output_tokens=sdk_result.output_tokens,
)
```

---

## 4. New Events and Payloads

### `pipeline:cost_update`

Emitted after every SDK call (agent, reviewer, planner) completes. Gives the frontend
a running total for the entire pipeline.

```python
{
    "event": "pipeline:cost_update",
    "data": {
        "pipeline_id": "abc123",
        "total_cost_usd": 2.34,              # cumulative pipeline total
        "planner_cost_usd": 0.15,            # planner subtotal
        "agent_cost_usd": 1.89,              # sum of all agent costs
        "review_cost_usd": 0.30,             # sum of all review costs
        "budget_limit_usd": 5.00,            # 0 if unlimited
        "budget_pct": 46.8,                  # percentage used (0 if unlimited)
    }
}
```

### `task:cost_update` (MODIFIED — existing event, richer payload)

```python
{
    "event": "task:cost_update",
    "data": {
        "task_id": "task-1",
        "cost_usd": 0.45,                    # incremental cost from this operation
        "total_cost_usd": 1.20,              # cumulative task total
        "category": "agent",                  # "agent" | "review"
        "input_tokens": 8500,                 # tokens from this operation
        "output_tokens": 3200,
    }
}
```

### `pipeline:budget_exceeded`

```python
{
    "event": "pipeline:budget_exceeded",
    "data": {
        "pipeline_id": "abc123",
        "total_cost_usd": 5.12,
        "budget_limit_usd": 5.00,
        "message": "Pipeline budget of $5.00 exceeded ($5.12 spent). Cancelling remaining tasks.",
    }
}
```

### `pipeline:cost_estimate`

Emitted alongside `pipeline:plan_ready` after the planner finishes.

```python
{
    "event": "pipeline:cost_estimate",
    "data": {
        "pipeline_id": "abc123",
        "estimated_cost_usd": 3.50,
        "breakdown": {
            "planner": 0.15,                   # already spent
            "agents": 2.85,                     # estimated
            "reviews": 0.50,                    # estimated
        },
        "confidence": "rough",                  # always "rough" for now
    }
}
```

---

## 5. Budget Enforcement Flow

```
                    ┌──────────────────┐
                    │  SDK call needed  │
                    │  (agent/review/   │
                    │   planner)        │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ Load pipeline    │
                    │ budget_limit_usd │
                    │ & total_cost_usd │
                    └────────┬─────────┘
                             │
                   ┌─────────▼──────────┐
                   │ budget_limit > 0 ? │
                   └──┬────────────┬────┘
                  yes │            │ no (unlimited)
                      │            │
            ┌─────────▼────────┐   │
            │ total_cost >=    │   │
            │ budget_limit ?   │   │
            └──┬──────────┬────┘   │
           yes │          │ no     │
               │          │        │
    ┌──────────▼───────┐  │        │
    │ emit             │  │        │
    │ pipeline:budget_ │  │        │
    │ exceeded         │  │        │
    └──────────┬───────┘  │        │
               │          │        │
    ┌──────────▼───────┐  │        │
    │ cancel_pipeline  │  │        │
    │ _hard()          │  │        │
    └──────────┬───────┘  │        │
               │          │        │
    ┌──────────▼───────┐  │        │
    │ emit             │  │        │
    │ pipeline:        │  │        │
    │ cancelled        │  │        │
    └──────────────────┘  │        │
                          │        │
               ┌──────────▼────────▼──┐
               │ Proceed with SDK     │
               │ call (sdk_query)     │
               └──────────┬───────────┘
                          │
               ┌──────────▼───────────┐
               │ SDK call completes   │
               │ Extract cost+tokens  │
               └──────────┬───────────┘
                          │
               ┌──────────▼───────────┐
               │ db.add_pipeline_cost │
               │ db.add_task_*_cost   │
               │ emit task:cost_update│
               │ emit pipeline:       │
               │   cost_update        │
               └──────────┬───────────┘
                          │
               ┌──────────▼───────────┐
               │ Post-call budget     │
               │ check (same logic)   │
               │ Cancel if exceeded   │
               └──────────────────────┘
```

### Budget Check Implementation

```python
# forge/core/budget.py — new module

from forge.storage.db import Database

class BudgetExceededError(Exception):
    """Raised when pipeline cost exceeds its budget."""
    def __init__(self, total: float, limit: float):
        self.total = total
        self.limit = limit
        super().__init__(f"Budget exceeded: ${total:.2f} / ${limit:.2f}")


async def check_budget(db: Database, pipeline_id: str) -> None:
    """Check if the pipeline has exceeded its budget. Raises BudgetExceededError if so."""
    budget = await db.get_pipeline_budget(pipeline_id)
    if budget <= 0:
        return  # unlimited

    cost = await db.get_pipeline_cost(pipeline_id)
    if cost >= budget:
        raise BudgetExceededError(cost, budget)
```

### Integration Points

The budget check runs **before** each SDK call in three places:

1. **`daemon_executor.py:_run_agent()`** — before `self._stream_agent()`
2. **`daemon_review.py:_run_review()`** — before calling `gate2_llm_review()`
3. **`daemon.py:_plan()`** — before calling `planner.generate_plan()`

Each call site catches `BudgetExceededError`, emits the event, and cancels:

```python
# Example: in _run_agent()
try:
    await check_budget(db, pipeline_id)
except BudgetExceededError as e:
    await self._emit("pipeline:budget_exceeded", {
        "pipeline_id": pipeline_id,
        "total_cost_usd": e.total,
        "budget_limit_usd": e.limit,
        "message": str(e),
    }, db=db, pipeline_id=pipeline_id)
    await db.cancel_pipeline_hard(pipeline_id)
    await self._emit("pipeline:cancelled", {"reason": "budget_exceeded"}, db=db, pipeline_id=pipeline_id)
    return False
```

---

## 6. API Changes

### CreateTaskRequest — new field

```python
# forge/api/models/schemas.py

class CreateTaskRequest(BaseModel):
    description: str
    project_path: str
    extra_dirs: list[str] = Field(default_factory=list)
    model_strategy: str = "auto"
    images: list[str] = Field(default_factory=list)
    branch_name: str | None = None
    budget_limit_usd: float | None = None     # NEW — optional per-pipeline budget
```

### Pipeline creation — store budget

```python
# forge/api/routes/tasks.py — in create_task()

await db.create_pipeline(
    id=pipeline_id,
    description=body.description,
    project_dir=body.project_path,
    model_strategy=body.model_strategy,
    user_id=user_id,
    # NEW: store budget (use per-pipeline value or fall back to global setting)
    budget_limit_usd=body.budget_limit_usd or settings.budget_limit_usd,
)
```

### Pipeline detail response — add cost fields

```python
# In the GET /tasks/{pipeline_id} response hydration:

"planner_cost_usd": pipeline.planner_cost_usd or 0,
"total_cost_usd": pipeline.total_cost_usd or 0,
"budget_limit_usd": pipeline.budget_limit_usd or 0,
"estimated_cost_usd": pipeline.estimated_cost_usd or 0,
```

### Task detail in response — add breakdown

```python
# In task serialization:

"agent_cost_usd": task.agent_cost_usd or 0,
"review_cost_usd": task.review_cost_usd or 0,
"input_tokens": task.input_tokens or 0,
"output_tokens": task.output_tokens or 0,
```

### Stats endpoint — add breakdown

```python
# In GET /stats response:

"total_spend_usd": total_spend,
"total_input_tokens": total_input_tokens,   # NEW
"total_output_tokens": total_output_tokens, # NEW
```

---

## 7. Frontend Changes

### 7a. TaskStore — new state fields and handlers

```typescript
// web/src/stores/taskStore.ts

export interface TaskState {
  // ... existing fields ...
  costUsd?: number;
  agentCostUsd?: number;       // NEW
  reviewCostUsd?: number;      // NEW
  inputTokens?: number;        // NEW
  outputTokens?: number;       // NEW
}

export interface PipelineState {
  // ... existing fields ...
  pipelineCost: {              // NEW
    totalCostUsd: number;
    plannerCostUsd: number;
    agentCostUsd: number;
    reviewCostUsd: number;
    budgetLimitUsd: number;
    budgetPct: number;
  };
  estimatedCostUsd: number;    // NEW
}

// New event handler in handleEvent switch:
case "pipeline:cost_update": {
  return {
    pipelineCost: {
      totalCostUsd: data.total_cost_usd as number,
      plannerCostUsd: data.planner_cost_usd as number,
      agentCostUsd: data.agent_cost_usd as number,
      reviewCostUsd: data.review_cost_usd as number,
      budgetLimitUsd: data.budget_limit_usd as number,
      budgetPct: data.budget_pct as number,
    },
    timeline: newTimeline,
  };
}

case "pipeline:cost_estimate": {
  return {
    estimatedCostUsd: data.estimated_cost_usd as number,
    timeline: newTimeline,
  };
}

case "pipeline:budget_exceeded": {
  sendNotification("Budget exceeded", data.message as string);
  return {
    phase: "cancelled" as PipelineState["phase"],
    timeline: newTimeline,
  };
}

// Modified task:cost_update handler:
case "task:cost_update": {
  const taskId = data.task_id as string;
  const existing = state.tasks[taskId];
  if (!existing) return { timeline: newTimeline };
  const category = data.category as string;
  return {
    tasks: {
      ...state.tasks,
      [taskId]: {
        ...existing,
        costUsd: (data.total_cost_usd as number) || (existing.costUsd || 0) + (data.cost_usd as number),
        agentCostUsd: category === "agent"
          ? (existing.agentCostUsd || 0) + (data.cost_usd as number)
          : existing.agentCostUsd,
        reviewCostUsd: category === "review"
          ? (existing.reviewCostUsd || 0) + (data.cost_usd as number)
          : existing.reviewCostUsd,
        inputTokens: (existing.inputTokens || 0) + (data.input_tokens as number || 0),
        outputTokens: (existing.outputTokens || 0) + (data.output_tokens as number || 0),
      },
    },
    timeline: newTimeline,
  };
}
```

### 7b. CompletionSummary — cost breakdown

```
┌─────────────────────────────────────────────────────────┐
│  All tasks completed successfully!                      │
│  All changes have been merged into the working branch.  │
│                                              [View PR]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────┐  │
│  │    5    │ │    5    │ │    0    │ │   12 files  │  │
│  │  Tasks  │ │ Passed  │ │ Failed  │ │   Changed   │  │
│  └─────────┘ └─────────┘ └─────────┘ └─────────────┘  │
│                                                         │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │ +342 / -89  │ │   $3.47     │ │  128K / 45K tok  │  │
│  │ Lines Chg'd │ │ Total Cost  │ │  In / Out Tokens │  │
│  └─────────────┘ └─────────────┘ └──────────────────┘  │
│                                                         │
│  Cost Breakdown                                         │
│  ┌─────────────────────────────────────────────┐        │
│  │  Planner   ██░░░░░░░░░░░░░░░░░░░  $0.15    │        │
│  │  Agents    ██████████████░░░░░░░  $2.82    │        │
│  │  Reviewers ███░░░░░░░░░░░░░░░░░░  $0.50    │        │
│  │  ─────────────────────────────────────────  │        │
│  │  Total                            $3.47    │        │
│  └─────────────────────────────────────────────┘        │
│                                                         │
│  Task Results                                    [Copy] │
│  ─────────────────────────────────────────────────────  │
│  ✓ #1  Add user model          +89/-12   3 files       │
│  ✓ #2  Create API routes       +156/-3   4 files       │
│  ...                                                    │
└─────────────────────────────────────────────────────────┘
```

### 7c. AgentCard — token counts in footer

```
┌─────────────────────────────────────┐
│  task-1                    [Working]│
│  Add user authentication model      │
│  ┌─────────────────────────────────┐│
│  │ > Reading src/models/user.py    ││
│  │ > Writing migration file        ││
│  │ > Running tests                 ││
│  └─────────────────────────────────┘│
│  ○ ○                               │
│  ──────────────────────────────────│
│  ⬤⬤  +89/-12  32K/12K tok  $0.45  │
│  (gates) (diff)  (tokens)  (cost)  │
└─────────────────────────────────────┘
```

Token display format: `{input_k}K/{output_k}K tok` (e.g., `32K/12K tok`).
Show only when tokens > 0.

### 7d. PlanPanel — estimated cost

```
┌─────────────────────────────────────────────────────┐
│  Plan Ready — 5 tasks                        [▼]    │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │ task-1  Add user model              [low]     │  │
│  │ task-2  Create API routes           [medium]  │  │
│  │ task-3  Add authentication          [high]    │  │
│  │ task-4  Write tests                 [medium]  │  │
│  │ task-5  Update documentation        [low]     │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  Estimated cost: ~$3.50                       │  │
│  │  (2 low × $0.25 + 2 med × $0.65 + 1 high ×  │  │
│  │   $1.20 + reviews ~$0.50)                     │  │
│  │  ⚠ Rough estimate — actual cost may vary     │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  [Execute Plan]                                     │
└─────────────────────────────────────────────────────┘
```

### 7e. Running Cost Indicator — pipeline header

During execution, show a cost ticker in the pipeline view header:

```
┌─────────────────────────────────────────────────────┐
│  Pipeline abc123 — Executing                        │
│  ┌─────────────────────────────────────────────┐    │
│  │  💰 $2.34 / $5.00                           │    │
│  │  ████████████████░░░░░░░░░░░░  46.8%        │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

When budget is unlimited (0):

```
│  💰 $2.34 spent                                     │
```

Implementation: new `CostIndicator` component reading from `useTaskStore(s => s.pipelineCost)`.

```
┌─────────────────────────────────────────────────────────┐
│  File: web/src/components/task/CostIndicator.tsx        │
│                                                         │
│  Props: none (reads from taskStore)                     │
│  Renders:                                               │
│  - If budgetLimitUsd > 0: progress bar + fraction       │
│  - If budgetLimitUsd == 0: simple "$X.XX spent" label   │
│  - Color: green < 50%, yellow 50-80%, red > 80%         │
│  - Visible only during "executing" phase                │
└─────────────────────────────────────────────────────────┘
```

---

## 8. Settings Additions

```python
# forge/config/settings.py

class ForgeSettings(BaseSettings):
    model_config = {"env_prefix": "FORGE_"}

    # ... existing settings ...

    # Budget & cost (NEW)
    budget_limit_usd: float = 0.0   # Global default. 0 = unlimited.

    # Cost estimation heuristic rates (NEW)
    # Per-task estimated cost by complexity tier and model.
    # Format: {model}_{complexity}_rate_usd
    cost_rate_opus_high: float = 1.50
    cost_rate_opus_medium: float = 0.80
    cost_rate_opus_low: float = 0.40
    cost_rate_sonnet_high: float = 0.45
    cost_rate_sonnet_medium: float = 0.25
    cost_rate_sonnet_low: float = 0.12
    cost_rate_haiku_high: float = 0.10
    cost_rate_haiku_medium: float = 0.05
    cost_rate_haiku_low: float = 0.02
    cost_rate_review: float = 0.08        # per review (any model)
    cost_rate_planner: float = 0.15       # flat rate for planning phase
```

Environment variable examples:
```bash
FORGE_BUDGET_LIMIT_USD=10.00        # $10 global budget
FORGE_COST_RATE_OPUS_HIGH=2.00      # Override if opus gets expensive
```

---

## 9. Cost Estimation Formula

Run **after** the planner produces the TaskGraph but **before** execution starts.

```python
# forge/core/cost_estimator.py — new module (replaces forge/api/services/cost_estimator.py)

from forge.config.settings import ForgeSettings
from forge.core.models import TaskDefinition


def estimate_pipeline_cost(
    tasks: list[TaskDefinition],
    model_strategy: str,
    settings: ForgeSettings,
) -> dict:
    """Estimate total pipeline cost from task list.

    Returns:
        {
            "estimated_cost_usd": float,
            "breakdown": {"planner": float, "agents": float, "reviews": float},
            "per_task": [{"task_id": str, "estimated_usd": float}, ...],
        }
    """
    from forge.core.model_router import select_model

    planner_cost = settings.cost_rate_planner
    agent_total = 0.0
    review_total = 0.0
    per_task = []

    for task in tasks:
        # Determine which model would be used
        model = select_model(model_strategy, "agent", task.complexity or "medium")
        complexity = task.complexity or "medium"

        # Look up rate
        rate_key = f"cost_rate_{_model_family(model)}_{complexity}"
        agent_rate = getattr(settings, rate_key, settings.cost_rate_sonnet_medium)

        # Each task gets agent cost + review cost
        task_agent = agent_rate
        task_review = settings.cost_rate_review

        # Retries: assume ~30% chance of 1 retry
        retry_factor = 1.3
        task_total = (task_agent + task_review) * retry_factor

        agent_total += task_agent * retry_factor
        review_total += task_review * retry_factor
        per_task.append({"task_id": task.id, "estimated_usd": round(task_total, 2)})

    total = planner_cost + agent_total + review_total

    return {
        "estimated_cost_usd": round(total, 2),
        "breakdown": {
            "planner": round(planner_cost, 2),
            "agents": round(agent_total, 2),
            "reviews": round(review_total, 2),
        },
        "per_task": per_task,
    }


def _model_family(model: str) -> str:
    """Map model name to family for rate lookup."""
    model_lower = model.lower()
    if "opus" in model_lower:
        return "opus"
    if "haiku" in model_lower:
        return "haiku"
    return "sonnet"  # default
```

### Historical Average Enhancement (Future)

Once enough pipelines have run, improve estimates using actual historical costs:

```python
# Future: query DB for average cost per complexity tier
# SELECT complexity, AVG(cost_usd) FROM tasks
#   WHERE state = 'done' GROUP BY complexity
# Use these as overrides when available (sample size >= 10)
```

---

## 10. Planner Cost Tracking

The planner runs before tasks exist. Track its cost on the PipelineRow directly.

```python
# forge/core/daemon.py — in _plan() or wherever generate_plan is called

result = await planner.generate_plan(user_input, context, on_message=on_message)

# After planner completes, record its cost
if hasattr(planner, '_last_sdk_result') and planner._last_sdk_result:
    planner_cost = planner._last_sdk_result.cost_usd
    await db.add_pipeline_cost(pipeline_id, planner_cost, category="planner")
    await self._emit("pipeline:cost_update", {
        "pipeline_id": pipeline_id,
        "total_cost_usd": planner_cost,
        "planner_cost_usd": planner_cost,
        "agent_cost_usd": 0,
        "review_cost_usd": 0,
        "budget_limit_usd": await db.get_pipeline_budget(pipeline_id),
        "budget_pct": ...,
    }, db=db, pipeline_id=pipeline_id)
```

Modify `ClaudePlannerLLM` to store the result:

```python
# forge/core/claude_planner.py

class ClaudePlannerLLM(PlannerLLM):
    def __init__(self, model: str = "sonnet", cwd: str | None = None) -> None:
        self._model = model
        self._cwd = cwd
        self._last_sdk_result: SdkResult | None = None    # NEW

    async def generate_plan(self, ...) -> str:
        # ... existing code ...
        result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
        self._last_sdk_result = SdkResult.from_result_message(result)   # NEW
        # ... rest of existing code ...
```

---

## 11. Review Cost Tracking

`gate2_llm_review()` currently discards the `ResultMessage` cost. Return it alongside the `GateResult`.

```python
# forge/review/llm_review.py

@dataclass
class ReviewCostInfo:
    """Cost data from an LLM review call."""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


async def gate2_llm_review(
    task_title: str,
    task_description: str,
    diff: str,
    worktree_path: str | None = None,
    model: str = "sonnet",
    prior_feedback: str | None = None,
    project_context: str = "",
) -> tuple[GateResult, ReviewCostInfo]:     # CHANGED return type
    # ... existing code ...

    for attempt in range(1, max_review_attempts + 1):
        result = await sdk_query(prompt=prompt, options=options)
        result_text = result.result if result and result.result else ""

        if result_text:
            sdk_info = SdkResult.from_result_message(result)
            cost_info = ReviewCostInfo(
                cost_usd=sdk_info.cost_usd,
                input_tokens=sdk_info.input_tokens,
                output_tokens=sdk_info.output_tokens,
            )
            return _parse_review_result(result_text), cost_info

    return (
        GateResult(passed=False, gate="gate2_llm_review", details="Empty after retries"),
        ReviewCostInfo(),
    )
```

Then in `daemon_review.py:_run_review()`, capture and persist the review cost:

```python
# After L2 gate:
gate_result, review_cost = await gate2_llm_review(...)

if review_cost.cost_usd > 0:
    await db.add_task_review_cost(
        task_id, review_cost.cost_usd,
        review_cost.input_tokens, review_cost.output_tokens,
    )
    await db.add_pipeline_cost(pipeline_id, review_cost.cost_usd, category="review")
    await self._emit("task:cost_update", {
        "task_id": task_id,
        "cost_usd": review_cost.cost_usd,
        "category": "review",
        "input_tokens": review_cost.input_tokens,
        "output_tokens": review_cost.output_tokens,
    }, db=db, pipeline_id=pipeline_id)
    # Also emit pipeline-level cost update
    total_cost = await db.get_pipeline_cost(pipeline_id)
    await self._emit("pipeline:cost_update", {
        "pipeline_id": pipeline_id,
        "total_cost_usd": total_cost,
        ...
    }, db=db, pipeline_id=pipeline_id)
```

---

## 12. Modified File Summary

| File | Change |
|------|--------|
| `forge/core/sdk_helpers.py` | Add `SdkResult` dataclass |
| `forge/agents/adapter.py` | Add `input_tokens`, `output_tokens` to `AgentResult`; populate from `SdkResult` |
| `forge/storage/db.py` | Add columns to `TaskRow` + `PipelineRow`; add cost/token DB methods |
| `forge/config/settings.py` | Add `budget_limit_usd`, cost rate settings |
| `forge/core/budget.py` | **NEW** — `check_budget()`, `BudgetExceededError` |
| `forge/core/cost_estimator.py` | **NEW** — `estimate_pipeline_cost()` |
| `forge/core/claude_planner.py` | Store `_last_sdk_result` for cost tracking |
| `forge/review/llm_review.py` | Add `ReviewCostInfo`; return `(GateResult, ReviewCostInfo)` |
| `forge/core/daemon_executor.py` | Budget check before agent; richer `task:cost_update`; emit `pipeline:cost_update` |
| `forge/core/daemon_review.py` | Capture review cost; emit events |
| `forge/core/daemon.py` | Budget check before planner; emit planner cost; emit cost estimate after planning |
| `forge/api/models/schemas.py` | Add `budget_limit_usd` to `CreateTaskRequest` |
| `forge/api/routes/tasks.py` | Pass budget to pipeline creation; add cost fields to responses |
| `web/src/stores/taskStore.ts` | Add `pipelineCost` state; handle new events |
| `web/src/components/task/CompletionSummary.tsx` | Cost breakdown section |
| `web/src/components/task/AgentCard.tsx` | Token counts in footer |
| `web/src/components/task/CostIndicator.tsx` | **NEW** — running cost progress bar |
| `web/src/app/tasks/view/page.tsx` | Cost estimate in PlanPanel; CostIndicator in header |

---

## 13. Edge Cases & Error Handling

| Scenario | Handling |
|----------|----------|
| `usage` dict is `None` on `ResultMessage` | `SdkResult.from_result_message()` defaults tokens to 0 |
| `usage` dict has unexpected keys | `.get()` with default 0; never crash |
| Budget exceeded mid-task | Current task completes (can't interrupt SDK call), budget checked after |
| Budget exceeded during review | Review completes, but remaining tasks are cancelled |
| Pipeline has no budget (0) | All checks skip; no progress bar shown |
| Negative cost from SDK | Clamp to 0 in `SdkResult` |
| SDK call fails (exception) | Cost = 0 for that call; no budget impact |
| Multiple retries of same task | Costs accumulate (agent + review per attempt) |
| Historical data unavailable | Fall back to heuristic rates in settings |
| Planner cost on pipeline restart | Previous costs are NOT reset (pipeline total is cumulative) |

---

## 14. Testing Strategy

1. **Unit tests for `SdkResult.from_result_message()`** — various `usage` dict shapes
2. **Unit tests for `check_budget()`** — unlimited, under budget, at limit, over
3. **Unit tests for `estimate_pipeline_cost()`** — various task mixes
4. **Integration test: budget cancellation** — mock SDK, verify events emitted
5. **Frontend tests: CostIndicator** — progress bar at 0%, 50%, 100%
6. **Frontend tests: event handlers** — `pipeline:cost_update`, `pipeline:budget_exceeded`
