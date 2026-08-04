from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from railwarden.util.atomic import atomic_write_json

ACTIVE_TASK_STATES = {
    "ready",
    "assigned",
    "running",
    "handoff_needed",
    "cooldown_wait",
    "validating",
    "validated",
    "review_ready",
    "reviewing",
    "review_passed",
    "merge_ready",
    "merge_approved",
    "integrating",
    "review",
    "blocked",
    "scheduled",
    "todo",
    "triage",
}


class FileTaskBackend:
    def __init__(self, runtime_dir: Path) -> None:
        self.path = runtime_dir / "state" / "tasks.json"

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.list_tasks_compat()

    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        atomic_write_json(self.path, {"tasks": tasks})

    def list_tasks_compat(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        raw = payload.get("tasks", []) if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            raise RuntimeError(f"Invalid task state: {self.path}")
        return [item for item in raw if isinstance(item, dict)]

    def task_for_package(self, package_id: str) -> dict[str, Any] | None:
        matches = [
            task
            for task in self.list_tasks_compat()
            if str(task.get("package_id", "")) == package_id
            or str(task.get("title", "")).startswith(f"{package_id} ")
        ]
        if not matches:
            return None
        priority = {
            "running": 0,
            "ready": 1,
            "validating": 2,
            "review_ready": 3,
            "reviewing": 4,
            "review_passed": 5,
            "merge_ready": 6,
            "merge_approved": 7,
            "review": 2,
            "blocked": 3,
            "todo": 4,
            "scheduled": 5,
            "triage": 6,
            "done": 7,
            "archived": 8,
        }
        return sorted(
            matches,
            key=lambda task: (
                priority.get(str(task.get("status")), 99),
                str(task.get("id", "")),
            ),
        )[0]
