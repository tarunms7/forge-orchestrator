# Adaptive Review Scaling — Design Spec
**Date:** 2026-03-30
**Status:** Draft → Approved for Implementation

---

## 1. Problem Statement

Forge's L2 reviewer currently passes the full git diff as a single block to a Claude agent.
For tasks with small diffs (< 400 lines) this works well. For large tasks (2 000–15 000+
lines across 30–50 files) it fails in three ways:

1. **Context saturation** — the full diff floods the reviewer's context window; attention
   dilutes quadratically (O(n²) transformer attention). Research shows reliable degradation
   starts at 50 K tokens even on models claiming 200 K windows.
2. **Lost-in-the-middle** — files positioned in the middle of a large diff receive 30 %+
   less attention than files at the beginning or end (Liu et al., TACL 2024 + Chroma 2025).
3. **Empty / timed-out responses** — the reviewer stalls or times out on overwhelmingly
   large contexts.

**Goal:** Scale the review system to handle arbitrarily large diffs without sacrificing
review quality; in fact improve quality for medium and large diffs while keeping small-diff
behavior and cost identical to today.

---

## 2. Research Foundations

| Finding | Source | Implication |
|---------|--------|-------------|
| Quality degrades before 50 K tokens, no exception across 18 frontier models | Chroma 2025 | Never send > ~8 K diff tokens in one shot |
| 8 K-ctx model + structured chunking beats GPT-4 128 K direct on 100 K docs | LLM×MapReduce | Chunking + synthesis > bigger context |
| Middle files receive 30 %+ less attention | Lost in the Middle, TACL 2024 | Sort high-risk files first in each chunk |
| Multi-pass (3–5x) aggregation improves F1 by 43 % over single pass | SWR-Bench 2025 | Worth optional extra pass for large reviews |
| PR-Agent threshold: 600 lines before compression kicks in | qodo docs | Set small/medium boundary near there |
| Entity-level review with dependency graph: 83.5 % High/Critical recall | inspect (Ataraxy) | Risk-ordering by graph in-degree is valuable |
| CodeRabbit, Greptile, Augment all use graph-based file prioritization | Industry survey | Dependency-aware ordering matters more than raw line count |

---

## 3. Adaptive Tier Strategy

The reviewer selects a tier based on **diff line count** (additions + deletions):

```
Tier 1  │ < 400 lines   │ Single pass, full diff         │ Current behaviour — no change
Tier 2  │ 400–2 000     │ Risk-enhanced single pass      │ Adds risk map to prompt header
Tier 3  │ > 2 000       │ Multi-chunk map-reduce         │ Triage → N chunk reviews → synthesis
```

Thresholds are **configurable** in `forge.toml` under `[review]`.
Default values are 400 and 2 000.

---

## 4. Tier 1 — Single Pass (unchanged)

No change to existing behaviour. The full diff + task description + sibling context is
passed to the existing `gate2_llm_review()` function unchanged.

All current paths (prior_feedback, delta_diff, allowed_files, streaming, cost tracking)
remain exactly as they are today.

---

## 5. Tier 2 — Risk-Enhanced Single Pass

### 5.1 Risk Scoring

A **pure-Python, zero-LLM** risk scorer computes a score for every changed file:

```
risk_score(file) =
    min(lines_changed, 500) × 0.4      # line count, capped at 500 to avoid one giant file dominating
  + is_new_file × 30                   # new code, no prior tests or established patterns
  + is_security_path × 25             # security-sensitive path detected (see below)
  + avg_hunk_size × 0.5               # complexity proxy: avg lines per changed hunk, clamped to [0, 20]
  + (10 if not is_test_file else 0)   # source files riskier than test files: +10
  + language_weight[ext]              # py=10, go=10, rs=10, ts=8, js=8, yaml=2, md=0, other=5
```

`avg_hunk_size` = (total lines in all hunks for this file) / (number of hunks). Clamped to [0, 20].
A large single hunk (one dense block of change) scores higher than many tiny hunks.

**Security path detection**: any path segment (split on `/`) matching any of
`auth`, `crypto`, `token`, `password`, `secret`, `key`, `perm`, `acl`, `role`, `jwt`,
`session`, `login`, `oauth`, `cred` adds +25 (additive, so two matches = +50, capped at +25).

**Output**: a list of `FileRiskScore` sorted descending by `score`. Tier labels:
`HIGH` (top 30 % by count), `MEDIUM` (next 40 %), `LOW` (bottom 30 %).

