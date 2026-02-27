"""Cross-session continuity. Structured handoff between sessions."""

import os


class SessionHandoff:
    """Manages session handoff files for cross-session continuity."""

    def __init__(self, forge_dir: str) -> None:
        self._dir = forge_dir

    def _handoff_path(self) -> str:
        return os.path.join(self._dir, "session-handoff.md")

    def _build_log_path(self) -> str:
        return os.path.join(self._dir, "build-log.md")

    def write(
        self,
        completed: list[str],
        in_progress: list[str],
        blockers: list[str],
        next_steps: list[str],
        decisions: list[str],
    ) -> None:
        """Write a structured session handoff file."""
        os.makedirs(self._dir, exist_ok=True)
        lines = ["# Session Handoff\n"]

        lines.append("\n## Completed\n")
        for item in completed:
            lines.append(f"- {item}\n")

        lines.append("\n## In Progress\n")
        for item in in_progress:
            lines.append(f"- {item}\n")

        if blockers:
            lines.append("\n## Blockers\n")
            for item in blockers:
                lines.append(f"- {item}\n")

        lines.append("\n## Next Steps\n")
        for item in next_steps:
            lines.append(f"- {item}\n")

        lines.append("\n## Decisions This Session\n")
        for item in decisions:
            lines.append(f"- {item}\n")

        with open(self._handoff_path(), "w") as f:
            f.writelines(lines)

    def read(self) -> str | None:
        """Read the handoff file, or None if it doesn't exist."""
        path = self._handoff_path()
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read()

    def update_build_log(self, phases: dict[str, bool]) -> None:
        """Update the build log with phase completion status."""
        os.makedirs(self._dir, exist_ok=True)
        lines = ["# Forge Build Log\n\n"]
        for phase, done in phases.items():
            marker = "x" if done else " "
            lines.append(f"- [{marker}] {phase}\n")

        with open(self._build_log_path(), "w") as f:
            f.writelines(lines)
