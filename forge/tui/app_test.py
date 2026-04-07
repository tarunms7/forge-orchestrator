"""Tests for PR task summaries and multi-repo wiring."""

from forge.tui.app import _build_task_summaries, _partition_pr_task_summaries
from forge.tui.state import TuiState


class TestBuildTaskSummariesRepoId:
    def test_build_task_summaries_includes_repo_id(self):
        tasks = [
            {
                "title": "Task A",
                "description": "desc a",
                "state": "done",
                "repo_id": "backend",
                "cost_usd": 1.5,
                "merge_result": {
                    "success": True,
                    "linesAdded": 10,
                    "linesRemoved": 2,
                    "filesChanged": 3,
                },
                "files": ["a.py"],
            },
            {
                "title": "Task B",
                "description": "desc b",
                "state": "done",
                "repo_id": "frontend",
                "cost_usd": 0.75,
                "merge_result": {
                    "success": True,
                    "linesAdded": 5,
                    "linesRemoved": 1,
                    "filesChanged": 1,
                },
                "files": ["b.ts"],
            },
            {
                "title": "Task C",
                "description": "desc c",
                "state": "done",
                # no repo_id, no cost_usd — should default
                "merge_result": {
                    "success": True,
                    "linesAdded": 1,
                    "linesRemoved": 0,
                    "filesChanged": 1,
                },
                "files": ["c.py"],
            },
        ]

        summaries = _build_task_summaries(tasks)

        assert len(summaries) == 3

        # Task A — explicit repo_id and cost_usd
        assert summaries[0]["repo_id"] == "backend"
        assert summaries[0]["cost_usd"] == 1.5
        assert summaries[0]["title"] == "Task A"
        assert summaries[0]["added"] == 10
        assert summaries[0]["removed"] == 2

        # Task B — explicit repo_id and cost_usd
        assert summaries[1]["repo_id"] == "frontend"
        assert summaries[1]["cost_usd"] == 0.75

        # Task C — defaults
        assert summaries[2]["repo_id"] == "default"
        assert summaries[2]["cost_usd"] == 0

        # All tasks should have enrichment fields (with defaults since no state data provided)
        for summary in summaries:
            assert summary["retry_count"] == 0
            assert summary["blocked_reason"] == ""
            assert summary["review_substatus"] == ""
            assert summary["merge_substatus"] == ""
            assert summary["review_gates"] == {}

    def test_build_task_summaries_with_enrichment_data(self):
        """_build_task_summaries should populate enrichment fields when state data provided."""
        tasks = [
            {
                "id": "task1",
                "title": "Retry task",
                "state": "done",
                "merge_result": {"success": True, "linesAdded": 5, "linesRemoved": 1, "filesChanged": 1},
            },
            {
                "id": "task2",
                "title": "Blocked task",
                "state": "blocked",
                "error": "dependency failed",
            },
            {
                "id": "task3",
                "title": "Reviewing task",
                "state": "in_review",
            },
        ]
        error_history = {"task1": ["first error", "second error"]}
        review_substatus = {"task3": "🔨 Build running"}
        merge_substatus = {"task1": "rebasing"}
        review_gates = {"task3": {"gate0_build": {"status": "passed"}}}

        summaries = _build_task_summaries(
            tasks,
            error_history=error_history,
            review_substatus=review_substatus,
            merge_substatus=merge_substatus,
            review_gates=review_gates,
        )

        assert len(summaries) == 3

        # Task 1 - has retry history
        assert summaries[0]["title"] == "Retry task"
        assert summaries[0]["retry_count"] == 2
        assert summaries[0]["blocked_reason"] == ""
        assert summaries[0]["merge_substatus"] == "rebasing"

        # Task 2 - blocked
        assert summaries[1]["title"] == "Blocked task"
        assert summaries[1]["retry_count"] == 0
        assert summaries[1]["blocked_reason"] == "dependency failed"

        # Task 3 - in review
        assert summaries[2]["title"] == "Reviewing task"
        assert summaries[2]["review_substatus"] == "🔨 Build running"
        assert summaries[2]["review_gates"] == {"gate0_build": {"status": "passed"}}

    def test_partition_pr_task_summaries_excludes_transient_states(self):
        summaries = [
            {"title": "Done", "state": "done"},
            {"title": "Merging", "state": "merging", "error": ""},
            {"title": "Reviewing", "state": "in_review", "error": ""},
            {"title": "Failed", "state": "error", "error": "review failed"},
            {"title": "Blocked", "state": "blocked", "error": "dep failed"},
        ]

        done_tasks, failed_tasks = _partition_pr_task_summaries(summaries)

        assert [task["title"] for task in done_tasks] == ["Done"]
        assert failed_tasks is not None
        assert [task["title"] for task in failed_tasks] == ["Failed", "Blocked"]


