from __future__ import annotations

from pathlib import Path
from typing import Any

from lfg.config.models import ProjectFiles, WorkPackage
from lfg.engine.controller import handoff_or_block
from lfg.runtime.results import normalize_result
from lfg.runtime.tasks import transition_task


def execute_normalize(
    files: ProjectFiles,
    *,
    task: dict[str, Any],
    package: WorkPackage,
) -> dict[str, Any]:
    normalized = normalize_result(files.project, task=task, package=package)
    return transition_task(
        files.project.runtime_directory,
        task,
        "validating",
        {"runtime_result_path": normalized["path"]},
    )


def execute_handoff(
    files: ProjectFiles,
    *,
    task: dict[str, Any],
    package: WorkPackage,
    provider: str,
    failure_text: str,
    failure_kind: str,
    workspace: Path,
    branch: str,
    log_path: Path | None,
) -> dict[str, Any]:
    return handoff_or_block(
        files,
        task,
        package,
        provider=provider,
        failure_text=failure_text,
        failure_kind=failure_kind,
        workspace=workspace,
        branch=branch,
        log_path=log_path,
    )


def execute_retry(
    files: ProjectFiles,
    *,
    task: dict[str, Any],
) -> dict[str, Any]:
    return transition_task(files.project.runtime_directory, task, "ready")