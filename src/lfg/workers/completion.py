from __future__ import annotations

from pathlib import Path
from typing import Any

from lfg.config.models import ProjectConfig, WorkPackage
from lfg.git import current_branch, head, tracked_is_clean
from lfg.providers.health import classify_failure
from lfg.runtime.results import normalize_result
from lfg.validation.package import run_package_validation
from lfg.validation.worker_result import load_worker_result, validate_completed_package


def classify_process_failure(log_text: str) -> str:
    kind, _, _, _ = classify_failure(log_text)
    return kind


def complete_worker_task(
    config: ProjectConfig,
    *,
    task: dict[str, Any],
    package: WorkPackage,
    workspace: Path,
    branch: str,
    provider: str,  # noqa: ARG001
    log_text: str,
    result_path: Path,
    runtime_result_path: Path,
) -> dict[str, Any]:
    if result_path.exists() or runtime_result_path.exists():
        path = runtime_result_path if runtime_result_path.exists() else result_path
        if result_path.exists() and not runtime_result_path.exists():
            normalized = normalize_result(config, task=task, package=package)
            path = Path(str(normalized["path"]))
        result = load_worker_result(path)
        validate_completed_package(
            package=package,
            result=result,
            workspace=workspace,
            expected_branch=branch,
        )
        validation = run_package_validation(
            config, package, workspace, str(result.get("commit_hash") or head(workspace))
        )
        return {
            "status": "validated",
            "result_path": str(path),
            "validation": validation,
        }

    has_commit = (
        workspace.exists()
        and tracked_is_clean(workspace)
        and head(workspace) != head(config.repository_root, branch)
    )
    if has_commit:
        normalized = normalize_result(config, task=task, package=package)
        path = Path(str(normalized["path"]))
        result = load_worker_result(path)
        validate_completed_package(
            package=package,
            result=result,
            workspace=workspace,
            expected_branch=branch,
        )
        validation = run_package_validation(
            config, package, workspace, str(result.get("commit_hash") or head(workspace))
        )
        return {
            "status": "validated",
            "result_path": str(path),
            "validation": validation,
            "normalized": True,
        }

    failure_kind = classify_process_failure(log_text)
    return {
        "status": "failed",
        "failure_kind": failure_kind,
        "commit_exists": has_commit,
        "result_json_exists": False,
        "workspace": str(workspace),
        "branch": current_branch(workspace) if workspace.exists() else branch,
        "failure": log_text[:1000],
    }