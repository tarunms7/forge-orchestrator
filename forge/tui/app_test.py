"""Tests for _build_task_summaries multi-repo fields."""


from forge.tui.app import _build_task_summaries


class TestBuildTaskSummariesRepoId:
    def test_build_task_summaries_includes_repo_id(self):
        tasks = [
            {
                "title": "Task A",
                "description": "desc a",
                "state": "done",
                "repo_id": "backend",
                "cost_usd": 1.5,
                "merge_result": {"success": True, "linesAdded": 10, "linesRemoved": 2, "filesChanged": 3},
                "files": ["a.py"],
            },
            {
                "title": "Task B",
                "description": "desc b",
                "state": "done",
                "repo_id": "frontend",
                "cost_usd": 0.75,
                "merge_result": {"success": True, "linesAdded": 5, "linesRemoved": 1, "filesChanged": 1},
                "files": ["b.ts"],
            },
            {
                "title": "Task C",
                "description": "desc c",
                "state": "done",
                # no repo_id, no cost_usd — should default
                "merge_result": {"success": True, "linesAdded": 1, "linesRemoved": 0, "filesChanged": 1},
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
