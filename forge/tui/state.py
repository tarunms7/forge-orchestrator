"""Reactive state container for the TUI.

Holds all data the UI needs: pipeline phase, tasks, agent output, costs.
Widgets read from this; EventBus writes to this via apply_event().
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

logger = logging.getLogger("forge.tui.state")


_GATE_LABELS = {
    "gate0_build": "\U0001f528 Build",
    "gate1_lint": "\U0001f4cf Lint",
    "gate1_5_test": "\U0001f9ea Tests",
    "gate2_llm_review": "\U0001f916 LLM Review",
}


class TuiState:
    """Single source of truth for TUI data."""

    def __init__(self, max_output_lines: int = 1000) -> None:
        self._max_output_lines = max_output_lines
        self._change_callbacks: list[Callable[[str], None]] = []

        self.pipeline_id: str | None = None
        self.phase: str = "idle"
        self.total_cost_usd: float = 0.0
        self.elapsed_seconds: float = 0.0

        self.tasks: dict[str, dict] = {}
        self.task_order: list[str] = []
        self.selected_task_id: str | None = None

        self.agent_output: dict[str, list[str]] = defaultdict(list)
        self.planner_output: list[str] = []
        self.error: str | None = None
        self._pending_state_updates: dict[str, dict] = {}

        self.pending_questions: dict[str, dict] = {}  # task_id → question data
        self.review_gates: dict[
            str, dict[str, dict]
        ] = {}  # task_id → gate_name → {status, details}
        self.pr_url: str | None = None
        self.question_history: dict[str, list[dict]] = {}  # task_id → [Q&A pairs]
        self.review_output: dict[str, list[str]] = defaultdict(
            list
        )  # task_id → streaming LLM review lines
        self.unified_log: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.streaming_task_ids: set[str] = set()  # tasks currently emitting streaming output
        self.pipeline_branch: str = ""  # branch where task work is merged
        self.base_branch: str = "main"  # branch the pipeline was created from (PR target)

        # Feature 2/3/5: contracts, cost, budget, preflight, followup
        self.contracts_output: list[str] = []
        self.contracts_ready: bool = False
        self.contracts_failed: str | None = None
        self.cost_estimate: dict | None = None
        self.budget_exceeded: bool = False
        self.preflight_error: str | None = None
        self.followup_tasks: dict[str, dict] = {}
        self.followup_output: dict[str, list[str]] = defaultdict(list)

        # Error history: maps task_id → list of previous error messages (across retries)
        self.error_history: dict[str, list[str]] = defaultdict(list)

        self.planning_stage: str = ""
        self.last_auto_decided: dict | None = None

        # Task diffs: computed by the daemon from the worktree, stored here
        # so the TUI can display them immediately without running git commands.
        self.task_diffs: dict[str, str] = {}  # task_id → diff text

        # Integration health checks
        self.integration_baseline: dict | None = None  # {"status": ..., "exit_code": ...}
        self.integration_degraded: bool = False  # True if user chose "ignore" on a failure
        self.integration_checks: dict[str, dict] = {}  # task_id → check result
        self.integration_prompt: dict | None = None  # pending user decision
        self.integration_final_gate: dict | None = None

        # Merge progress substatus (task_id → step label)
        self.merge_substatus: dict[str, str] = {}

        # Multi-repo state
        self.repos: list[dict] = []
        self.per_repo_pr_urls: dict[str, str] = {}
        self.per_repo_merge_status: dict[str, str] = {}

    def on_change(self, callback: Callable[[str], None]) -> None:
        self._change_callbacks.append(callback)

    def remove_change_callback(self, callback: Callable[[str], None]) -> None:
        try:
            self._change_callbacks.remove(callback)
        except ValueError:
            pass

    def _notify(self, field: str) -> None:
        for cb in self._change_callbacks:
            try:
                cb(field)
            except Exception:
                logger.exception("Change callback error for field %r", field)

    def apply_event(self, event_type: str, data: dict) -> None:
        handler = self._EVENT_MAP.get(event_type)
        if handler:
            handler(self, data)

    def _on_phase_changed(self, data: dict) -> None:
        self.phase = data.get("phase", self.phase)
        self._notify("phase")

    def _on_plan_ready(self, data: dict) -> None:
        self.repos = data.get("repos", [])
        self.tasks.clear()
        self.task_order.clear()
        for t in data.get("tasks", []):
            tid = t["id"]
            self.tasks[tid] = {
                "id": tid,
                "title": t.get("title", ""),
                "description": t.get("description", ""),
                "files": t.get("files", []),
                "depends_on": t.get("depends_on", []),
                "complexity": t.get("complexity", "medium"),
                "state": "todo",
                "agent_cost": 0.0,
                "error": None,
                "repo": t.get("repo"),
            }
            self.task_order.append(tid)
        if self.task_order:
            # Always reset — IDs may change after daemon remaps them
            self.selected_task_id = self.task_order[0]
        # Apply any buffered state updates that arrived before plan_ready
        for tid, update_data in self._pending_state_updates.items():
            if tid in self.tasks:
                self.tasks[tid]["state"] = update_data.get("state", self.tasks[tid]["state"])
                if "error" in update_data:
                    self.tasks[tid]["error"] = update_data["error"]
        self._pending_state_updates.clear()
        self._notify("tasks")

    def _on_task_state_changed(self, data: dict) -> None:
        tid = data.get("task_id")
        if not tid:
            return
        new_state = data.get("state", "")
        if new_state in ("done", "error"):
            self.streaming_task_ids.discard(tid)
        # Clear merge substatus when task leaves MERGING
        if new_state != "merging":
            self.merge_substatus.pop(tid, None)
        if tid in self.tasks:
            self.tasks[tid]["state"] = data.get("state", self.tasks[tid]["state"])
            if "error" in data:
                self.tasks[tid]["error"] = data["error"]
            # Track error history: when a task transitions to 'error', preserve the message
            if new_state == "error" and "error" in data and data["error"]:
                self.error_history[tid].append(data["error"])
            self._notify("tasks")
        else:
            # Buffer for when task is added via plan_ready
            self._pending_state_updates[tid] = data

    def _on_merge_progress(self, data: dict) -> None:
        tid = data.get("task_id")
        step = data.get("step", "")
        if tid and step:
            self.merge_substatus[tid] = step
            self._notify("tasks")  # triggers task list re-render

    def _on_agent_output(self, data: dict) -> None:
        tid = data.get("task_id", "")
        line = data.get("line", "")
        lines = self.agent_output[tid]
        lines.append(line)
        if len(lines) > self._max_output_lines:
            del lines[: len(lines) - self._max_output_lines]
        # Unified log
        ulog = self.unified_log[tid]
        ulog.append(("agent", line))
        if len(ulog) > self._max_output_lines:
            del ulog[: len(ulog) - self._max_output_lines]
        if tid:
            self.streaming_task_ids.add(tid)
        self._notify("agent_output")

    def _on_planner_output(self, data: dict) -> None:
        self.planner_output.append(data.get("line", ""))
        if len(self.planner_output) > self._max_output_lines:
            del self.planner_output[: len(self.planner_output) - self._max_output_lines]
        self._notify("planner_output")

    def _on_cost_update(self, data: dict) -> None:
        if "total_cost_usd" in data:
            self.total_cost_usd = data["total_cost_usd"]
        self._notify("cost")

    def _on_task_cost_update(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            if "agent_cost" in data:
                self.tasks[tid]["agent_cost"] = data["agent_cost"]
            self._notify("tasks")

    def _on_review_update(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["review"] = data
            self._notify("tasks")

    def _on_merge_result(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["merge_result"] = data
            self._notify("tasks")

    def _on_awaiting_approval(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["state"] = "awaiting_approval"
            self._notify("tasks")

    def _on_review_diff(self, data: dict) -> None:
        """Store the diff computed from the task's worktree."""
        tid = data.get("task_id")
        diff = data.get("diff", "")
        if tid:
            self.task_diffs[tid] = diff
            self._notify("task_diffs")

    def _on_pipeline_error(self, data: dict) -> None:
        self.error = data.get("error", "Unknown error")
        self._notify("error")

    def _on_branch_resolved(self, data: dict) -> None:
        branch = data.get("branch", "")
        if branch:
            self.pipeline_branch = branch

    def _on_task_question(self, data: dict) -> None:
        task_id = data.get("task_id")
        if task_id and task_id in self.tasks:
            self.tasks[task_id]["state"] = "awaiting_input"
            self.pending_questions[task_id] = data.get("question", {})
            self._notify("tasks")

    def _on_task_answer(self, data: dict) -> None:
        task_id = data.get("task_id")
        if task_id:
            q = self.pending_questions.pop(task_id, None)
            if q:
                history = self.question_history.setdefault(task_id, [])
                history.append({"question": q, "answer": data.get("answer")})
            self._notify("tasks")

    def _on_task_resumed(self, data: dict) -> None:
        task_id = data.get("task_id")
        if task_id and task_id in self.tasks:
            self.tasks[task_id]["state"] = "in_progress"
            self._notify("tasks")

    def _on_task_interjection(self, data: dict) -> None:
        """Human sent a message to a running agent."""
        task_id = data.get("task_id")
        if task_id and task_id in self.tasks:
            history = self.tasks[task_id].setdefault("interjections", [])
            history.append(data.get("message", ""))
            self._notify("tasks")

    def _on_task_auto_decided(self, data: dict) -> None:
        task_id = data.get("task_id")
        if task_id:
            q = self.pending_questions.pop(task_id, None)
            if q:
                history = self.question_history.setdefault(task_id, [])
                history.append(
                    {"question": q, "answer": f"[auto: {data.get('reason', 'unknown')}]"}
                )
            self._notify("tasks")
            self.last_auto_decided = {"task_id": task_id, "reason": data.get("reason", "timeout")}
            self._notify("auto_decided")

    def _on_review_gate_started(self, data: dict) -> None:
        task_id = data.get("task_id")
        gate = data.get("gate")
        if task_id and gate:
            self.review_gates.setdefault(task_id, {})[gate] = {"status": "running"}
            self._notify("tasks")

    def _on_review_gate_passed(self, data: dict) -> None:
        task_id = data.get("task_id")
        gate = data.get("gate")
        if task_id and gate:
            self.review_gates.setdefault(task_id, {})[gate] = {
                "status": "passed",
                "details": data.get("details"),
            }
            # Unified log
            gate_label = _GATE_LABELS.get(gate, gate)
            self.unified_log[task_id].append(
                ("gate", f"{gate_label}: \u2713 {data.get('details', 'passed')}")
            )
            self._notify("tasks")

    def _on_review_gate_failed(self, data: dict) -> None:
        task_id = data.get("task_id")
        gate = data.get("gate")
        if task_id and gate:
            self.review_gates.setdefault(task_id, {})[gate] = {
                "status": "failed",
                "details": data.get("details"),
            }
            # Unified log
            gate_label = _GATE_LABELS.get(gate, gate)
            self.unified_log[task_id].append(
                ("gate", f"{gate_label}: \u2717 {data.get('details', 'failed')}")
            )
            self._notify("tasks")

    def _on_review_llm_output(self, data: dict) -> None:
        task_id = data.get("task_id")
        line = data.get("line", "")
        if task_id:
            lines = self.review_output[task_id]
            lines.append(line)
            if len(lines) > self._max_output_lines:
                del lines[: len(lines) - self._max_output_lines]
            # Unified log
            ulog = self.unified_log[task_id]
            ulog.append(("review", line))
            if len(ulog) > self._max_output_lines:
                del ulog[: len(ulog) - self._max_output_lines]
            self.streaming_task_ids.add(task_id)
            self._notify("review_output")

    def _on_review_llm_feedback(self, data: dict) -> None:
        task_id = data.get("task_id")
        if task_id:
            gates = self.review_gates.setdefault(task_id, {})
            gates["gate2_llm_review"] = {"status": "passed", "details": data.get("feedback")}
            self._notify("tasks")

    def _on_cost_estimate(self, data: dict) -> None:
        self.cost_estimate = data
        self._notify("cost_estimate")

    def _on_budget_exceeded(self, data: dict) -> None:
        self.budget_exceeded = True
        self._notify("budget_exceeded")

    def _on_contracts_output(self, data: dict) -> None:
        self.contracts_output.append(data.get("line", ""))
        if len(self.contracts_output) > self._max_output_lines:
            del self.contracts_output[: len(self.contracts_output) - self._max_output_lines]
        self._notify("contracts_output")

    def _on_contracts_ready(self, data: dict) -> None:
        self.contracts_ready = True
        self._notify("contracts_ready")

    def _on_contracts_failed(self, data: dict) -> None:
        self.contracts_failed = data.get("error", "Unknown")
        self._notify("contracts_failed")

    def _on_files_changed(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.tasks:
            self.tasks[tid]["files_changed"] = data.get("files", [])
            self._notify("tasks")

    def _on_cancelled(self, data: dict) -> None:
        self.phase = "cancelled"
        self._notify("phase")

    def _on_paused(self, data: dict) -> None:
        self.phase = "paused"
        self._notify("phase")

    def _on_pipeline_resumed(self, data: dict) -> None:
        self.phase = "executing"
        self._notify("phase")

    def _on_restarted(self, data: dict) -> None:
        self.tasks.clear()
        self.task_order.clear()
        self.agent_output.clear()
        self.unified_log.clear()
        self.planner_output.clear()
        self.contracts_output.clear()
        self.contracts_ready = False
        self.contracts_failed = None
        self.cost_estimate = None
        self.budget_exceeded = False
        self.preflight_error = None
        self.followup_tasks.clear()
        self.followup_output.clear()
        self.error_history.clear()
        self.error = None
        self.total_cost_usd = 0.0
        self.planning_stage = ""
        self.phase = "planning"
        self._notify("phase")

    def _on_worktrees_cleaned(self, data: dict) -> None:
        logger.debug("Worktrees cleaned event received")

    def _on_preflight_failed(self, data: dict) -> None:
        self.preflight_error = data.get("error", "Unknown")
        self._notify("preflight_error")

    def _on_followup_started(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid:
            self.followup_tasks[tid] = {"task_id": tid, "state": "started", "error": None}
            self._notify("followup_tasks")

    def _on_followup_completed(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.followup_tasks:
            self.followup_tasks[tid]["state"] = "completed"
            self._notify("followup_tasks")

    def _on_followup_error(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid and tid in self.followup_tasks:
            self.followup_tasks[tid]["state"] = "error"
            self.followup_tasks[tid]["error"] = data.get("error") or "Unknown error"
            self._notify("followup_tasks")

    def _on_followup_output(self, data: dict) -> None:
        tid = data.get("task_id")
        if tid:
            self.followup_output[tid].append(data.get("line", ""))
            self._notify("followup_output")

    def _on_slot_acquired(self, data: dict) -> None:
        logger.debug("Slot acquired event received")

    def _on_slot_released(self, data: dict) -> None:
        logger.debug("Slot released event received")

    def _on_slot_queued(self, data: dict) -> None:
        logger.debug("Slot queued event received")

    def _on_planning_question(self, data: dict) -> None:
        """Architect has a question during planning."""
        self.pending_questions["__planning__"] = data.get("question", {})
        self._notify("planning")

    def _on_planning_answer(self, data: dict) -> None:
        """Planning question was answered."""
        self.pending_questions.pop("__planning__", None)
        self._notify("planning")

    def _handle_planning_output(self, stage: str, data: dict) -> None:
        """Handle streaming output from the planning stage.

        Kept for backward compatibility — both unified planner (via planner:output)
        and any legacy per-stage events route through here.
        """
        if self.planning_stage != stage:
            self.planning_stage = stage
            if stage != "Planner":
                # Only insert separators for legacy per-stage events
                self.planner_output.append(f"─── {stage} ───")
            self._notify("planning_stage")
        line = data.get("line", "")
        self.planner_output.append(line)
        if len(self.planner_output) > self._max_output_lines:
            del self.planner_output[: len(self.planner_output) - self._max_output_lines]
        self._notify("planner_output")

    # ── Integration health check handlers ──────────────────────────

    def _on_integration_baseline_started(self, data: dict) -> None:
        self.integration_baseline = {"status": "running"}
        self._notify("integration")

    def _on_integration_baseline_result(self, data: dict) -> None:
        self.integration_baseline = data
        self._notify("integration")

    def _on_integration_baseline_failed_prompt(self, data: dict) -> None:
        self.integration_prompt = {
            "type": "baseline",
            "exit_code": data.get("exit_code"),
            "stderr": data.get("stderr", ""),
            "options": ["ignore_and_continue", "cancel_pipeline"],
        }
        self._notify("integration")

    def _on_integration_baseline_response(self, data: dict) -> None:
        self.integration_prompt = None
        if data.get("action") == "ignore_and_continue":
            self.integration_degraded = True
        self._notify("integration")

    def _on_integration_check_started(self, data: dict) -> None:
        tid = data.get("task_id", "")
        self.integration_checks[tid] = {"status": "running"}
        self._notify("integration")

    def _on_integration_check_result(self, data: dict) -> None:
        tid = data.get("task_id", "")
        self.integration_checks[tid] = data
        if data.get("action") == "ignore_and_continue":
            self.integration_degraded = True
        self._notify("integration")

    def _on_integration_check_prompt(self, data: dict) -> None:
        self.integration_prompt = {
            "type": "post_merge",
            "task_id": data.get("task_id"),
            "cmd": data.get("cmd"),
            "stderr": data.get("stderr", ""),
            "exit_code": data.get("exit_code"),
            "is_regression": data.get("is_regression", False),
            "baseline_was_red": data.get("baseline_was_red", False),
            "options": data.get("options", ["ignore_and_continue", "stop_pipeline"]),
            "phase": data.get("phase", "post_merge"),
        }
        self._notify("integration")

    def _on_integration_check_response(self, data: dict) -> None:
        self.integration_prompt = None
        if data.get("action") == "ignore_and_continue":
            self.integration_degraded = True
        self._notify("integration")

    def _on_integration_final_gate_started(self, data: dict) -> None:
        self.integration_final_gate = {"status": "running"}
        self._notify("integration")

    def _on_integration_final_gate_result(self, data: dict) -> None:
        self.integration_final_gate = data
        self._notify("integration")

    def _on_all_tasks_done(self, data: dict) -> None:
        summary = data.get("summary", {})
        result = summary.get("result", "complete")
        if result == "partial_success":
            self.phase = "partial_success"
        elif result == "error":
            self.phase = "error"
        else:
            self.phase = "final_approval"
        self._notify("phase")

    def _on_interrupted(self, data: dict) -> None:
        self.phase = "interrupted"
        self._notify("phase")

    def _on_pr_creating(self, data: dict) -> None:
        self.phase = "pr_creating"
        self._notify("phase")

    def _on_pr_created(self, data: dict) -> None:
        self.pr_url = data.get("pr_url")
        repo_id = data.get("repo_id")
        if repo_id and self.pr_url:
            self.per_repo_pr_urls[repo_id] = self.pr_url
        self.phase = "pr_created"
        self._notify("phase")

    def _on_pr_failed(self, data: dict) -> None:
        self.error = data.get("error", "PR creation failed")
        self._notify("error")

    def reset(self) -> None:
        """Reset all pipeline-specific state for a new task."""
        self.phase = "idle"
        self.tasks.clear()
        self.task_order.clear()
        self.selected_task_id = None
        self.agent_output.clear()
        self.review_output.clear()
        self.unified_log.clear()
        self.planner_output.clear()
        self.review_gates.clear()
        self.streaming_task_ids.clear()
        self.error = None
        self.elapsed_seconds = 0.0
        self.total_cost_usd = 0.0
        self.pipeline_branch = ""
        self.base_branch = "main"
        self.question_history.clear()
        self.pending_questions.clear()
        self.pr_url = None
        self._pending_state_updates.clear()
        self.error_history.clear()
        self.planning_stage = ""
        self.task_diffs.clear()
        # Integration health checks
        self.integration_baseline = None
        self.integration_degraded = False
        self.integration_checks.clear()
        self.integration_prompt = None
        self.integration_final_gate = None
        # Multi-repo state
        self.repos = []
        self.per_repo_pr_urls = {}
        self.per_repo_merge_status = {}

    @property
    def done_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t["state"] == "done")

    @property
    def error_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t["state"] == "error")

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def progress_pct(self) -> float:
        if not self.tasks:
            return 0.0
        return (self.done_count / self.total_count) * 100

    @property
    def active_task_ids(self) -> list[str]:
        return [
            tid
            for tid, t in self.tasks.items()
            if t["state"] in ("in_progress", "in_review", "merging")
        ]

    @property
    def is_multi_repo(self) -> bool:
        return len(self.repos) > 1

    _EVENT_MAP: dict[str, Callable[[TuiState, dict], None]] = {
        "pipeline:phase_changed": _on_phase_changed,
        "pipeline:plan_ready": _on_plan_ready,
        "pipeline:cost_update": _on_cost_update,
        "pipeline:error": _on_pipeline_error,
        "pipeline:branch_resolved": _on_branch_resolved,
        "task:state_changed": _on_task_state_changed,
        "task:agent_output": _on_agent_output,
        "task:cost_update": _on_task_cost_update,
        "task:review_update": _on_review_update,
        "task:merge_result": _on_merge_result,
        "task:merge_progress": _on_merge_progress,
        "task:awaiting_approval": _on_awaiting_approval,
        "task:review_diff": _on_review_diff,
        "planner:output": _on_planner_output,
        "planning:question": _on_planning_question,
        "planning:answer": _on_planning_answer,
        "task:question": _on_task_question,
        "task:answer": _on_task_answer,
        "task:resumed": _on_task_resumed,
        "task:auto_decided": _on_task_auto_decided,
        "task:interjection": _on_task_interjection,
        "review:gate_started": _on_review_gate_started,
        "review:gate_passed": _on_review_gate_passed,
        "review:gate_failed": _on_review_gate_failed,
        "review:llm_feedback": _on_review_llm_feedback,
        "review:llm_output": _on_review_llm_output,
        "pipeline:all_tasks_done": _on_all_tasks_done,
        "pipeline:interrupted": _on_interrupted,
        "pipeline:pr_creating": _on_pr_creating,
        "pipeline:pr_created": _on_pr_created,
        "pipeline:pr_failed": _on_pr_failed,
        "pipeline:cost_estimate": _on_cost_estimate,
        "pipeline:budget_exceeded": _on_budget_exceeded,
        "contracts:output": _on_contracts_output,
        "pipeline:contracts_ready": _on_contracts_ready,
        "pipeline:contracts_failed": _on_contracts_failed,
        "task:files_changed": _on_files_changed,
        "pipeline:cancelled": _on_cancelled,
        "pipeline:paused": _on_paused,
        "pipeline:resumed": _on_pipeline_resumed,
        "pipeline:restarted": _on_restarted,
        "pipeline:worktrees_cleaned": _on_worktrees_cleaned,
        "pipeline:preflight_failed": _on_preflight_failed,
        "followup:task_started": _on_followup_started,
        "followup:task_completed": _on_followup_completed,
        "followup:task_error": _on_followup_error,
        "followup:agent_output": _on_followup_output,
        "slot:acquired": _on_slot_acquired,
        "slot:released": _on_slot_released,
        "slot:queued": _on_slot_queued,
        # Integration health checks
        "integration:baseline_started": _on_integration_baseline_started,
        "integration:baseline_result": _on_integration_baseline_result,
        "integration:baseline_failed_prompt": _on_integration_baseline_failed_prompt,
        "integration:baseline_response": _on_integration_baseline_response,
        "integration:check_started": _on_integration_check_started,
        "integration:check_result": _on_integration_check_result,
        "integration:check_prompt": _on_integration_check_prompt,
        "integration:check_response": _on_integration_check_response,
        "integration:final_gate_started": _on_integration_final_gate_started,
        "integration:final_gate_result": _on_integration_final_gate_result,
    }