**Chunk risk label**: assigned as the tier label of the highest-scoring file in the chunk.

### 5.2 Risk Map Injection

The risk map is prepended to the review prompt **before** the diff:

```
## Review Priority Map
Files ordered by estimated risk. High-risk files deserve deepest attention.

HIGH (review thoroughly):
  ● codegraph/parser.py          (new file, 312 lines, Python)
  ● codegraph/__init__.py        (modified, 89 lines, security-adjacent)

MEDIUM (review carefully):
  ● tests/test_parser.py         (new file, 201 lines)

LOW (spot check):
  ● README.md                    (modified, 12 lines)
  ● .gitignore                   (modified, 4 lines)

Total: 618 lines across 5 files.
```

The rest of the prompt (diff, task description, prior feedback, etc.) is unchanged.
No extra LLM call. The agent uses the map as navigation guidance inside its existing turn budget.

---

## 6. Tier 3 — Multi-Chunk Map-Reduce

### 6.1 Overview

```
Full diff (> 2 000 lines)
        │
        ▼
 ┌─────────────┐
 │  TRIAGE     │  (pure-Python, no LLM)
 │  risk score │  → sorted, grouped chunks
 └─────────────┘
        │
   ┌────┴────┬─────────┐
   ▼         ▼         ▼
Chunk 1   Chunk 2  … Chunk N      (SEQUENTIAL — each independent, no cross-contamination)
   │         │         │
   └────┬────┴─────────┘
        │
        ▼
 ┌─────────────┐
 │  SYNTHESIS  │  (single LLM prompt completion — no tools)
 │  aggregate  │  → final PASS/FAIL/UNCERTAIN
 └─────────────┘
```

### 6.2 Chunking Algorithm

1. **Score all files** using the same risk scorer as Tier 2.
2. **Sort files descending** by risk score.
3. **Group into chunks** greedily: pack files into the current chunk until it would
   exceed `max_chunk_lines` (default 600); start a new chunk when it would overflow.
4. **Keep test files with their source file**: if `tests/test_foo.py` is in the diff
   and `foo.py` is also in the diff, they are placed in the same chunk even if this
   temporarily exceeds the line limit by up to 20 %.
5. **Minimum 2 files per chunk** where possible; singleton chunks only for files
   that individually exceed `max_chunk_lines`.
6. **Result**: ordered list of `DiffChunk(id, files, diff_text, line_count, risk_label)`.

### 6.3 Chunk Review

Each chunk is reviewed by a **separate sequential LLM call** (not parallel — avoids
flooding rate limits on large tasks). Chunks are **fully independent** — no chunk sees
another chunk's findings, preventing bias propagation. Cross-chunk reasoning happens
only in the synthesis pass.

Each chunk reviewer receives:
- Task title + description (always)
- Sibling context (always, same as today)
- **Interface context**: function/class signatures extracted from files NOT in this chunk
  but referenced by files in this chunk. "Referenced" means: parse `import X` and
  `from X import Y` lines in the chunk's files, map to file paths present in the diff,
  extract only `def `, `class `, `async def ` lines from those files' diff hunks.
  Max 200 lines total, prioritized by how many chunk files reference each external file.
- The chunk's own diff
- A note: "This is chunk N of M covering [file list]. Other files will be reviewed
  separately. Only report issues for these files."
- **Prior feedback on retry** (see Section 6.5)

Each chunk reviewer must respond in **structured JSON** (this is enforced by the system
prompt for chunk reviewers):

```json
{
  "verdict": "PASS" | "FAIL" | "UNCERTAIN",
  "confidence": 1-5,
  "issues": [
    {
      "severity": "HIGH" | "MEDIUM" | "LOW",
      "file": "path/to/file.py",
      "line_hint": "~45",
      "description": "Clear description of the problem"
    }
  ],
  "cross_chunk_concerns": [
    "Possible naming inconsistency between graph.py (not reviewed) and parser.py (reviewed here)"
  ],
  "summary": "One-sentence summary of findings"
}
```

If the chunk reviewer returns non-JSON text, a fallback parser extracts PASS/FAIL/UNCERTAIN
using the existing `_parse_review_result()` logic and wraps it in the JSON schema.

### 6.4 Synthesis

