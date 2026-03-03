"""Follow-up question routing and execution.

After a pipeline completes, users can submit follow-up questions that get
intelligently routed to the agent that originally worked on the relevant task.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from claude_code_sdk import ClaudeCodeOptions

from forge.agents.adapter import ClaudeAdapter
from forge.agents.runtime import AgentRuntime
from forge.core.events import EventEmitter
from forge.core.sdk_helpers import sdk_query
from forge.storage.db import Database

logger = logging.getLogger("forge.followup")


class FollowUpStatus(str, Enum):
    """Status of a follow-up execution."""
    PENDING = "pending"
    CLASSIFYING = "classifying"
    EXECUTING = "executing"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class FollowUpQuestion:
    """A single follow-up question from the user."""
    text: str
    context: str | None = None


@dataclass
class ClassifiedQuestion:
    """A question that has been mapped to an original task."""
    question_index: int
    question: FollowUpQuestion
    task_id: str
    task_title: str


@dataclass
class FollowUpResult:
    """Result of executing follow-ups for a single task."""
    task_id: str
    task_title: str
    questions: list[FollowUpQuestion]
    success: bool
    summary: str
    files_changed: list[str] = field(default_factory=list)
    error: str | None = None
    cost_usd: float = 0.0


@dataclass
class FollowUpExecution:
    """Full follow-up execution state."""
    id: str
    pipeline_id: str
    status: FollowUpStatus
    questions: list[FollowUpQuestion]
    classification: dict[int, str] = field(default_factory=dict)  # question_index -> task_id
    results: list[FollowUpResult] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


async def classify_questions(
    questions: list[FollowUpQuestion],
    tasks: list[dict],
) -> dict[int, str]:
    """Use an LLM call to classify which original task each follow-up relates to.

    Args:
        questions: List of follow-up questions from the user.
        tasks: List of original task dicts with id, title, description, files.

    Returns:
        Mapping of question_index -> task_id.
    """
    if not questions or not tasks:
        return {}

    # If there's only one task, all questions map to it
    if len(tasks) == 1:
        return {i: tasks[0]["id"] for i in range(len(questions))}

    # Build the classification prompt
    task_descriptions = []
    for t in tasks:
        files_str = ", ".join(t.get("files", [])[:10])
        task_descriptions.append(
            f"  - ID: {t['id']}\n"
            f"    Title: {t.get('title', 'Untitled')}\n"
            f"    Description: {t.get('description', 'No description')}\n"
            f"    Files: {files_str}"
        )

    question_descriptions = []
    for i, q in enumerate(questions):
        ctx = f" (Context: {q.context})" if q.context else ""
        question_descriptions.append(f"  {i}: {q.text}{ctx}")

    prompt = (
        "You are a classifier. Given a list of completed tasks from a software pipeline "
        "and a list of follow-up questions, determine which task each question relates to.\n\n"
        "## Original Tasks\n"
        f"{''.join(task_descriptions)}\n\n"
        "## Follow-up Questions\n"
        f"{''.join(question_descriptions)}\n\n"
        "Respond with ONLY a JSON object mapping question index (as string) to task ID. "
        "Every question must be mapped to exactly one task. If a question doesn't clearly "
        "relate to any task, assign it to the most relevant one.\n\n"
        'Example response: {"0": "task-1", "1": "task-2", "2": "task-1"}'
    )

    options = ClaudeCodeOptions(
        system_prompt="You are a JSON classifier. Respond only with valid JSON.",
        max_turns=1,
    )

    try:
        result = await sdk_query(prompt=prompt, options=options)
        if result and result.result:
            # Parse the JSON response
            text = result.result.strip()
            # Handle markdown code blocks
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            mapping = json.loads(text)
            # Convert to int keys and validate task IDs
            valid_task_ids = {t["id"] for t in tasks}
            classified: dict[int, str] = {}
            for k, v in mapping.items():
                idx = int(k)
                if 0 <= idx < len(questions) and v in valid_task_ids:
                    classified[idx] = v
            # Assign unclassified questions to the first task as fallback
            for i in range(len(questions)):
                if i not in classified:
                    classified[i] = tasks[0]["id"]
            return classified
    except Exception as exc:
        logger.warning("LLM classification failed, falling back to heuristic: %s", exc)

    # Fallback: assign all questions to the first task
    return {i: tasks[0]["id"] for i in range(len(questions))}


async def execute_followups(
    followup: FollowUpExecution,
    pipeline_tasks: list[dict],
    pipeline_db_tasks: list,
    pipeline: object,
    db: Database,
    emitter: EventEmitter | None = None,
) -> FollowUpExecution:
    """Execute follow-up questions by routing them to appropriate agents.

    For each unique task that has follow-up questions:
      1. Retrieve the original task's context
      2. Determine the working branch
      3. Create/reuse a worktree on that branch
      4. Build a prompt with original context + follow-up questions
      5. Spawn a Claude agent to address the follow-ups
      6. Stream output via events
      7. Commit changes and push to the same pipeline branch

    Args:
        followup: The FollowUpExecution tracking object.
        pipeline_tasks: List of task dicts from the pipeline's task_graph_json.
        pipeline_db_tasks: List of TaskRow objects from DB.
        pipeline: The PipelineRow object.
        db: Database instance.
        emitter: Optional EventEmitter for streaming events.

    Returns:
        Updated FollowUpExecution with results.
    """
    followup.status = FollowUpStatus.EXECUTING

    # Group questions by task_id
    task_questions: dict[str, list[tuple[int, FollowUpQuestion]]] = {}
    for q_idx, task_id in followup.classification.items():
        if task_id not in task_questions:
            task_questions[task_id] = []
        task_questions[task_id].append((q_idx, followup.questions[q_idx]))

    # Build lookup maps
    task_info_map = {t["id"]: t for t in pipeline_tasks}
    db_task_map = {t.id: t for t in pipeline_db_tasks}

    project_dir = pipeline.project_dir
    pipeline_id = pipeline.id
    branch_name = f"forge/pipeline-{pipeline_id[:8]}"

    for task_id, indexed_questions in task_questions.items():
        task_info = task_info_map.get(task_id, {})
        db_task = db_task_map.get(task_id)
        task_title = task_info.get("title", task_id)
        questions_for_task = [q for _, q in indexed_questions]

        if emitter:
            await emitter.emit("followup:task_started", {
                "followup_id": followup.id,
                "task_id": task_id,
                "task_title": task_title,
                "question_count": len(questions_for_task),
            })

        try:
            result = await _execute_task_followup(
                task_id=task_id,
                task_info=task_info,
                db_task=db_task,
                questions=questions_for_task,
                project_dir=project_dir,
                branch_name=branch_name,
                pipeline_id=pipeline_id,
                db=db,
                emitter=emitter,
                followup_id=followup.id,
            )
            followup.results.append(result)

            if emitter:
                await emitter.emit("followup:task_completed", {
                    "followup_id": followup.id,
                    "task_id": task_id,
                    "success": result.success,
                    "summary": result.summary,
                    "files_changed": result.files_changed,
                })

        except Exception as exc:
            logger.exception("Follow-up execution failed for task %s", task_id)
            error_result = FollowUpResult(
                task_id=task_id,
                task_title=task_title,
                questions=questions_for_task,
                success=False,
                summary=f"Follow-up failed: {exc}",
                error=str(exc),
            )
            followup.results.append(error_result)

            if emitter:
                await emitter.emit("followup:task_error", {
                    "followup_id": followup.id,
                    "task_id": task_id,
                    "error": str(exc),
                })

    # Determine overall status
    all_success = all(r.success for r in followup.results)
    any_success = any(r.success for r in followup.results)

    if all_success:
        followup.status = FollowUpStatus.COMPLETE
    elif any_success:
        followup.status = FollowUpStatus.COMPLETE  # Partial success is still complete
    else:
        followup.status = FollowUpStatus.ERROR
        followup.error = "All follow-up executions failed"

    return followup


async def _execute_task_followup(
    *,
    task_id: str,
    task_info: dict,
    db_task: object | None,
    questions: list[FollowUpQuestion],
    project_dir: str,
    branch_name: str,
    pipeline_id: str,
    db: Database,
    emitter: EventEmitter | None,
    followup_id: str,
) -> FollowUpResult:
    """Execute follow-up questions for a single task.

    Creates a worktree on the pipeline branch, builds a context-rich prompt,
    and spawns a Claude agent to address the follow-ups.
    """
    task_title = task_info.get("title", task_id)
    task_description = task_info.get("description", "")
    task_files = task_info.get("files", [])

    # Gather original agent output and review feedback from events
    original_output = await _gather_task_context(pipeline_id, task_id, db)

    # Build the follow-up prompt
    prompt = _build_followup_prompt(
        task_title=task_title,
        task_description=task_description,
        task_files=task_files,
        original_output=original_output,
        review_feedback=getattr(db_task, "review_feedback", None) if db_task else None,
        questions=questions,
    )

    # Set up worktree on the pipeline branch
    worktree_id = f"followup-{followup_id[:8]}-{task_id}"
    worktree_dir = os.path.join(project_dir, ".forge", "worktrees", worktree_id)

    try:
        _setup_worktree(project_dir, worktree_dir, branch_name, worktree_id)
    except Exception as exc:
        return FollowUpResult(
            task_id=task_id,
            task_title=task_title,
            questions=questions,
            success=False,
            summary=f"Failed to set up worktree: {exc}",
            error=str(exc),
        )

    try:
        # Set up message streaming callback
        async def on_message(msg):
            if emitter:
                text = ""
                if hasattr(msg, "content"):
                    text = msg.content if isinstance(msg.content, str) else str(msg.content)
                elif hasattr(msg, "result"):
                    text = msg.result or ""
                if text:
                    await emitter.emit("followup:agent_output", {
                        "followup_id": followup_id,
                        "task_id": task_id,
                        "line": text[:500],
                    })

        # Run the agent
        adapter = ClaudeAdapter()
        runtime = AgentRuntime(adapter=adapter, timeout_seconds=600)

        agent_result = await runtime.run_task(
            agent_id=f"followup-{task_id}",
            task_prompt=prompt,
            worktree_path=worktree_dir,
            allowed_files=task_files,
            on_message=on_message,
        )

        # If the agent made changes, commit and push
        files_changed = agent_result.files_changed
        if files_changed and agent_result.success:
            _commit_and_push(worktree_dir, project_dir, branch_name, followup_id, task_title)

        return FollowUpResult(
            task_id=task_id,
            task_title=task_title,
            questions=questions,
            success=agent_result.success,
            summary=agent_result.summary,
            files_changed=files_changed,
            error=agent_result.error,
            cost_usd=agent_result.cost_usd,
        )

    finally:
        # Clean up worktree
        _cleanup_worktree(project_dir, worktree_dir, worktree_id)


async def _gather_task_context(
    pipeline_id: str,
    task_id: str,
    db: Database,
) -> str:
    """Gather the original agent output and review feedback for a task."""
    events = await db.list_events(pipeline_id, task_id=task_id)

    output_lines: list[str] = []
    review_gates: list[str] = []

    for ev in events:
        if ev.event_type == "task:agent_output":
            line = ev.payload.get("line", "")
            if line:
                output_lines.append(line)
        elif ev.event_type == "task:review_update":
            gate = ev.payload.get("gate", "unknown")
            passed = ev.payload.get("passed", False)
            details = ev.payload.get("details", "")
            status = "PASS" if passed else "FAIL"
            review_gates.append(f"  [{status}] {gate}: {details}")

    parts: list[str] = []
    if output_lines:
        # Limit to last 100 lines to avoid prompt bloat
        recent_lines = output_lines[-100:]
        parts.append("## Agent Output (last 100 lines)\n" + "\n".join(recent_lines))
    if review_gates:
        parts.append("## Review Results\n" + "\n".join(review_gates))

    return "\n\n".join(parts) if parts else "(No prior output recorded)"


def _build_followup_prompt(
    *,
    task_title: str,
    task_description: str,
    task_files: list[str],
    original_output: str,
    review_feedback: str | None,
    questions: list[FollowUpQuestion],
) -> str:
    """Build a comprehensive prompt for the follow-up agent."""
    files_str = "\n".join(f"  - {f}" for f in task_files) if task_files else "  (none specified)"

    questions_str = "\n".join(
        f"  {i + 1}. {q.text}" + (f"\n     Context: {q.context}" if q.context else "")
        for i, q in enumerate(questions)
    )

    feedback_section = ""
    if review_feedback:
        feedback_section = f"\n## Review Feedback from Previous Run\n{review_feedback}\n"

    return (
        f"# Follow-up Task\n\n"
        f"You are continuing work on a previously completed task. "
        f"The user has follow-up questions/requests that need to be addressed.\n\n"
        f"## Original Task\n"
        f"**Title:** {task_title}\n"
        f"**Description:** {task_description}\n"
        f"**Files:** \n{files_str}\n\n"
        f"## What Was Previously Done\n"
        f"{original_output}\n"
        f"{feedback_section}\n"
        f"## Follow-up Questions/Requests\n"
        f"{questions_str}\n\n"
        f"## Instructions\n"
        f"1. Read the relevant files to understand the current state of the code\n"
        f"2. Address each follow-up question/request\n"
        f"3. Make the necessary code changes\n"
        f"4. Write or update tests as needed\n"
        f"5. Commit your changes with a clear message describing the follow-up work\n"
    )


def _setup_worktree(
    project_dir: str,
    worktree_dir: str,
    branch_name: str,
    worktree_id: str,
) -> None:
    """Create a git worktree on the pipeline branch for follow-up work."""
    os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)

    # Check if the branch exists
    branch_check = subprocess.run(
        ["git", "rev-parse", "--verify", branch_name],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )

    if branch_check.returncode != 0:
        raise RuntimeError(
            f"Pipeline branch '{branch_name}' does not exist. "
            f"Cannot create worktree for follow-up."
        )

    # Create worktree from the pipeline branch (detached)
    result = subprocess.run(
        ["git", "worktree", "add", worktree_dir, branch_name],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Failed to create worktree: {error}")


def _commit_and_push(
    worktree_dir: str,
    project_dir: str,
    branch_name: str,
    followup_id: str,
    task_title: str,
) -> None:
    """Commit follow-up changes and push to the pipeline branch."""
    # Stage all changes
    subprocess.run(
        ["git", "add", "-A"],
        cwd=worktree_dir,
        capture_output=True,
    )

    # Check if there are staged changes
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=worktree_dir,
        capture_output=True,
    )
    if status.returncode == 0:
        # No changes to commit
        return

    # Commit
    commit_msg = f"followup({followup_id[:8]}): address follow-up for '{task_title}'"
    subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=worktree_dir,
        capture_output=True,
        text=True,
    )

    # Push to remote (best effort — don't fail the whole follow-up if push fails)
    remote_result = subprocess.run(
        ["git", "remote"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    remotes = remote_result.stdout.strip()
    if remotes:
        remote_name = remotes.split("\n")[0]
        push_result = subprocess.run(
            ["git", "push", remote_name, f"HEAD:{branch_name}"],
            cwd=worktree_dir,
            capture_output=True,
            text=True,
        )
        if push_result.returncode != 0:
            logger.warning(
                "Push failed for follow-up %s: %s",
                followup_id,
                push_result.stderr.strip(),
            )


def _cleanup_worktree(
    project_dir: str,
    worktree_dir: str,
    worktree_id: str,
) -> None:
    """Remove a follow-up worktree."""
    try:
        subprocess.run(
            ["git", "worktree", "remove", worktree_dir, "--force"],
            cwd=project_dir,
            capture_output=True,
        )
    except Exception as exc:
        logger.warning("Failed to remove worktree %s: %s", worktree_id, exc)
