from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lfg.config.models import (
    ProjectConfig,
    ProjectFiles,
    ProviderConfig,
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


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "project"


def load_project_config(repository_root: Path) -> ProjectConfig:
    config_path = repository_root / ".lfg" / "factory.yaml"
    if not config_path.exists():
        config_path = repository_root / ".lfg" / "project.yaml"
    payload = _load_yaml(config_path)
    project = payload.get("project")
    if not isinstance(project, dict):
        raise ConfigurationError("project.yaml must define project mapping")
    planning = payload.get("planning", {})
    hermes = payload.get("hermes", {})
    providers = payload.get("providers", {})
    workers = payload.get("workers", {})
    execution = payload.get("execution", {})
    monitoring = payload.get("monitoring", {})
    runtime = payload.get("runtime", {})
    if (
        not isinstance(planning, dict)
        or not isinstance(hermes, dict)
        or not isinstance(providers, dict)
        or not isinstance(workers, dict)
        or not isinstance(execution, dict)
        or not isinstance(monitoring, dict)
        or not isinstance(runtime, dict)
    ):
        raise ConfigurationError("Invalid project.yaml structure")
    providers_raw = workers.get("providers", ["codex", "antigravity", "composer"])
    if not isinstance(providers_raw, list):
        raise ConfigurationError("workers.providers must be a list")
    worker_provider_names = tuple(str(item) for item in providers_raw)
    provider_configs: dict[str, ProviderConfig] = {}
    for index, name in enumerate(worker_provider_names):
        raw = providers.get(name, {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ConfigurationError(f"providers.{name} must be a mapping")
        capabilities = raw.get("capabilities", [])
        if not isinstance(capabilities, list):
            raise ConfigurationError(f"providers.{name}.capabilities must be a list")
        provider_configs[name] = ProviderConfig(
            name=name,
            priority=int(raw.get("priority", (index + 1) * 10)),
            capabilities=tuple(str(item) for item in capabilities),
            cooldown_seconds=int(raw.get("cooldown_seconds", 3600)),
        )
    name = str(project.get("name") or repository_root.name)
    default_model = str(planning.get("model", "Claude Opus 4.6 (Thinking)"))
    hermes_fallback = hermes.get("fallback_model")
    profile_map_raw = hermes.get("profile_map", {})
    if not isinstance(profile_map_raw, dict):
        raise ConfigurationError("hermes.profile_map must be a mapping")
    board = str(project.get("board", f"lfg-{name}"))
    hermes_board = str(hermes.get("board", board))
    hermes_project_slug = str(hermes.get("project_slug", _slug(name)))
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
        board=board,
        planner_provider=str(planning.get("provider", "antigravity")),
        planner_model=default_model,
        planner_fallback_allowed=bool(
            planning.get("allow_fallback", hermes.get("allow_fallback", False))
        ),
        worker_concurrency=int(workers.get("concurrency", 3)),
        worker_providers=worker_provider_names,
        provider_configs=provider_configs,
        hermes_primary_model=str(hermes.get("primary_model", default_model)),
        hermes_fallback_model=str(hermes_fallback) if hermes_fallback else None,
        hermes_allow_fallback=bool(
            hermes.get("allow_fallback", planning.get("allow_fallback", False))
        ),
        hermes_board=hermes_board,
        hermes_project_slug=hermes_project_slug,
        hermes_orchestrator_profile=str(hermes["orchestrator_profile"])
        if hermes.get("orchestrator_profile")
        else None,
        hermes_default_assignee=str(hermes.get("default_assignee", "default")),
        hermes_profile_map={
            str(key): str(value) for key, value in profile_map_raw.items()
        },
        hermes_workspace_mode=str(hermes.get("workspace_mode", "worktree")),
        execution_require_plan_approval=bool(
            execution.get(
                "require_plan_approval", planning.get("approval_required", True)
            )
        ),
        execution_preserve_partial_work_on_handoff=bool(
            execution.get("preserve_partial_work_on_handoff", True)
        ),
        monitoring_git_graph=bool(monitoring.get("git_graph", True)),
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
            acceptance_criteria=tuple(
                str(item) for item in raw.get("acceptance_criteria", [])
            ),
            validation_commands=_load_validation_commands(
                raw.get("validation_commands", raw.get("validation", [])),
                source=f"work_packages.{package_id}.validation_commands",
            ),
            preferred_providers=tuple(
                str(item) for item in raw.get("preferred_providers", [])
            ),
            model_profile=str(raw["model_profile"])
            if raw.get("model_profile") is not None
            else None,
            reviewer_profile=str(raw["reviewer_profile"])
            if raw.get("reviewer_profile") is not None
            else None,
            risk_level=str(raw.get("risk_level", "medium")),
            context_refs=tuple(str(item) for item in raw.get("context_refs", [])),
            merge_policy=str(raw.get("merge_policy", "auto_after_review")),
            approval_required=bool(raw.get("approval_required", False)),
            review_required=bool(raw.get("review_required", True)),
            branch=str(raw["branch"]) if raw.get("branch") else None,
            worktree=Path(str(raw["worktree"])) if raw.get("worktree") else None,
            status_notes=str(raw["status_notes"])
            if raw.get("status_notes") is not None
            else None,
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
    return _load_validation_commands(raw_commands, source="validation.commands")


def _load_validation_commands(
    raw_commands: object, *, source: str
) -> tuple[ValidationCommand, ...]:
    if raw_commands in (None, ""):
        return ()
    if not isinstance(raw_commands, list):
        raise ConfigurationError(f"{source} must be a list")
    commands: list[ValidationCommand] = []
    for index, raw in enumerate(raw_commands):
        if isinstance(raw, str):
            raw = {"name": f"validation-{index + 1}", "command": raw}
        if not isinstance(raw, dict):
            raise ConfigurationError(f"Invalid validation command in {source}[{index}]")
        command = raw.get("command")
        if isinstance(command, dict):
            cwd = command.get("cwd", ".")
            argv = command.get("argv", [])
        else:
            cwd = raw.get("cwd", ".")
            argv = raw.get("argv", raw.get("command", []))
        if isinstance(argv, str):
            argv = argv.split()
        if not isinstance(argv, list) or not argv:
            raise ConfigurationError(f"{source}[{index}] requires argv list")
        outputs = raw.get("generated_outputs", [])
        if not isinstance(outputs, list):
            raise ConfigurationError(
                f"{source}[{index}].generated_outputs must be a list"
            )
        commands.append(
            ValidationCommand(
                name=str(raw.get("name", f"validation-{index + 1}")),
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
