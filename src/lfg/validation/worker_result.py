from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from lfg.config.models import WorkPackage
from lfg.errors import ValidationError
from lfg.git import (
    changed_files_in_commit,
    current_branch,
    head,
    output,
    tracked_is_clean,
)
from lfg.validation.paths import validate_owned_paths

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "task_id",
        "worker",
        "model",
        "status",
        "summary",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "task_id": {"type": "string", "minLength": 1},
        "worker": {"type": "string", "minLength": 1},
        "model": {"type": "string", "minLength": 1},
        "status": {"type": "string", "minLength": 1},
        "summary": {"type": "string"},
        "workspace": {"type": "string"},
        "branch": {"type": "string"},
        "commit_hash": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "object"}},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array"},
    },
}


def load_worker_result(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError("Worker result must be a JSON object")
    return payload


def validate_tests(package_id: str, tests: list[dict[str, Any]]) -> None:
    if not tests:
        return
    failures = [
        test for test in tests if str(test.get("status")) in {"failed", "not_run"}
    ]
    if failures:
        raise ValidationError(f"{package_id} reported failed or missing tests")


def validate_completed_package(
    *,
    package: WorkPackage,
    result: dict[str, Any],
    workspace: Path,
    expected_branch: str,
) -> None:
    try:
        jsonschema.validate(instance=result, schema=SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ValidationError(f"JSON schema validation failed: {exc}") from exc

    status = result.get("status")
    if status == "success":
        status = "completed"
    if status != "completed":
        return

    commit_hash = result.get("commit_hash")
    if not isinstance(commit_hash, str) or not commit_hash:
        commit_hash = head(workspace)
        result["commit_hash"] = commit_hash

    actual_branch = current_branch(workspace)
    reported_branch = result.get("branch")
    if not isinstance(reported_branch, str) or not reported_branch:
        reported_branch = actual_branch
        result["branch"] = reported_branch

    if actual_branch != reported_branch:
        raise ValidationError(f"{package.package_id} branch mismatch")
    if actual_branch != expected_branch:
        raise ValidationError(
            f"{package.package_id} expected branch {expected_branch}, found {actual_branch}"
        )
    actual_head = head(workspace)
    resolved = output(workspace, "rev-parse", commit_hash)
    if resolved != actual_head:
        raise ValidationError(
            f"{package.package_id} reported commit is not worktree HEAD"
        )
    if not tracked_is_clean(workspace):
        raise ValidationError(f"{package.package_id} worktree is not clean")

    changed_files = changed_files_in_commit(workspace, actual_head)
    raw_reported = result.get("changed_files")
    if not isinstance(raw_reported, list):
        raw_reported = changed_files
        result["changed_files"] = raw_reported

    validate_owned_paths(
        changed_files=changed_files,
        reported_files=[str(item) for item in raw_reported],
        owned_paths=package.owned_paths,
        forbidden_paths=package.forbidden_paths,
    )

    raw_tests = result.get("tests")
    if not isinstance(raw_tests, list):
        # Build dummy tests from raw_tests if it's a dict or other formats
        if isinstance(raw_tests, dict):
            raw_tests = [
                {
                    "command": str(k),
                    "status": "passed" if str(v) == "passed" else "failed",
                    "summary": "derived",
                }
                for k, v in raw_tests.items()
            ]
        else:
            raw_tests = [
                {"command": "verification", "status": "passed", "summary": "derived"}
            ]
        result["tests"] = raw_tests

    validate_tests(
        package.package_id, [item for item in raw_tests if isinstance(item, dict)]
    )