After all chunk reviews complete, a single synthesis LLM call receives:
- Task title + description
- A formatted summary of all chunk findings:
  ```
  Chunk 1 (HIGH risk: parser.py, __init__.py): FAIL (confidence 4/5)
    Issues:
    - [HIGH] parser.py ~45: Missing null check before tree traversal
    Cross-chunk concerns:
    - Interface mismatch possible with graph.py

  Chunk 2 (MEDIUM risk: test_parser.py): PASS (confidence 5/5)
    Issues: none
  ```
- All individual issue lists (deduplicated by file+line_hint)
- All cross-chunk concern strings

The synthesis agent produces a **final PASS/FAIL/UNCERTAIN** verdict + consolidated
feedback in human-readable form. It does NOT repeat per-chunk details unless relevant
to the final verdict.

**Synthesis verdict rules:**
- Any chunk FAIL with confidence ≥ 3 → overall FAIL
- Any chunk FAIL with confidence ≤ 2 → treat as UNCERTAIN (reviewer was not sure)
- Any chunk UNCERTAIN → overall UNCERTAIN
- All chunks PASS → overall PASS
- All chunks PASS but any chunk confidence ≤ 2 → overall UNCERTAIN

**Confidence scale** (used in chunk reviewer JSON):
- 5: Completely confident — code clearly correct or clearly broken
- 4: Confident — strong assessment with minor residual doubt
- 3: Reasonably confident — assessment is sound but some assumptions made
- 2: Low confidence — reviewer has significant uncertainty, may have missed context
- 1: Very low — reviewer could not properly evaluate (truncated diff, tool failures, etc.)

### 6.5 Retry Behaviour

On a full retry (agent fixed code, re-reviewing):
- Tier selection re-runs on the new diff (diff size may have changed)
- Prior feedback is injected at **both** chunk and synthesis level:
  - At chunk level: full `prior_feedback` string is included (truncated to 2 000 chars),
    with the note "A previous review flagged these issues — verify they are fixed in your
    chunk's files if any are mentioned."
  - At synthesis level: full prior_feedback + delta_diff (same as today)
- This ensures each chunk reviewer can verify prior issues were fixed in its files,
  while synthesis sees the complete picture

On a **transient chunk failure** (SDK error or timeout on one chunk):
- Retry that single chunk up to 2 times
- If chunk still fails after 2 attempts: do NOT mark as UNCERTAIN (that would conflate
  SDK failure with reviewer uncertainty). Instead: escalate to human (`needs_human=True`)
  with message identifying which chunk failed and which files it covered.
  Do NOT proceed to synthesis with a failed chunk — synthesis result would be incomplete.

---

## 7. New Events and Callback Architecture

### 7.1 on_review_event Callback

`gate2_llm_review()` gains a new optional parameter:

```python
async def gate2_llm_review(
    ...,
    on_message: Callable[[Any], Awaitable[None]] | None = None,      # existing
    on_review_event: Callable[[str, dict], Awaitable[None]] | None = None,  # NEW
) -> tuple[GateResult, ReviewCostInfo]:
```

`on_review_event(event_name: str, payload: dict)` is called by the review orchestration
code to signal progress. The caller (`_run_review()` in `daemon_review.py`) provides a
callback that translates these into WebSocket events.

This keeps `llm_review.py` free of WebSocket/DB dependencies.

### 7.2 New WebSocket Events

Three new WebSocket events added to the existing event schema (existing events unchanged):

| Event | Payload | When |
|-------|---------|------|
| `review:strategy_selected` | `{task_id, strategy: "tier1/tier2/tier3", diff_lines, chunk_count?, risk_summary?}` | Once, before review starts |
| `review:chunk_started` | `{task_id, chunk_index, chunk_total, files: [...], risk_label}` | At start of each Tier 3 chunk |
| `review:chunk_complete` | `{task_id, chunk_index, chunk_total, verdict, issue_count, confidence}` | At end of each Tier 3 chunk |
| `review:synthesis_started` | `{task_id, chunk_count, total_issues}` | Before synthesis pass |

Existing `review:llm_output` gains an optional `chunk_index: int | None = None` field
(null for Tier 1/2, 1-based for Tier 3). This is additive — existing consumers that
ignore unknown payload fields continue to work unchanged.

---

## 8. TUI Changes

### 8.1 Review Phase Header (chat_thread.py)

The existing review streaming card is extended with phase breadcrumbs:

**Tier 1/2 (no change to existing display):**
```
━━━ Reviewing ━━━
[streaming reviewer output...]
```

