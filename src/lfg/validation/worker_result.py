from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from lfg.config.models import WorkPackage
from lfg.errors import ValidationError
from lfg.git import changed_files_in_commit, current_branch, head, is_clean, output
from lfg.validation.paths import validate_owned_paths

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "task_id",
        "worker",
        "model",
        "status",
        "summary",
        "workspace",
        "branch",
        "commit_hash",
        "changed_files",
        "tests",
        "blockers",
        "evidence",
    ],
    "properties": {
        "schema_version": {"const": "1.0.0"},
        "task_id": {"type": "string", "minLength": 1},
        "worker": {"type": "string", "minLength": 1},
        "model": {"type": "string", "minLength": 1},
        "status": {"enum": ["completed", "blocked", "failed"]},
        "summary": {"type": "string"},
        "workspace": {"type": "string", "minLength": 1},
        "branch": {"type": ["string", "null"]},
        "commit_hash": {"type": ["string", "null"]},
        "changed_files": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["command", "status", "summary"],
                "properties": {
                    "command": {"type": "string"},
                    "status": {"enum": ["passed", "failed", "not_run", "skipped"]},
                    "summary": {"type": "string"},
                },
            },
        },
        "blockers": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}


def load_worker_result(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValidationError("Worker result must be a JSON object")
    return payload


def validate_tests(package_id: str, tests: list[dict[str, Any]]) -> None:
    if not tests:
        raise ValidationError(f"{package_id} reported no test evidence")
    failures = [
        test for test in tests if str(test.get("status")) in {"failed", "not_run"}
    ]
    if failures:
        raise ValidationError(f"{package_id} reported failed or missing tests")
    if not any(str(test.get("status")) in {"passed", "skipped"} for test in tests):
        raise ValidationError(f"{package_id} has no acceptable test evidence")


def validate_completed_package(
    *,
    package: WorkPackage,
    result: dict[str, Any],
    workspace: Path,
    expected_branch: str,
) -> None:
    jsonschema.validate(instance=result, schema=SCHEMA)
    if result.get("status") != "completed":
        return
    commit_hash = result.get("commit_hash")
    if not isinstance(commit_hash, str) or not commit_hash:
        raise ValidationError(f"{package.package_id} completed without commit_hash")
    actual_branch = current_branch(workspace)
    if actual_branch != result.get("branch"):
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
    if not is_clean(workspace):
        raise ValidationError(f"{package.package_id} worktree is not clean")
    changed_files = changed_files_in_commit(workspace, actual_head)
    raw_reported = result.get("changed_files")
    if not isinstance(raw_reported, list):
        raise ValidationError("changed_files must be a list")
    validate_owned_paths(
        changed_files=changed_files,
        reported_files=[str(item) for item in raw_reported],
        owned_paths=package.owned_paths,
        forbidden_paths=package.forbidden_paths,
    )
    raw_tests = result.get("tests")
    if not isinstance(raw_tests, list):
        raise ValidationError("tests must be a list")
    validate_tests(
        package.package_id, [item for item in raw_tests if isinstance(item, dict)]
    )
