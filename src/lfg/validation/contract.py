from __future__ import annotations

from pathlib import Path
from typing import Any

from lfg.errors import ConfigurationError, LfgError
from lfg.scheduler.dag import validate_dag
from lfg.validation.commands import parse_validation_argv


def validate_packages_for_freeze(
    repository_root: Path,
    packages: list[dict[str, Any]],
    *,
    require_context_refs: bool = True,
) -> None:
    if not packages:
        raise LfgError("Cannot freeze an empty work package list")
    package_map = {
        str(item["id"]): item for item in packages if isinstance(item, dict) and item.get("id")
    }
    validate_dag_from_dicts(package_map)
    owned_registry: dict[str, str] = {}
    for package_id, package in sorted(package_map.items()):
        owned = package.get("owned_paths", [])
        if not isinstance(owned, list) or not owned:
            raise ConfigurationError(f"{package_id} requires non-empty owned_paths")
        refs = package.get("context_refs", [])
        if require_context_refs and (not isinstance(refs, list) or not refs):
            raise ConfigurationError(f"{package_id} requires at least one context_ref")
        for ref in refs if isinstance(refs, list) else []:
            path = repository_root / str(ref)
            if not path.exists():
                raise ConfigurationError(f"{package_id} context_ref missing: {ref}")
        for raw_path in owned:
            owned_path = str(raw_path)
            if (
                owned_path in owned_registry
                and owned_registry[owned_path] != package_id
            ):
                raise ConfigurationError(
                    f"Path {owned_path} owned by both "
                    f"{owned_registry[owned_path]} and {package_id}"
                )
            owned_registry[owned_path] = package_id
        _validate_package_commands(package_id, package)


def _validate_package_commands(package_id: str, package: dict[str, Any]) -> None:
    commands = package.get("validation_commands", package.get("validation", [])) or []
    if not isinstance(commands, list):
        raise ConfigurationError(f"{package_id} validation_commands must be a list")
    for index, raw in enumerate(commands):
        source = f"{package_id}.validation_commands[{index}]"
        if isinstance(raw, str):
            parse_validation_argv(raw, source=source)
        elif isinstance(raw, dict):
            command = raw.get("command", raw.get("argv"))
            argv = command.get("argv", []) if isinstance(command, dict) else command
            parse_validation_argv(argv, source=source)


def validate_dag_from_dicts(packages: dict[str, dict[str, Any]]) -> None:
    from lfg.config.models import WorkPackage

    models = {
        package_id: WorkPackage(
            package_id=package_id,
            name=str(package.get("name", package_id)),
            objective=str(package.get("objective", "")),
            dependencies=tuple(str(item) for item in package.get("dependencies", [])),
        )
        for package_id, package in packages.items()
    }
    validate_dag(models)


def infer_scaffold_owned_paths(package: dict[str, Any]) -> list[str]:
    objective = str(package.get("objective", "")).lower()
    name = str(package.get("name", "")).lower()
    owned = list(package.get("owned_paths", []))
    existing = {str(item) for item in owned}
    hints = ("scaffold", "bootstrap", "init", "vite", "react", "npm", "frontend")
    if not any(hint in objective or hint in name for hint in hints):
        return owned
    for extra in (
        "package.json",
        "package-lock.json",
        "public/",
        "index.html",
        "vite.config.ts",
        "tsconfig.json",
    ):
        if extra not in existing:
            owned.append(extra)
            existing.add(extra)
    return owned