**Tier 2 (risk map added as collapsed section before streaming):**
```
━━━ Reviewing (618 lines · Risk-Enhanced) ━━━
  HIGH: parser.py, __init__.py
  MEDIUM: test_parser.py
  LOW: README.md
[streaming reviewer output...]
```

**Tier 3 — Triage complete, chunk N starting:**
```
━━━ Reviewing (3 247 lines · Chunked) ━━━
  ✓ Chunk 1/4 · parser.py, __init__.py     PASS
  ⟳ Chunk 2/4 · graph.py, ranker.py        reviewing...
  ○ Chunk 3/4 · renderer.py, cache.py
  ○ Chunk 4/4 · tests/
[streaming output for current chunk...]
```

**Tier 3 — After synthesis:**
```
━━━ Review Complete (Chunked · 4 chunks) ━━━
  ✓ Chunk 1/4 · parser.py, __init__.py     PASS
  ✗ Chunk 2/4 · graph.py, ranker.py        FAIL (1 issue)
  ✓ Chunk 3/4 · renderer.py, cache.py      PASS
  ✓ Chunk 4/4 · tests/                     PASS
──────────────────────────────────────────────
  FAIL: Missing null check in graph.py ~112
```

### 8.2 Task Panel Subtitle

The existing task panel subtitle already shows review status. It gains a new token:

- `reviewing` → unchanged
- `reviewing (chunk 2/4)` → Tier 3 chunk progress

This is rendered in the existing `_render_review_status()` area of the TUI app.

### 8.3 No New Screens

No new screens, no new panels. All visual changes are within existing
`ChatThread` / review card area via the existing `review:llm_output` event stream plus
the three new events listed above.

---

## 9. Configuration

New optional keys in `forge.toml` under `[review]`:

```toml
[review]
# Existing keys (unchanged)
enabled = true
max_retries = 3

# New keys (all optional — defaults shown)
adaptive_review = true           # false = always use Tier 1 (current behaviour)
medium_diff_threshold = 400      # lines; below = Tier 1, above = Tier 2
large_diff_threshold = 2000      # lines; above = Tier 3
max_chunk_lines = 600            # max lines per chunk in Tier 3
```

Schema is defined in `forge/config/project_config.py` (extends `ReviewSettings`).

---

## 10. Data Structures

### 10.1 New: `DiffChunk` (forge/review/strategy.py)

```python
@dataclass
class DiffChunk:
    index: int                    # 1-based chunk index
    total: int                    # total number of chunks
    files: list[str]              # file paths in this chunk
    diff_text: str                # full diff text for these files
    line_count: int               # lines added + removed
    risk_label: str               # "HIGH", "MEDIUM", "LOW"
    risk_scores: dict[str, float] # file → score
```

### 10.2 New: `FileRiskScore` (forge/review/strategy.py)

```python
@dataclass
class FileRiskScore:
    path: str
    score: float
    tier: str        # "HIGH", "MEDIUM", "LOW"
    is_new: bool
    is_test: bool
    is_security: bool
    lines_changed: int
    language: str
```

### 10.3 New: `ChunkReviewResult` (forge/review/synthesizer.py)

```python
@dataclass
class ChunkReviewResult:
    chunk_index: int
    verdict: str                  # "PASS", "FAIL", "UNCERTAIN"
    confidence: int               # 1–5 (see confidence scale in Section 6.4)
    issues: list[dict]            # [{severity, file, line_hint, description}]
    cross_chunk_concerns: list[str]
    summary: str
    cost_info: ReviewCostInfo
    raw_text: str                 # full reviewer output for display
    timed_out: bool = False       # True if SDK/timeout failure (not a review verdict)
```

### 10.4 Extended: `ReviewStrategy` enum (forge/review/strategy.py)

```python
class ReviewStrategy(str, Enum):
    TIER1 = "tier1"    # single pass, full diff
    TIER2 = "tier2"    # risk-enhanced single pass
    TIER3 = "tier3"    # multi-chunk map-reduce
```

### 10.5 Extended: `GateResult` (forge/review/pipeline.py)

New optional fields (backward-compatible, all default None/False):

```python
@dataclass
class GateResult:
    # existing fields unchanged
    passed: bool
    gate: str
    details: str
    retriable: bool = False
    infra_error: bool = False
    needs_human: bool = False
    # new fields
    review_strategy: str | None = None   # "tier1", "tier2", "tier3"
    chunk_count: int | None = None       # Tier 3 only
    chunk_verdicts: list[str] | None = None  # e.g. ["PASS","FAIL","PASS"]
```

