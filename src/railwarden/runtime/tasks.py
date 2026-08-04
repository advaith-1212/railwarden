from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from railwarden.runtime.events import append_event
from railwarden.tasks.file_backend import FileTaskBackend

TASK_STATES = {
    "planned",
    "ready",
    "assigned",
    "running",
    "handoff_needed",
    "cooldown_wait",
    "decision_required",
    "validating",
    "validated",
    "review_ready",
    "reviewing",
    "review_passed",
    "merge_ready",
    "merge_approved",
    "integration_ready",
    "integrating",
    "merged",
    "rejected",
    "blocked",
    "failed",
}

# A terminal task must never be silently resurrected. Other transitions remain
# intentionally controller-driven while the pre-1.0 lifecycle matures.
TERMINAL_TASK_STATES = {"merged", "rejected"}


def task_id_for_package(package_id: str) -> str:
    return f"task-{package_id}"


def load_tasks(runtime_dir: Path) -> list[dict[str, Any]]:
    return FileTaskBackend(runtime_dir).list_tasks_compat()


def save_tasks(runtime_dir: Path, tasks: list[dict[str, Any]]) -> None:
    FileTaskBackend(runtime_dir).save_tasks(tasks)


def ensure_task(
    runtime_dir: Path,
    *,
    package_id: str,
    name: str,
    dependencies: tuple[str, ...],
) -> dict[str, Any]:
    tasks = load_tasks(runtime_dir)
    wanted = task_id_for_package(package_id)
    for task in tasks:
        if task.get("id") == wanted or task.get("package_id") == package_id:
            return task
    task = {
        "id": wanted,
        "package_id": package_id,
        "title": f"{package_id} {name}",
        "status": "planned",
        "dependencies": list(dependencies),
        "attempt": 0,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    tasks.append(task)
    save_tasks(runtime_dir, tasks)
    append_event(runtime_dir, "task_created", {"status": "planned"}, task_id=wanted)
    return task


def upsert_task(runtime_dir: Path, updated: dict[str, Any]) -> dict[str, Any]:
    tasks = load_tasks(runtime_dir)
    task_id = str(updated["id"])
    for index, task in enumerate(tasks):
        if task.get("id") == task_id:
            tasks[index] = updated
            break
    else:
        tasks.append(updated)
    save_tasks(runtime_dir, tasks)
    return updated


def transition_task(
    runtime_dir: Path,
    task: dict[str, Any],
    status: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in TASK_STATES:
        raise ValueError(f"Unknown task status: {status}")
    old = str(task.get("status", ""))
    if old in TERMINAL_TASK_STATES and status != old:
        raise ValueError(f"Cannot transition terminal task from {old} to {status}")
    task["status"] = status
    task["updated_at"] = time.time()
    if payload:
        task.update(payload)
    upsert_task(runtime_dir, task)
    append_event(
        runtime_dir,
        "task_transition",
        {"from": old, "to": status, **(payload or {})},
        task_id=str(task.get("id")),
    )
    return task
