from __future__ import annotations

import re
import shlex
import time
from pathlib import Path

from lfg.config.models import ProjectConfig, ValidationCommand, WorkPackage
from lfg.util.atomic import atomic_write_json
from lfg.validation.runner import run_validation_suite

_COMMAND_TOKEN = re.compile(r"^[a-z0-9_./:$%+=,@-]+$")


def _looks_like_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    argv = shlex.split(stripped)
    if not argv:
        return False
    first = argv[0]
    if first.startswith(("./", "../", "/")):
        return True
    return bool(_COMMAND_TOKEN.fullmatch(first))


def commands_for_package(package: WorkPackage) -> tuple[ValidationCommand, ...]:
    if package.validation_commands:
        return package.validation_commands
    commands: list[ValidationCommand] = []
    for index, command in enumerate(package.acceptance_tests):
        if not _looks_like_command(command):
            continue
        argv = tuple(shlex.split(command))
        if not argv:
            continue
        commands.append(
            ValidationCommand(
                name=f"{package.package_id}-acceptance-{index + 1}",
                cwd=Path(),
                argv=argv,
            )
        )
    return tuple(commands)


def run_package_validation(
    config: ProjectConfig, package: WorkPackage, workspace: Path, commit_hash: str
) -> dict[str, object]:
    commands = commands_for_package(package)
    status, results, removed = run_validation_suite(workspace, commands)
    payload: dict[str, object] = {
        "schema_version": "2.0.0",
        "package_id": package.package_id,
        "commit_hash": commit_hash,
        "workspace": str(workspace),
        "status": status,
        "required": True,
        "commands": [result.to_dict() for result in results],
        "cleanup": removed,
        "created_at": time.time(),
    }
    evidence_path = (
        config.runtime_directory
        / "validation"
        / f"{package.package_id}-{commit_hash[:12]}-package.json"
    )
    atomic_write_json(evidence_path, payload)
    payload["evidence_path"] = str(evidence_path)
    return payload