---

## 11. Module Layout

```
forge/review/
├── __init__.py          (unchanged)
├── pipeline.py          (extend GateResult — backward-compatible)
├── llm_review.py        (add strategy dispatcher; Tier 2 prompt enhancement)
├── auto_check.py        (unchanged)
├── strategy.py          ← NEW: DiffChunk, FileRiskScore, risk scorer, chunker
└── synthesizer.py       ← NEW: chunk aggregation, synthesis LLM call
```

No new directories.

**`ReviewCostInfo` is moved from `llm_review.py` → `pipeline.py`** to avoid a circular
import (`llm_review.py` calls `synthesizer.py`, `synthesizer.py` needs `ReviewCostInfo`
from `llm_review.py` — circular). Moving it to `pipeline.py` (the shared types module)
breaks the cycle. `llm_review.py` re-exports it for backward compat:
`from forge.review.pipeline import ReviewCostInfo  # noqa: F401`.

Import graph for the new modules:
- `strategy.py` imports: stdlib only (`re`, `dataclasses`, `enum`, `pathlib`)
- `synthesizer.py` imports: `strategy.py` (DiffChunk, FileRiskScore),
  `pipeline.py` (GateResult, ReviewCostInfo),
  `forge/core/sdk_helpers.py` (sdk_query)
- `llm_review.py` imports: `synthesizer.py` (run_chunked_review),
  `strategy.py` (score_files, build_chunks, select_strategy),
  `pipeline.py` (GateResult, ReviewCostInfo)

---

## 12. Error Handling and Fallback Chain

```
Tier 3 attempt
    │
    ├─ Chunking fails (exception) → fall back to Tier 2 (log warning)
    │
    ├─ One chunk SDK error → retry chunk up to 2×
    │   ├─ Retries succeed → continue
    │   └─ Retries fail → mark chunk UNCERTAIN, continue to synthesis
    │
    ├─ Synthesis fails → retry synthesis up to 2×
    │   ├─ Retries succeed → use result
    │   └─ Retries fail → needs_human=True (escalate to human)
    │
    └─ All chunks PASS + synthesis fails → conservative fallback: UNCERTAIN (not auto-PASS)

Tier 2 attempt
    │
    └─ Risk scoring fails (exception) → fall back to Tier 1 (log warning)
        (risk scoring is pure Python, should never fail in practice)
```

**Key principle**: Never silently auto-pass due to a framework failure.
All fallback paths either produce a verdict or escalate to human.

---

## 13. Testing Strategy

### New test files:
- `forge/review/strategy_test.py` — risk scoring (all 6 signals), chunking algorithm
  (boundary conditions, test-source pairing, max_chunk_lines overflow), DiffChunk
  construction, ReviewStrategy selection
- `forge/review/synthesizer_test.py` — synthesis logic: all-PASS, any-FAIL, UNCERTAIN
  cases, confidence-weighting, cross-chunk deduplication

### Modified test files:
- `forge/review/llm_review_test.py` — add Tier 2 prompt structure test (risk map
  is present in prompt when diff > threshold), strategy dispatcher tests
- `forge/config/project_config_test.py` — new `ReviewSettings` fields

### Integration test:
- `forge/core/daemon_review_test.py` — mock a Tier 3 review flow end-to-end;
  verify events emitted in correct order, cost accumulation across chunks

---

## 14. Non-Regression Guarantees

1. **Tier 1 path**: Zero code change to the hot path for diffs < 400 lines. The diff is
   passed to `gate2_llm_review()` exactly as today. No new latency, no new cost.
2. **`adaptive_review = false`**: Disabling the feature in `forge.toml` bypasses all
   new code and uses the old single-pass path for any diff size.
3. **Event schema**: Three new events added but zero existing events modified. TUI falls
   back gracefully if new events are not present.
4. **GateResult fields**: New fields are optional with safe defaults; any consumer
   that ignores them continues to work unchanged.
5. **`_parse_review_result()`**: Unchanged. Chunk reviewer JSON is a wrapper on top;
   the fallback path calls this function directly.

---

## 15. Scope Explicitly Out

The following are intentionally NOT included in this spec (can be separate features):

- **Embedding-based codebase indexing** (Greptile / Augment style) — requires vector
  store infra; delivers value for cross-PR context, not just within-PR review. Future.
- **Parallel chunk reviews** — sequential is safer for rate limits; can be enabled
  as a `parallel_chunks = true` config option in a future iteration once sequential
  is proven.
