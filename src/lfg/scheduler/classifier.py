from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from lfg.config.models import ProjectConfig, WorkPackage
from lfg.git import branch_exists, branch_is_ancestor, head, output, tracked_is_clean
from lfg.scheduler.dag import validate_dag
from lfg.tasks.file_backend import ACTIVE_TASK_STATES, FileTaskBackend


@dataclass(frozen=True)
class PackageState:
    package_id: str
    name: str
    state: str
    dependencies: tuple[str, ...]
    unmet_dependencies: tuple[str, ...]
    branch: str
    worktree: Path
    head: str | None
    task_id: str | None
    task_status: str | None
    dirty: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["worktree"] = str(self.worktree)
        return payload


def package_branch(package: WorkPackage) -> str:
    return package.branch or f"lfg/{package.package_id}"


def package_worktree(config: ProjectConfig, package: WorkPackage) -> Path:
    if package.worktree is not None:
        path = Path(package.worktree)
        return path if path.is_absolute() else config.worktree_root / path
    return config.worktree_root / package.package_id.lower().replace("-", "")


def branch_has_new_commits(
    repository: Path, branch: str, base_branch: str = "main"
) -> bool:
    if not branch_exists(repository, branch):
        return False
    try:
        # Try comparing to base_branch (e.g. main)
        res = output(repository, "log", "--oneline", f"{base_branch}..{branch}")
        return res.strip() != ""
    except Exception:
        try:
            # Fallback to compare against the parent of the branch branching point
            b_base = output(repository, "merge-base", branch, base_branch)
            res = output(repository, "log", "--oneline", f"{b_base}..{branch}")
            return res.strip() != ""
        except Exception:
            return False


def classify_packages(
    config: ProjectConfig,
    packages: dict[str, WorkPackage],
    backend: FileTaskBackend | None = None,
    historical_branch_overrides: dict[str, str] | None = None,
) -> list[PackageState]:
    validate_dag(packages)
    overrides = historical_branch_overrides or {}
    tasks = backend or FileTaskBackend(config.runtime_directory)
    merged_ids = {
        package_id
        for package_id, package in packages.items()
        if branch_exists(
            config.repository_root, overrides.get(package_id, package_branch(package))
        )
        and branch_has_new_commits(
            config.repository_root,
            overrides.get(package_id, package_branch(package)),
            base_branch="main",
        )
        and branch_is_ancestor(
            config.repository_root,
            overrides.get(package_id, package_branch(package)),
            config.integration_branch,
        )
    }
    states: list[PackageState] = []
    for package_id, package in sorted(packages.items()):
        branch = overrides.get(package_id, package_branch(package))
        worktree = package_worktree(config, package)
        branch_head = (
            head(config.repository_root, branch)
            if branch_exists(config.repository_root, branch)
            else None
        )
        dirty = worktree.is_dir() and not tracked_is_clean(worktree)
        has_commits = branch_head is not None and branch_has_new_commits(
            config.repository_root,
            branch,
            base_branch=config.integration_branch,
        )
        task = tasks.task_for_package(package_id)
        task_id = str(task["id"]) if task and task.get("id") else None
        task_status = str(task["status"]) if task and task.get("status") else None
        unmet = tuple(
            dependency
            for dependency in package.dependencies
            if dependency not in merged_ids
        )
        if dirty:
            state = "repair_required"
            detail = "Worktree contains uncommitted changes and must be repaired."
        elif package_id in merged_ids:
            state = "merged"
            detail = "Package branch is contained in the integration branch."
        elif task_status in ACTIVE_TASK_STATES:
            state = "active"
            detail = f"Task is currently {task_status}."
        elif has_commits and unmet:
            state = "integration_blocked"
            detail = (
                "Branch has unmerged commits but dependencies are not merged: "
                + ", ".join(unmet)
            )
        elif has_commits:
            state = "integration_ready"
            detail = "Branch has commits ready for serialized integration."
        elif not unmet:
            state = "execution_ready"
            detail = "All dependencies are merged; a worker may execute this package."
        else:
            state = "waiting"
            detail = "Waiting for merged dependencies: " + ", ".join(unmet)
        states.append(
            PackageState(
                package_id=package_id,
                name=package.name,
                state=state,
                dependencies=package.dependencies,
                unmet_dependencies=unmet,
                branch=branch,
                worktree=worktree,
                head=branch_head,
                task_id=task_id,
                task_status=task_status,
                dirty=dirty,
                detail=detail,
            )
        )
    return states


def execution_plan(
    config: ProjectConfig, states: list[PackageState]
) -> dict[str, object]:
    available = list(config.worker_providers[: config.worker_concurrency])
    assignments: list[dict[str, object]] = []
    candidates = [
        state
        for state in states
        if state.state in {"repair_required", "execution_ready"}
    ]
    candidates.sort(
        key=lambda item: (0 if item.state == "repair_required" else 1, item.package_id)
    )
    for state in candidates:
        if not available:
            break
        worker = available.pop(0)
        assignments.append(
            {
                "package_id": state.package_id,
                "name": state.name,
                "action": "repair" if state.state == "repair_required" else "execute",
                "worker": worker,
                "branch": state.branch,
                "worktree": str(state.worktree),
                "task_id": state.task_id,
            }
        )
    queue = [
        {
            "package_id": state.package_id,
            "name": state.name,
            "branch": state.branch,
            "head": state.head,
            "task_id": state.task_id,
        }
        for state in states
        if state.state == "integration_ready"
    ]
    queue.sort(key=lambda item: str(item["package_id"]))
    waiting = [
        {
            "package_id": state.package_id,
            "unmet_dependencies": list(state.unmet_dependencies),
        }
        for state in states
        if state.state in {"waiting", "integration_blocked"}
    ]
    return {
        "worker_assignments": assignments,
        "integration_queue": queue,
        "waiting": waiting,
        "available_workers": available,
    }
