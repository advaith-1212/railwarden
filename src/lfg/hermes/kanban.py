from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lfg.config.models import (
    ProjectConfig,
    ProjectFiles,
    ValidationCommand,
    WorkPackage,
)
from lfg.errors import LfgError
from lfg.hermes.profile import hermes_executable
from lfg.scheduler.classifier import package_branch
from lfg.validation.package import commands_for_package


@dataclass(frozen=True)
class HermesCommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class KanbanTaskPlan:
    package_id: str
    title: str
    body: str
    assignee: str | None
    workspace: str
    branch: str
    idempotency_key: str
    dependencies: tuple[str, ...]
    skills: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class KanbanLinkPlan:
    parent_package_id: str
    child_package_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class KanbanImportPlan:
    board: str
    project_slug: str | None
    tasks: tuple[KanbanTaskPlan, ...]
    links: tuple[KanbanLinkPlan, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "board": self.board,
            "project_slug": self.project_slug,
            "tasks": [task.to_dict() for task in self.tasks],
            "links": [link.to_dict() for link in self.links],
        }


@dataclass(frozen=True)
class BootstrapPlan:
    board: str
    project_slug: str
    repository: Path
    required_profiles: tuple[str, ...]
    external_lanes: tuple[str, ...]
    actions: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["repository"] = str(self.repository)
        return payload