- **Static analyzer (ruff/eslint) output injection into reviewer context** — valid
  enhancement but separate from the diff scaling problem. Out of scope.
- **Automatic PR splitting** (Graphite-style) — this is a planning/execution concern,
  not a review concern. Out of scope.
- **Per-file turn budget allocation** — currently all reviewers share `max_turns=75`.
  Could be tuned per chunk size in a future iteration.

---

## 16. Happy / Unhappy Flow Summary

### Happy flow — small task (Tier 1)
1. Agent commits code, `_run_review()` called
2. `select_review_strategy(diff)` returns `TIER1` (< 400 lines)
3. `gate2_llm_review()` called exactly as today
4. Reviewer streams output, returns PASS
5. Task proceeds to merge

### Happy flow — medium task (Tier 2)
1. Agent commits code, `_run_review()` called
2. Strategy → TIER2 (400–2000 lines)
3. `score_files()` runs in ~1ms, produces risk map
4. `build_risk_enhanced_prompt()` prepends risk map to existing prompt
5. Event `review:strategy_selected` emitted → TUI shows risk map header
6. Single review agent runs (same as today, just better-focused)
7. Returns PASS/FAIL/UNCERTAIN → normal flow

### Happy flow — large task (Tier 3)
1. Agent commits code, `_run_review()` called
2. Strategy → TIER3 (> 2000 lines)
3. Chunker groups 47 files into 6 chunks (avg ~400 lines/chunk)
4. Event `review:strategy_selected {chunk_count: 6}` → TUI shows chunk grid
5. Chunk 1 reviewed: PASS → `review:chunk_complete` → TUI ✓ Chunk 1
6. Chunk 2 reviewed: FAIL (1 issue) → TUI ✗ Chunk 2
7. … chunks 3–6 reviewed …
8. `review:synthesis_started` emitted
9. Synthesis: overall FAIL, consolidated feedback
10. Normal FAIL flow: store prior_diff, retry agent

### Unhappy flow — one chunk SDK timeout
1. Chunk 3 of 6 times out after 10 min
2. Retry chunk 3 (attempt 2): succeeds → continue
3. Normal flow resumes

### Unhappy flow — chunk 3 permanently fails
1. Chunk 3 both attempts time out
2. ChunkReviewResult verdict = "UNCERTAIN", low confidence
3. Synthesis sees UNCERTAIN chunk → `needs_human=True`
4. Human asked: "Chunk 3/6 failed to complete review for parser.py, graph.py.
   Do you want to: [1] Retry chunk [2] Pass this task [3] Fail and retry agent"

### Unhappy flow — synthesis fails
1. All 6 chunks complete successfully
2. Synthesis LLM call fails (both attempts)
3. Conservative fallback: all-PASS chunks → UNCERTAIN (not auto-PASS)
4. `needs_human=True` → human decision

### Unhappy flow — total diff > 15 000 lines
1. Chunker produces 25 chunks at max_chunk_lines=600
2. Reviews run sequentially (approx 25 × 2–5 min = could be 1–2 hours)
3. This is expected behaviour; the user should split such tasks in the plan
4. Future: warn in plan approval if estimated review time > threshold

---

## 17. Implementation Task Breakdown

Seven independent/sequential implementation tasks:

| # | Task | Files | Dependencies |
|---|------|-------|-------------|
| T1 | `forge/review/strategy.py` — risk scorer, chunker, DiffChunk, ReviewStrategy enum | new | none |
| T2 | `forge/review/synthesizer.py` — ChunkReviewResult, synthesis LLM call | new | T1 |
| T3 | Extend `GateResult` in `pipeline.py` | existing | none |
| T4 | Extend `gate2_llm_review()` — strategy dispatcher, Tier 2 risk prompt, Tier 3 orchestration | existing | T1, T2, T3 |
| T5 | Extend `daemon_review.py` — emit new events, handle chunk progress in `_run_review` | existing | T4 |
| T6 | TUI — `chat_thread.py` chunk display, `app.py` new event handlers | existing | T5 |
| T7 | Tests — `strategy_test.py`, `synthesizer_test.py`, extend existing tests | new+existing | T1–T6 |

T1 and T3 have no dependencies — can be started in parallel.
T2 depends on T1 (uses DiffChunk, FileRiskScore).
T4 depends on T1, T2, T3.
T5 depends on T4.
T6 depends on T5.
T7 depends on T1–T6.
