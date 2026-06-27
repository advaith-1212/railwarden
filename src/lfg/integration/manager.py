from __future__ import annotations

import fcntl
from typing import Any

from lfg.config.models import ProjectConfig, ValidationCommand
from lfg.errors import GitError, ValidationError
from lfg.git import (
    branch_is_ancestor,
    head,
    output,
    run_git,
    tracked_is_clean,
    untracked_files,
)
from lfg.util.atomic import atomic_write_json
from lfg.validation.runner import cleanup_new_untracked, run_validation_suite


def integrate_one(
    *,
    config: ProjectConfig,
    candidate: dict[str, Any],
    validation_commands: tuple[ValidationCommand, ...],
    execute: bool,
) -> dict[str, Any]:
    lock_dir = config.runtime_directory / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "integration.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GitError("Another integration manager is already running")
        repository = config.repository_root
        if output(repository, "branch", "--show-current") != config.integration_branch:
            raise GitError(f"Repository must be on {config.integration_branch}")
        if not tracked_is_clean(repository):
            raise GitError("Integration repository has tracked changes before merge")
        package_id = str(candidate["package_id"])
        branch = str(candidate["branch"])
        scheduled_head = str(candidate["head"])
        actual_head = head(repository, branch)
        if not actual_head.startswith(scheduled_head):
            raise GitError(f"{package_id} head changed after scheduling")
        if branch_is_ancestor(repository, branch, config.integration_branch):
            raise GitError(f"{package_id} already merged")
        before_head = head(repository)
        payload: dict[str, Any] = {
            "status": "planned",
            "package_id": package_id,
            "branch": branch,
            "before_head": before_head,
            "scheduled_head": scheduled_head,
            "actual_head": actual_head,
        }
        if not execute:
            return payload
        before_untracked = untracked_files(repository)
        merge = run_git(
            repository, "merge", "--no-ff", "--no-edit", branch, check=False
        )
        if merge.returncode != 0:
            run_git(repository, "merge", "--abort", check=False)
            raise GitError(f"Merge failed for {package_id}: {merge.stderr}")
        after_head = head(repository)
        try:
            status, results, removed = run_validation_suite(
                repository, validation_commands
            )
        except ValidationError as exc:
            run_git(repository, "reset", "--hard", before_head, check=False)
            removed = cleanup_new_untracked(repository, before_untracked, ())
            raise ValidationError(
                f"Post-merge validation failed for {package_id}; rolled back to {before_head}; cleanup={removed}: {exc}"
            )
        if status != "passed":
            run_git(repository, "reset", "--hard", before_head, check=False)
            removed = cleanup_new_untracked(repository, before_untracked, ())
            raise ValidationError(
                f"Post-merge validation failed for {package_id}; rolled back to {before_head}; cleanup={removed}"
            )
        payload.update(
            {
                "status": "merged_and_validated",
                "after_head": after_head,
                "validation": [result.to_dict() for result in results],
                "cleanup": removed,
            }
        )
        evidence_path = (
            config.runtime_directory
            / "validation"
            / f"{package_id}-{after_head[:12]}.json"
        )
        atomic_write_json(evidence_path, payload)
        return payload
