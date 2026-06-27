from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ValidationCommand:
    name: str
    cwd: Path
    argv: tuple[str, ...]
    required: bool = True
    generated_outputs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class WorkPackage:
    package_id: str
    name: str
    objective: str
    dependencies: tuple[str, ...] = ()
    owned_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    acceptance_tests: tuple[str, ...] = ()
    preferred_providers: tuple[str, ...] = ()
    branch: str | None = None
    worktree: Path | None = None
    status_notes: str | None = None


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    priority: int
    capabilities: tuple[str, ...] = ()
    cooldown_seconds: int = 3600


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    repository_root: Path
    integration_branch: str
    worktree_root: Path
    runtime_directory: Path
    board: str
    planner_provider: str
    planner_model: str
    planner_fallback_allowed: bool
    worker_concurrency: int
    worker_providers: tuple[str, ...]
    provider_configs: dict[str, ProviderConfig] = field(default_factory=dict)
    hermes_primary_model: str = "Claude Opus 4.6 (Thinking)"
    hermes_fallback_model: str | None = None
    hermes_allow_fallback: bool = False
    execution_require_plan_approval: bool = True
    execution_preserve_partial_work_on_handoff: bool = True
    monitoring_git_graph: bool = True


ProviderStatus = Literal[
    "healthy",
    "degraded",
    "cooldown",
    "unavailable",
    "needs_auth",
    "probe",
]


@dataclass
class ProviderState:
    name: str
    status: ProviderStatus = "healthy"
    failure_kind: str | None = None
    failure_count: int = 0
    cooldown_until: float | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None
    last_error: str | None = None
    matched_pattern: str | None = None


@dataclass(frozen=True)
class WorkerResult:
    schema_version: str
    task_id: str
    worker: str
    model: str
    status: str
    summary: str
    workspace: Path
    branch: str | None
    commit_hash: str | None
    changed_files: tuple[str, ...]
    tests: tuple[dict[str, str], ...]
    blockers: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanDiff:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed_dependencies: tuple[str, ...] = ()
    changed_scope: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectFiles:
    project: ProjectConfig
    packages: dict[str, WorkPackage] = field(default_factory=dict)
    validation: tuple[ValidationCommand, ...] = ()