class HermesAdapter:
    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or hermes_executable()

    def run(
        self,
        args: list[str],
        *,
        check: bool = True,
        timeout: float = 30.0,
    ) -> HermesCommandResult:
        if self.executable is None:
            raise LfgError("Hermes executable is not installed.")
        command = [self.executable, *args]
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        result = HermesCommandResult(
            command=tuple(command),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise LfgError(f"Hermes command failed: {' '.join(command)}\n{detail}")
        return result

    def json(
        self,
        args: list[str],
        *,
        check: bool = True,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        result = self.run(args, check=check, timeout=timeout)
        text = result.stdout.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LfgError(
                f"Hermes did not return JSON for {' '.join(result.command)}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise LfgError("Hermes JSON output must be an object.")
        return payload


def hermes_board(config: ProjectConfig) -> str:
    return config.hermes_board or config.board


def hermes_project_slug(config: ProjectConfig) -> str:
    return config.hermes_project_slug or _slug(config.name)


def required_profiles(config: ProjectConfig) -> tuple[str, ...]:
    names = {
        config.hermes_default_assignee,
        *config.hermes_profile_map.values(),
    }
    if config.hermes_orchestrator_profile:
        names.add(config.hermes_orchestrator_profile)
    return tuple(sorted(item for item in names if item))


def external_lane_profiles(config: ProjectConfig) -> tuple[str, ...]:
    return tuple(item for item in required_profiles(config) if not _profile_name(item))


def build_import_plan(files: ProjectFiles) -> KanbanImportPlan:
    tasks = tuple(
        _task_plan(files.project, package)
        for package in sorted(files.packages.values(), key=lambda item: item.package_id)
    )
    links = tuple(
        KanbanLinkPlan(
            parent_package_id=dependency, child_package_id=package.package_id
        )
        for package in sorted(files.packages.values(), key=lambda item: item.package_id)
        for dependency in package.dependencies
    )
    return KanbanImportPlan(
        board=hermes_board(files.project),
        project_slug=hermes_project_slug(files.project),
        tasks=tasks,
        links=links,
    )


def bootstrap_plan(config: ProjectConfig) -> BootstrapPlan:
    board = hermes_board(config)
    project_slug = hermes_project_slug(config)
    profiles = required_profiles(config)
    external = external_lane_profiles(config)
    profile_actions = tuple(
        f"verify Hermes profile `{profile}`"
        if profile in external
        else f"ensure Hermes profile `{profile}`"
        for profile in profiles
    )
    return BootstrapPlan(
        board=board,
        project_slug=project_slug,
        repository=config.repository_root,
        required_profiles=profiles,
        external_lanes=external,
        actions=(
            f"ensure Hermes Kanban board `{board}`",
            f"ensure Hermes project `{project_slug}` bound to `{board}`",
            *profile_actions,
            "verify Hermes gateway dispatcher",
        ),
    )


def hermes_status(config: ProjectConfig, adapter: HermesAdapter) -> dict[str, object]:
    version = _safe_text(adapter, ["--version"], timeout=10.0)
    return {
        "version": version,
        "update_available": "Update available" in version,
        "gateway": _safe_text(adapter, ["gateway", "status"], timeout=15.0),
        "boards": _safe_text(adapter, ["kanban", "boards", "list"], timeout=15.0),
        "current_board": hermes_board(config),
        "project_slug": hermes_project_slug(config),
        "projects": _safe_text(adapter, ["project", "list"], timeout=15.0),
        "profiles": _safe_text(adapter, ["profile", "list"], timeout=15.0),
        "diagnostics": _safe_text(
            adapter,
            ["kanban", "--board", hermes_board(config), "diagnostics"],
            timeout=15.0,
        ),
    }


def apply_bootstrap(config: ProjectConfig, adapter: HermesAdapter) -> BootstrapPlan:
    plan = bootstrap_plan(config)
    adapter.run(
        [
            "kanban",
            "boards",
            "create",
            plan.board,
            "--name",
            config.name,
            "--description",
            f"LFG companion board for {config.repository_root}",
            "--switch",
        ],
        check=False,
    )
    adapter.run(
        [
            "project",
            "create",
            config.name,
            str(config.repository_root),
            "--slug",
            plan.project_slug,
            "--primary",
            str(config.repository_root),
            "--board",
            plan.board,
            "--use",
        ],
        check=False,
    )
    adapter.run(
        ["project", "bind-board", plan.project_slug, plan.board],
        check=False,
    )
    for profile in plan.required_profiles:
        if profile in plan.external_lanes:
            continue
        adapter.run(
            [
                "profile",
                "create",
                profile,
                "--clone",
                "--description",
                f"LFG Kanban worker profile for {config.name}.",
            ],
            check=False,
        )
    return plan


def apply_import_plan(
    plan: KanbanImportPlan,
    adapter: HermesAdapter,
) -> dict[str, object]:
    task_ids: dict[str, str] = {}
    created: list[dict[str, object]] = []
    for task in plan.tasks:
        payload = adapter.json(_create_args(plan, task), timeout=60.0)
        task_id = _task_id(payload)
        if task_id is None:
            raise LfgError(f"Hermes create did not return a task id for {task.title}.")
        task_ids[task.package_id] = task_id
        created.append({"package_id": task.package_id, "task_id": task_id})
    linked: list[dict[str, object]] = []
    for link in plan.links:
        parent = task_ids.get(link.parent_package_id)
        child = task_ids.get(link.child_package_id)
        if parent is None or child is None:
            raise LfgError(
                "Cannot create Hermes link for unknown package dependency: "
                f"{link.parent_package_id} -> {link.child_package_id}"
            )
        adapter.run(["kanban", "--board", plan.board, "link", parent, child])
        linked.append({"parent": parent, "child": child})
    return {"created": created, "linked": linked}


def _task_plan(config: ProjectConfig, package: WorkPackage) -> KanbanTaskPlan:
    return KanbanTaskPlan(
        package_id=package.package_id,
        title=f"{package.package_id} {package.name}",
        body=_task_body(config, package),
        assignee=_assignee(config, package),
        workspace=config.hermes_workspace_mode,
        branch=package_branch(package),
        idempotency_key=f"lfg:{config.name}:{package.package_id}",
        dependencies=package.dependencies,
        skills=_package_skills(package),
    )


def _task_body(config: ProjectConfig, package: WorkPackage) -> str:
    commands = commands_for_package(package)
    lines = [
        f"Work package: {package.package_id} - {package.name}",
        "",
        "Objective:",
        package.objective or "No objective supplied.",
        "",
        "Repository:",
        f"- {config.repository_root}",
        "",
        "Branch and workspace:",
        f"- Expected branch: {package_branch(package)}",
        f"- Workspace mode: {config.hermes_workspace_mode}",
        "",
        "Owned paths:",
        *_bullets(package.owned_paths),
        "",
        "Forbidden paths:",
        *_bullets(package.forbidden_paths),
        "",
        "Acceptance criteria:",
        *_bullets(package.acceptance_criteria),
        "",
        "Acceptance tests:",
        *_bullets(package.acceptance_tests),
        "",
        "Validation commands:",
        *_command_bullets(commands),
        "",
        "Context refs:",
        *_bullets(package.context_refs),
        "",
        "Risk and review:",
        f"- risk_level: {package.risk_level}",
        f"- merge_policy: {package.merge_policy}",
        f"- review_required: {package.review_required}",
        f"- approval_required: {package.approval_required}",
        "",
        "Commit expectation:",
        f"- Commit completed work on {package_branch(package)}.",
        "- Include changed files and validation evidence in kanban_complete metadata.",
        "",
        "Hermes/LFG boundary:",
        "- Hermes Kanban owns task lifecycle, run history, retries, and handoffs.",
        "- LFG provides this repo-specific work contract and validation policy.",
    ]
    if package.status_notes:
        lines.extend(["", "Status notes:", package.status_notes])
    return "\n".join(lines).rstrip() + "\n"


def _create_args(plan: KanbanImportPlan, task: KanbanTaskPlan) -> list[str]:
    args = [
        "kanban",
        "--board",
        plan.board,
        "create",
        task.title,
        "--body",
        task.body,
        "--workspace",
        task.workspace,
        "--branch",
        task.branch,
        "--idempotency-key",
        task.idempotency_key,
        "--json",
    ]
    if task.assignee:
        args.extend(["--assignee", task.assignee])
    if plan.project_slug:
        args.extend(["--project", plan.project_slug])
    for skill in task.skills:
        args.extend(["--skill", skill])
    return args


def _task_id(payload: dict[str, Any]) -> str | None:
    for key in ("id", "task_id"):
        if payload.get(key):
            return str(payload[key])
    task = payload.get("task")
    if isinstance(task, dict) and task.get("id"):
        return str(task["id"])
    return None


def _assignee(config: ProjectConfig, package: WorkPackage) -> str | None:
    for provider in package.preferred_providers:
        mapped = config.hermes_profile_map.get(provider)
        if mapped:
            return mapped
    return config.hermes_default_assignee or None


def _package_skills(package: WorkPackage) -> tuple[str, ...]:
    skills: list[str] = []
    for ref in package.context_refs:
        if ref.startswith("skill:"):
            skills.append(ref.removeprefix("skill:"))
    return tuple(skills)


def _bullets(items: tuple[str, ...]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- none"]


def _command_bullets(commands: tuple[ValidationCommand, ...]) -> list[str]:
    if not commands:
        return ["- none"]
    return [
        f"- {command.name}: (cd {command.cwd or Path()}; {' '.join(command.argv)})"
        for command in commands
    ]


def _safe_text(
    adapter: HermesAdapter,
    args: list[str],
    *,
    timeout: float,
) -> str:
    try:
        result = adapter.run(args, check=False, timeout=timeout)
    except (LfgError, OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    text = f"{result.stdout}\n{result.stderr}".strip()
    return text or f"exit {result.returncode}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    return slug or "project"


def _profile_name(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+", value))
