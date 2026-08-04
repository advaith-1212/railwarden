from __future__ import annotations

import shutil
from pathlib import Path

from railwarden.errors import GitError
from railwarden.git import (
    branch_exists,
    current_branch,
    run_git,
    tracked_is_clean,
    worktree_entries,
    worktree_is_usable,
)


def _remove_workspace(repository: Path, workspace: Path) -> None:
    run_git(repository, "worktree", "prune", check=False)
    shutil.rmtree(workspace)


def is_registered_worktree(repository: Path, workspace: Path) -> bool:
    target = workspace.resolve()
    return any(
        Path(entry.get("worktree", "")).resolve() == target
        for entry in worktree_entries(repository)
    )


def ensure_worktree(
    *,
    repository: Path,
    integration_branch: str,
    workspace: Path,
    branch: str,
    action: str,
    dry_run: bool = False,
) -> dict[str, object]:
    registered = (
        is_registered_worktree(repository, workspace) if workspace.exists() else False
    )
    if workspace.exists() and not registered:
        git_marker = workspace / ".git"
        if git_marker.exists():
            if worktree_is_usable(workspace):
                raise GitError(
                    f"Refusing to use unregistered path as a worktree: {workspace}"
                )
            _remove_workspace(repository, workspace)
        else:
            shutil.rmtree(workspace)
        registered = False
    if registered:
        actual_branch = current_branch(workspace)
        if actual_branch != branch:
            raise GitError(
                f"Worktree {workspace} is on {actual_branch!r}, expected {branch!r}"
            )
        dirty = not tracked_is_clean(workspace)
        if dirty and action != "repair":
            raise GitError(
                f"Execution worktree is dirty and cannot be reused safely: {workspace}"
            )
        return {
            "operation": "reuse",
            "workspace": str(workspace),
            "branch": branch,
            "dirty": dirty,
        }
    if action == "repair":
        raise GitError(
            f"Repair worktree does not exist or is not registered: {workspace}"
        )
    command = (
        ["worktree", "add", str(workspace), branch]
        if branch_exists(repository, branch)
        else ["worktree", "add", "-b", branch, str(workspace), integration_branch]
    )
    if not dry_run:
        workspace.parent.mkdir(parents=True, exist_ok=True)
        run_git(repository, *command)
    return {
        "operation": "create",
        "workspace": str(workspace),
        "branch": branch,
        "dirty": False,
        "command": ["git", "-C", str(repository), *command],
    }
