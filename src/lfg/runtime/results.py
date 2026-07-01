from __future__ import annotations

from pathlib import Path
from typing import Any

from lfg.config.models import ProjectConfig, WorkPackage
from lfg.errors import LfgError
from lfg.git import changed_files_in_commit, current_branch, head, tracked_is_clean
from lfg.scheduler.classifier import package_branch, package_worktree
from lfg.util.atomic import atomic_write_json
from lfg.validation.worker_result import load_worker_result


def runtime_result_path(config: ProjectConfig, task_id: str) -> Path:
    return config.runtime_directory / "results" / f"{task_id}.json"


def worker_result_path(workspace: Path, task_id: str) -> Path:
    return workspace / ".lfg-results" / f"{task_id}.json"


def normalize_result(
    config: ProjectConfig,
    *,
    task: dict[str, Any],
    package: WorkPackage,
) -> dict[str, Any]:
    task_id = str(task["id"])
    workspace = Path(str(task.get("worktree", package_worktree(config, package))))
    provider = str(task.get("provider") or task.get("last_provider") or "unknown")
    branch = str(task.get("branch") or package_branch(package))
    source_path = Path(str(task.get("result_path", worker_result_path(workspace, task_id))))
    runtime_path = Path(str(task.get("runtime_result_path", runtime_result_path(config, task_id))))
    if source_path.exists():
        payload = load_worker_result(source_path)
    else:
        if not workspace.exists():
            raise LfgError(f"Task worktree does not exist: {workspace}")
        if not tracked_is_clean(workspace):
            raise LfgError(f"Cannot synthesize result for dirty worktree: {workspace}")
        commit_hash = head(workspace)
        payload = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "worker": provider,
            "model": str(task.get("model") or provider),
            "status": "completed",
            "summary": "LFG synthesized result from clean committed work.",
            "workspace": str(workspace),
            "branch": current_branch(workspace),
            "commit_hash": commit_hash,
            "changed_files": changed_files_in_commit(workspace, commit_hash),
            "tests": [],
            "blockers": [],
            "evidence": [],
        }
    if payload.get("status") == "success":
        payload["status"] = "completed"
    payload.setdefault("schema_version", "1.0.0")
    payload.setdefault("task_id", task_id)
    payload.setdefault("worker", provider)
    payload.setdefault("model", str(task.get("model") or provider))
    payload.setdefault("summary", "")
    payload.setdefault("workspace", str(workspace))
    payload.setdefault("branch", branch)
    payload.setdefault("commit_hash", head(workspace) if workspace.exists() else None)
    if workspace.exists() and payload.get("commit_hash"):
        payload.setdefault(
            "changed_files",
            changed_files_in_commit(workspace, str(payload["commit_hash"])),
        )
    else:
        payload.setdefault("changed_files", [])
    payload.setdefault("tests", [])
    payload.setdefault("blockers", [])
    payload.setdefault("evidence", [])
    payload["normalized_by"] = "lfg"
    payload["source_result_path"] = str(source_path) if source_path.exists() else None
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(runtime_path, payload)
    return {"status": "normalized", "path": str(runtime_path), "result": payload}
