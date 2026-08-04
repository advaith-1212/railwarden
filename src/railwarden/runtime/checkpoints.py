from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from railwarden.config.models import ProjectConfig
from railwarden.errors import GitError
from railwarden.git import current_branch, output, run_git
from railwarden.runtime.events import append_event
from railwarden.runtime.secrets import redacted
from railwarden.util.atomic import atomic_write_json


@dataclass(frozen=True)
class CheckpointResult:
    task_id: str
    branch: str
    attempt: int
    commit: str | None
    files: tuple[str, ...]
    metadata_path: Path
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "branch": self.branch,
            "attempt": self.attempt,
            "commit": self.commit,
            "files": list(self.files),
            "metadata_path": str(self.metadata_path),
            "status": self.status,
        }


def create_checkpoint_commit(
    config: ProjectConfig,
    *,
    task_id: str,
    workspace: Path,
    attempt: int,
    allowed_paths: tuple[str, ...] = (),
) -> CheckpointResult:
    branch = current_branch(workspace)
    if branch == config.integration_branch:
        raise GitError("Checkpoint commits are forbidden on the integration branch")
    if not branch:
        raise GitError("Checkpoint commits require a named task branch")
    run_git(workspace, "add", "-A", "--", ".")
    if allowed_paths:
        _unstage_disallowed(workspace, allowed_paths)
    staged = _staged_files(workspace)
    commit: str | None = None
    status = "empty"
    if staged:
        message = f"warden checkpoint: {task_id} attempt {attempt}"
        run_git(workspace, "commit", "-m", message)
        commit = output(workspace, "rev-parse", "HEAD")
        status = "created"
    metadata = {
        "task_id": task_id,
        "branch": branch,
        "attempt": attempt,
        "commit": commit,
        "files": staged,
        "created_at": time.time(),
        "status": status,
    }
    path = config.runtime_directory / "checkpoints" / f"{task_id}-{attempt}.json"
    atomic_write_json(path, json.loads(redacted(json.dumps(metadata))))
    append_event(
        config.runtime_directory,
        "checkpoint_created",
        {"branch": branch, "commit": commit, "files": staged, "status": status},
        task_id=task_id,
    )
    return CheckpointResult(
        task_id=task_id,
        branch=branch,
        attempt=attempt,
        commit=commit,
        files=tuple(staged),
        metadata_path=path,
        status=status,
    )


def _staged_files(workspace: Path) -> list[str]:
    text = output(workspace, "diff", "--cached", "--name-only")
    return [line for line in text.splitlines() if line]


def _unstage_disallowed(workspace: Path, allowed_paths: tuple[str, ...]) -> None:
    staged = _staged_files(workspace)
    disallowed = [
        item
        for item in staged
        if not any(
            item == allowed.rstrip("/") or item.startswith(f"{allowed.rstrip('/')}/")
            for allowed in allowed_paths
        )
    ]
    if disallowed:
        run_git(workspace, "restore", "--staged", "--", *disallowed)
