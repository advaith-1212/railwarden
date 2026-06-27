from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lfg.config.models import (
    ProjectConfig,
    ProjectFiles,
    ValidationCommand,
    WorkPackage,
)
from lfg.errors import ConfigurationError
from lfg.git import discover_repo


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigurationError(f"Missing configuration file: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigurationError(f"{path} must contain a mapping")
    return payload


def _path(root: Path, value: str | None, default: str) -> Path:
    text = value or default
    if text == "auto":
        text = default
    path = Path(text)
    return path if path.is_absolute() else root / path


def load_project_config(repository_root: Path) -> ProjectConfig:
    payload = _load_yaml(repository_root / ".lfg" / "project.yaml")
    project = payload.get("project")
    if not isinstance(project, dict):
        raise ConfigurationError("project.yaml must define project mapping")
    planning = payload.get("planning", {})
    workers = payload.get("workers", {})
    runtime = payload.get("runtime", {})
    if (
        not isinstance(planning, dict)
        or not isinstance(workers, dict)
        or not isinstance(runtime, dict)
    ):
        raise ConfigurationError("Invalid project.yaml structure")
    providers_raw = workers.get("providers", ["codex", "antigravity", "composer"])
    if not isinstance(providers_raw, list):
        raise ConfigurationError("workers.providers must be a list")
    name = str(project.get("name") or repository_root.name)
    return ProjectConfig(
        name=name,
        repository_root=repository_root,
        integration_branch=str(project.get("integration_branch", "integration/lfg")),
        worktree_root=_path(
            repository_root,
            str(project.get("worktree_root", "auto")),
            "../.lfg-worktrees",
        ),
        runtime_directory=_path(
            repository_root,
            str(runtime.get("directory", ".lfg-runtime")),
            ".lfg-runtime",
        ),
        board=str(project.get("board", f"lfg-{name}")),
        planner_provider=str(planning.get("provider", "antigravity")),
        planner_model=str(planning.get("model", "Claude Opus 4.6 (Thinking)")),
        planner_fallback_allowed=bool(planning.get("allow_fallback", False)),
        worker_concurrency=int(workers.get("concurrency", 3)),
        worker_providers=tuple(str(item) for item in providers_raw),
    )


def load_work_packages(repository_root: Path) -> dict[str, WorkPackage]:
    path = repository_root / ".lfg" / "work_packages.yaml"
    if not path.exists():
        return {}
    payload = _load_yaml(path)
    raw_packages = payload.get("work_packages")
    if not isinstance(raw_packages, list):
        raise ConfigurationError("work_packages.yaml must contain work_packages list")
    packages: dict[str, WorkPackage] = {}
    for raw in raw_packages:
        if not isinstance(raw, dict):
            raise ConfigurationError("Invalid work package record")
        package_id = str(raw["id"])
        packages[package_id] = WorkPackage(
            package_id=package_id,
            name=str(raw.get("name", package_id)),
            objective=str(raw.get("objective", "")),
            dependencies=tuple(str(item) for item in raw.get("dependencies", [])),
            owned_paths=tuple(str(item) for item in raw.get("owned_paths", [])),
            forbidden_paths=tuple(str(item) for item in raw.get("forbidden_paths", [])),
            acceptance_tests=tuple(
                str(item) for item in raw.get("acceptance_tests", [])
            ),
            branch=str(raw["branch"]) if raw.get("branch") else None,
            worktree=Path(str(raw["worktree"])) if raw.get("worktree") else None,
        )
    return packages


def load_validation(repository_root: Path) -> tuple[ValidationCommand, ...]:
    path = repository_root / ".lfg" / "validation.yaml"
    if not path.exists():
        return ()
    payload = _load_yaml(path)
    raw_commands = payload.get("commands", [])
    if not isinstance(raw_commands, list):
        raise ConfigurationError("validation commands must be a list")
    commands: list[ValidationCommand] = []
    for raw in raw_commands:
        if not isinstance(raw, dict):
            raise ConfigurationError("Invalid validation command")
        command = raw.get("command")
        if isinstance(command, dict):
            cwd = command.get("cwd", ".")
            argv = command.get("argv", [])
        else:
            cwd = raw.get("cwd", ".")
            argv = raw.get("argv", raw.get("command", []))
        if not isinstance(argv, list) or not argv:
            raise ConfigurationError("Validation command requires argv list")
        outputs = raw.get("generated_outputs", [])
        if not isinstance(outputs, list):
            raise ConfigurationError("generated_outputs must be a list")
        commands.append(
            ValidationCommand(
                name=str(raw["name"]),
                cwd=Path(str(cwd)),
                argv=tuple(str(item) for item in argv),
                required=bool(raw.get("required", True)),
                generated_outputs=tuple(Path(str(item)) for item in outputs),
            )
        )
    return tuple(commands)


def load_project_files(start: Path) -> ProjectFiles:
    root = discover_repo(start)
    project = load_project_config(root)
    return ProjectFiles(
        project=project,
        packages=load_work_packages(root),
        validation=load_validation(root),
    )