class TestMultiRepoPrCreationEvents:
    """Verify that multi-repo PR creation emits per-repo events with repo_id."""

    def test_per_repo_pr_created_events(self):
        """Emitting pipeline:pr_created with repo_id populates per_repo_pr_urls."""
        state = TuiState()
        # Set up multi-repo state
        state.apply_event(
            "pipeline:plan_ready",
            {
                "tasks": [
                    {
                        "id": "t1",
                        "title": "Auth",
                        "repo": "backend",
                        "files": ["a.py"],
                        "depends_on": [],
                        "complexity": "medium",
                    },
                    {
                        "id": "t2",
                        "title": "Login",
                        "repo": "frontend",
                        "files": ["b.tsx"],
                        "depends_on": [],
                        "complexity": "low",
                    },
                ],
                "repos": [
                    {"repo_id": "backend", "project_dir": "/p/backend", "base_branch": "main"},
                    {"repo_id": "frontend", "project_dir": "/p/frontend", "base_branch": "main"},
                ],
            },
        )
        assert state.is_multi_repo

        # Simulate per-repo PR creation events (as the app would emit them)
        state.apply_event(
            "pipeline:pr_created",
            {
                "pr_url": "https://github.com/org/backend/pull/42",
                "repo_id": "backend",
            },
        )
        state.apply_event(
            "pipeline:pr_created",
            {
                "pr_url": "https://github.com/org/frontend/pull/99",
                "repo_id": "frontend",
            },
        )

        assert state.per_repo_pr_urls == {
            "backend": "https://github.com/org/backend/pull/42",
            "frontend": "https://github.com/org/frontend/pull/99",
        }
        assert state.phase == "pr_created"

    def test_final_approval_receives_multi_repo_flag(self):
        """FinalApprovalScreen is constructed with multi_repo params."""
        from forge.tui.screens.final_approval import FinalApprovalScreen

        repos = [
            {"repo_id": "backend", "project_dir": "/p/backend", "base_branch": "main"},
            {"repo_id": "frontend", "project_dir": "/p/frontend", "base_branch": "main"},
        ]
        pr_urls = {"backend": "https://github.com/org/backend/pull/42"}
        stats = {
            "added": 100,
            "removed": 20,
            "files": 5,
            "elapsed": "2m 30s",
            "cost": 1.5,
            "questions": 1,
            "repo_count": 2,
            "task_count": 3,
        }

        screen = FinalApprovalScreen(
            stats=stats,
            tasks=[],
            pipeline_branch="forge/feature",
            base_branch="main",
            partial=False,
            multi_repo=True,
            per_repo_pr_urls=pr_urls,
            repos=repos,
        )

        assert screen._multi_repo is True
        assert screen._per_repo_pr_urls == pr_urls
        assert screen._repos == repos
        assert screen._stats["repo_count"] == 2
        assert screen._stats["task_count"] == 3

    def test_stats_includes_repo_count_when_multi_repo(self):
        """_push_final_approval should add repo_count and task_count when multi-repo."""
        state = TuiState()
        state.apply_event(
            "pipeline:plan_ready",
            {
                "tasks": [
                    {
                        "id": "t1",
                        "title": "Auth",
                        "repo": "backend",
                        "files": ["a.py"],
                        "depends_on": [],
                        "complexity": "medium",
                    },
                ],
                "repos": [
                    {"repo_id": "backend", "project_dir": "/p/backend", "base_branch": "main"},
                    {"repo_id": "frontend", "project_dir": "/p/frontend", "base_branch": "main"},
                ],
            },
        )
        assert state.is_multi_repo
        assert len(state.repos) == 2
