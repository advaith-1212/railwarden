from __future__ import annotations

import time
from typing import Any

from railwarden.config.models import ProjectConfig, WorkPackage
from railwarden.util.atomic import atomic_write_json
from railwarden.validation.paths import validate_owned_paths


def requires_human_merge_approval(package: WorkPackage) -> bool:
    return (
        package.approval_required
        or package.risk_level.lower() in {"high", "critical"}
        or package.merge_policy == "manual"
    )


def run_package_review(
    config: ProjectConfig,
    package: WorkPackage,
    *,
    task: dict[str, Any],
    worker_provider: str,
    reviewer_provider: str | None,
    changed_files: list[str],
    validation_evidence: dict[str, object],
) -> dict[str, object]:
    reviewer = (
        reviewer_provider or package.reviewer_profile or "railwarden-local-reviewer"
    )
    status = "passed"
    findings: list[str] = []
    if reviewer == worker_provider and package.review_required:
        status = "failed"
        findings.append("Reviewer provider must differ from worker provider.")
    if validation_evidence.get("status") != "passed":
        status = "failed"
        findings.append("Package validation has not passed.")
    try:
        validate_owned_paths(
            changed_files=changed_files,
            reported_files=changed_files,
            owned_paths=package.owned_paths,
            forbidden_paths=package.forbidden_paths,
        )
    except Exception as exc:
        status = "failed"
        findings.append(str(exc))
    payload: dict[str, object] = {
        "schema_version": "2.0.0",
        "package_id": package.package_id,
        "task_id": task.get("id"),
        "worker_provider": worker_provider,
        "reviewer_provider": reviewer,
        "status": status,
        "findings": findings,
        "validation_evidence": validation_evidence.get("evidence_path"),
        "human_merge_approval_required": requires_human_merge_approval(package),
        "created_at": time.time(),
    }
    commit_hash = str(task.get("commit_hash") or "unknown")
    evidence_path = (
        config.runtime_directory
        / "reviews"
        / f"{package.package_id}-{commit_hash[:12]}-review.json"
    )
    atomic_write_json(evidence_path, payload)
    payload["evidence_path"] = str(evidence_path)
    return payload
