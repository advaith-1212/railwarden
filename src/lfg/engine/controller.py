from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lfg.config.models import ProjectConfig, ProjectFiles, WorkPackage
from lfg.errors import GitError, ValidationError
from lfg.git import branch_exists, head, is_clean
from lfg.integration.manager import integrate_one
from lfg.planning.pipeline import plan_is_approved
from lfg.processes.supervisor import launch_managed, process_alive
from lfg.providers.adapters import ProviderAdapter, default_adapters
from lfg.providers.health import (
    is_available,
    load_state,
    record_failure,
    record_success,
    refresh_state,
)
from lfg.provisioning.worktrees import ensure_worktree
from lfg.runtime.checkpoints import CheckpointResult, create_checkpoint_commit
from lfg.runtime.events import append_event
from lfg.runtime.handoff import create_handoff_packet
from lfg.runtime.quota import quota_allows_start
from lfg.runtime.session import (
    AgentInstance,
    load_session_profile,
    save_session_profile,
    update_agent,
)
from lfg.runtime.skills import load_skills
from lfg.runtime.tasks import (
    ensure_task,
    load_tasks,
    save_tasks,
    task_id_for_package,
    transition_task,
)
from lfg.scheduler.classifier import classify_packages, package_branch, package_worktree
from lfg.validation.worker_result import load_worker_result, validate_completed_package

TERMINAL_STATES = {"merged", "blocked", "failed"}
ACTIVE_STATES = {"assigned", "running", "validating", "integrating"}


def _current_goal(config: ProjectConfig) -> str:
    state_path = config.runtime_directory / "state" / "pending-plan.json"
    if state_path.exists():
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return str(payload.get("goal", ""))
    runs_dir = config.runtime_directory / "runs"
    if not runs_dir.exists():
        return ""
    goals = sorted(runs_dir.glob("*/goal.md"))
    return goals[-1].read_text(encoding="utf-8").strip() if goals else ""


def _task_by_id(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(task.get("id")): task for task in tasks if task.get("id")}


def _process_path(config: ProjectConfig, task_id: str) -> Path:
    return config.runtime_directory / "processes" / f"{task_id}.json"


def _result_path(config: ProjectConfig, task_id: str) -> Path:
    return config.runtime_directory / "results" / f"{task_id}.json"


def _log_path(config: ProjectConfig, task_id: str, attempt: int, provider: str) -> Path:
    return config.runtime_directory / "logs" / f"{task_id}-{attempt}-{provider}.log"


def _prompt_path(config: ProjectConfig, task_id: str, attempt: int) -> Path:
    return config.runtime_directory / "prompts" / f"{task_id}-{attempt}.md"


def _write_prompt(
    config: ProjectConfig,
    package: WorkPackage,
    task: dict[str, Any],
    workspace: Path,
    result_path: Path,
) -> Path:
    template = config.repository_root / "templates" / "worker_task_prompt.md"
    if template.exists():
        base = template.read_text(encoding="utf-8")
    else:
        pkg_template = Path(__file__).parents[3] / "templates" / "worker_task_prompt.md"
        if pkg_template.exists():
            base = pkg_template.read_text(encoding="utf-8")
        else:
            base = "# LFG Worker Task\n"
    attempt = int(task.get("attempt", 0))
    path = _prompt_path(config, str(task["id"]), attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    skills = load_skills(config)
    skill_refs = "\n".join(
        f"- {skill.name}: {skill.path} ({'runtime' if skill.runtime_only else 'project'})"
        for skill in skills
    )
    skill_text = "\n\n".join(
        f"## Skill: {skill.name}\n\n{skill.text.rstrip()}" for skill in skills
    )
    body = f"""{base.rstrip()}

Task id: {task["id"]}
Package id: {package.package_id}
Name: {package.name}
Objective:
{package.objective}

Owned paths:
{chr(10).join(f"- {item}" for item in package.owned_paths) or "- none"}

Forbidden paths:
{chr(10).join(f"- {item}" for item in package.forbidden_paths) or "- none"}

Acceptance tests:
{chr(10).join(f"- {item}" for item in package.acceptance_tests) or "- none"}

Workspace: {workspace}
Expected result JSON path: {result_path}

Available skills:
{skill_refs or "- none"}

LFG MCP routing:
- Agents with native MCP support should use the generated LFG MCP config.
- Agents without native MCP support must ask Hermes/LFG to execute MCP operations.

Skill text:
{skill_text or "No project or runtime skills are currently defined."}

Write the structured worker result JSON to the expected path before exiting.
"""
    path.write_text(body, encoding="utf-8")
    return path


def eligible_providers(
    config: ProjectConfig,
    package: WorkPackage,
    *,
    exclude: set[str] | None = None,
) -> list[str]:
    excluded = exclude or set()
    profile = load_session_profile(config)
    paused_adapters = {
        agent.executor_adapter
        for agent in profile.agents
        if agent.state in {"paused", "rate_limited", "unavailable"}
    }
    preferred = [
        item for item in package.preferred_providers if item in config.worker_providers
    ]
    remaining = [item for item in config.worker_providers if item not in preferred]
    candidates = preferred + remaining
    available = [
        name
        for name in candidates
        if name not in excluded
        and name not in paused_adapters
        and is_available(config.runtime_directory, name)
    ]
    return sorted(
        available,
        key=lambda name: (
            config.provider_configs[name].priority
            if name in config.provider_configs
            else 999
        ),
    )


def hydrate_task_state(files: ProjectFiles) -> list[dict[str, Any]]:
    config = files.project
    tasks = load_tasks(config.runtime_directory)
    by_id = _task_by_id(tasks)
    for package in files.packages.values():
        task = ensure_task(
            config.runtime_directory,
            package_id=package.package_id,
            name=package.name,
            dependencies=package.dependencies,
        )
        by_id[str(task["id"])] = task
    states = classify_packages(config, files.packages)
    for state in states:
        existing_task = by_id.get(task_id_for_package(state.package_id))
        if not existing_task:
            continue
        status = str(existing_task.get("status", "planned"))
        if status in ACTIVE_STATES or status in TERMINAL_STATES:
            continue
        if state.state == "merged":
            transition_task(config.runtime_directory, existing_task, "merged")
        elif state.state == "integration_ready":
            transition_task(
                config.runtime_directory, existing_task, "integration_ready"
            )
        elif state.state in {"execution_ready", "repair_required"}:
            transition_task(config.runtime_directory, existing_task, "ready")
    return load_tasks(config.runtime_directory)


def _package_for_task(files: ProjectFiles, task: dict[str, Any]) -> WorkPackage | None:
    package_id = str(task.get("package_id", ""))
    return files.packages.get(package_id)


def _handoff_or_block(
    files: ProjectFiles,
    task: dict[str, Any],
    package: WorkPackage,
    *,
    provider: str,
    failure_text: str,
    failure_kind: str,
    workspace: Path,
    branch: str,
    log_path: Path | None,
) -> dict[str, Any]:
    config = files.project
    next_providers = eligible_providers(config, package, exclude={provider})
    next_provider = next_providers[0] if next_providers else None
    checkpoint = _checkpoint_preserved_work(config, task, package, workspace)
    packet = create_handoff_packet(
        runtime_dir=config.runtime_directory,
        task=task,
        goal=_current_goal(config),
        objective=package.objective,
        workspace=workspace,
        branch=branch,
        provider=provider,
        failure_kind=failure_kind,
        log_path=log_path,
        next_provider=next_provider,
    )
    payload: dict[str, Any] = {
        "handoff_packet": str(packet),
        "last_provider": provider,
        "failure_kind": failure_kind,
        "failure": failure_text[:1000],
    }
    if checkpoint is not None:
        payload["checkpoint"] = checkpoint.to_dict()
    if next_provider:
        return transition_task(
            config.runtime_directory, task, "handoff_needed", payload
        )
    return transition_task(config.runtime_directory, task, "blocked", payload)


def _checkpoint_preserved_work(
    config: ProjectConfig,
    task: dict[str, Any],
    package: WorkPackage,
    workspace: Path,
) -> CheckpointResult | None:
    if not workspace.exists():
        return None
    try:
        return create_checkpoint_commit(
            config,
            task_id=str(task["id"]),
            workspace=workspace,
            attempt=int(task.get("attempt", 0)),
            allowed_paths=package.owned_paths,
        )
    except GitError as exc:
        append_event(
            config.runtime_directory,
            "checkpoint_skipped",
            {"reason": str(exc), "workspace": str(workspace)},
            task_id=str(task.get("id")),
        )
        return None


def reconcile_processes(files: ProjectFiles) -> list[dict[str, Any]]:
    config = files.project
    tasks = load_tasks(config.runtime_directory)
    for task in tasks:
        status = str(task.get("status", ""))
        if status not in ACTIVE_STATES:
            continue
        process_path = _process_path(config, str(task["id"]))
        if not process_path.exists():
            continue
        process = json.loads(process_path.read_text(encoding="utf-8"))
        if not isinstance(process, dict):
            continue
        pid = int(process.get("pid", 0))
        if pid > 0 and process_alive(pid):
            continue
        package = _package_for_task(files, task)
        if package is None:
            transition_task(
                config.runtime_directory, task, "failed", {"reason": "package removed"}
            )
            continue
        provider = str(task.get("provider", process.get("provider", "")))
        branch = str(task.get("branch", package_branch(package)))
        workspace = Path(str(task.get("worktree", package_worktree(config, package))))
        result_path = Path(
            str(task.get("result_path", _result_path(config, str(task["id"]))))
        )
        if result_path.exists():
            transition_task(config.runtime_directory, task, "validating")
            result = load_worker_result(result_path)
            worker_status = result.get("status")
            if worker_status == "success":
                worker_status = "completed"
                result["status"] = "completed"
            if worker_status == "completed":
                try:
                    validate_completed_package(
                        package=package,
                        result=result,
                        workspace=workspace,
                        expected_branch=branch,
                    )
                except ValidationError as exc:
                    _handoff_or_block(
                        files,
                        task,
                        package,
                        provider=provider,
                        failure_text=str(exc),
                        failure_kind="validation_failed",
                        workspace=workspace,
                        branch=branch,
                        log_path=Path(str(process.get("log_path", ""))),
                    )
                    continue
                record_success(config.runtime_directory, provider)
                transition_task(
                    config.runtime_directory,
                    task,
                    "integration_ready",
                    {"commit_hash": result.get("commit_hash")},
                )
            elif result.get("status") == "blocked":
                transition_task(
                    config.runtime_directory,
                    task,
                    "blocked",
                    {"blockers": result.get("blockers", [])},
                )
            else:
                transition_task(
                    config.runtime_directory, task, "failed", {"result": result}
                )
            continue
        log_path = Path(str(process.get("log_path", "")))
        failure_text = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.exists()
            else ""
        )
        provider_config = config.provider_configs.get(provider)
        state = record_failure(
            config.runtime_directory,
            provider,
            failure_text,
            cooldown_seconds=provider_config.cooldown_seconds
            if provider_config is not None
            else 3600,
        )
        dirty_or_branch = workspace.exists() and (
            not is_clean(workspace) or branch_exists(config.repository_root, branch)
        )
        if dirty_or_branch and config.execution_preserve_partial_work_on_handoff:
            _handoff_or_block(
                files,
                task,
                package,
                provider=provider,
                failure_text=failure_text,
                failure_kind=state.failure_kind or "adapter_failure",
                workspace=workspace,
                branch=branch,
                log_path=log_path,
            )
        else:
            transition_task(
                config.runtime_directory,
                task,
                "blocked" if state.status == "needs_auth" else "failed",
                {"failure_kind": state.failure_kind, "failure": failure_text[:1000]},
            )
    return load_tasks(config.runtime_directory)


def launch_task(
    files: ProjectFiles,
    package: WorkPackage,
    task: dict[str, Any],
    provider: str,
    adapters: dict[str, ProviderAdapter],
    *,
    launch: bool,
) -> dict[str, Any]:
    config = files.project
    adapter = _adapter_for_session_agent(files, provider, adapters[provider])
    branch = package_branch(package)
    workspace = package_worktree(config, package)
    action = "repair" if str(task.get("status")) == "handoff_needed" else "execute"
    ensure_worktree(
        repository=config.repository_root,
        integration_branch=config.integration_branch,
        workspace=workspace,
        branch=branch,
        action=action,
    )
    attempt = int(task.get("attempt", 0)) + 1
    task["attempt"] = attempt
    result_path = _result_path(config, str(task["id"]))
    prompt_path = _write_prompt(config, package, task, workspace, result_path)
    log_path = _log_path(config, str(task["id"]), attempt, provider)
    command = adapter.launch_command(workspace, prompt_path, result_path)
    payload = {
        "provider": provider,
        "branch": branch,
        "worktree": str(workspace),
        "prompt_path": str(prompt_path),
        "result_path": str(result_path),
        "log_path": str(log_path),
    }
    transition_task(config.runtime_directory, task, "assigned", payload)
    agent = _agent_for_provider(files, provider)
    if agent is not None:
        _set_agent_runtime_state(
            config,
            agent,
            state="running" if launch else "ready",
            active_task=str(task["id"]),
        )
    if launch:
        managed = launch_managed(
            command,
            cwd=workspace,
            log_path=log_path,
            pid_path=_process_path(config, str(task["id"])),
        )
        process_payload = {
            "pid": managed.pid,
            "pgid": managed.pgid,
            "command": list(managed.command),
            "log_path": str(managed.log_path),
            "provider": provider,
        }
        process_path = _process_path(config, str(task["id"]))
        process_path.write_text(json.dumps(process_payload, indent=2), encoding="utf-8")
        transition_task(config.runtime_directory, task, "running", {"pid": managed.pid})
    append_event(
        config.runtime_directory,
        "task_launched" if launch else "task_launch_planned",
        {"provider": provider, "package_id": package.package_id},
        task_id=str(task["id"]),
    )
    return task


def _adapter_for_session_agent(
    files: ProjectFiles, provider: str, adapter: ProviderAdapter
) -> ProviderAdapter:
    agent = _agent_for_provider(files, provider)
    if agent is None:
        return adapter
    return ProviderAdapter(
        name=adapter.name,
        executable=adapter.executable,
        model=agent.model_profile.model,
        reasoning_effort=agent.model_profile.reasoning_effort,
    )


def _agent_for_provider(files: ProjectFiles, provider: str) -> AgentInstance | None:
    profile = load_session_profile(files.project)
    for agent in profile.agents:
        if agent.executor_adapter == provider:
            return agent
    for agent in profile.agents:
        if agent.model_profile.provider == provider:
            return agent
    return None


def _set_agent_runtime_state(
    config: ProjectConfig,
    agent: AgentInstance,
    *,
    state: str,
    active_task: str | None,
) -> AgentInstance:
    updated = AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=agent.model_profile,
        executor_adapter=agent.executor_adapter,
        state=state,  # type: ignore[arg-type]
        quota_policy=agent.quota_policy,
        active_task=active_task,
    )
    profile = load_session_profile(config)
    save_session_profile(config, update_agent(profile, updated))
    return updated


def _pause_for_quota(
    files: ProjectFiles,
    task: dict[str, Any],
    package: WorkPackage,
    provider: str,
    agent: AgentInstance,
    quota_payload: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    config = files.project
    paused = _set_agent_runtime_state(
        config,
        agent,
        state="paused",
        active_task=str(task["id"]),
    )
    branch = str(task.get("branch", package_branch(package)))
    workspace = Path(str(task.get("worktree", package_worktree(config, package))))
    checkpoint = _checkpoint_preserved_work(config, task, package, workspace)
    packet = create_handoff_packet(
        runtime_dir=config.runtime_directory,
        task=task,
        goal=_current_goal(config),
        objective=package.objective,
        workspace=workspace,
        branch=branch,
        provider=provider,
        failure_kind=reason,
        log_path=Path(str(task["log_path"])) if task.get("log_path") else None,
        next_provider=None,
    )
    payload: dict[str, Any] = {
        "reason": reason,
        "provider": provider,
        "quota": quota_payload,
        "agent_id": paused.agent_id,
        "handoff_packet": str(packet),
        "failure_kind": reason,
    }
    if checkpoint is not None:
        payload["checkpoint"] = checkpoint.to_dict()
    transition_task(config.runtime_directory, task, "handoff_needed", payload)
    append_event(
        config.runtime_directory,
        "agent_paused_for_quota",
        {
            "agent_id": agent.agent_id,
            "provider": provider,
            "remaining_percent": quota_payload.get("remaining_percent"),
            "handoff_packet": str(packet),
        },
        task_id=str(task["id"]),
    )
    return task


def controller_tick(
    files: ProjectFiles,
    *,
    adapters: dict[str, ProviderAdapter] | None = None,
    launch: bool = True,
    integrate: bool = True,
) -> dict[str, Any]:
    config = files.project
    if not plan_is_approved(config):
        append_event(config.runtime_directory, "controller_waiting_for_plan_approval")
        return {"status": "waiting_for_plan_approval", "launched": []}
    adapters = adapters or default_adapters()
    for provider in config.worker_providers:
        state = refresh_state(load_state(config.runtime_directory, provider))
        if state.status == "probe":
            from lfg.providers.health import save_state

            save_state(config.runtime_directory, state)
    hydrate_task_state(files)
    reconcile_processes(files)
    integrated: dict[str, Any] | None = None
    if integrate:
        candidates = integration_candidates(files)
        if candidates:
            candidate = candidates[0]
            task_id = str(candidate.get("task_id") or "")
            tasks_by_id = _task_by_id(load_tasks(config.runtime_directory))
            task = tasks_by_id.get(task_id)
            if task is not None:
                transition_task(config.runtime_directory, task, "integrating")
            try:
                integrated = integrate_one(
                    config=config,
                    candidate=candidate,
                    validation_commands=files.validation,
                    execute=True,
                )
            except (GitError, ValidationError) as exc:
                if task is not None:
                    transition_task(
                        config.runtime_directory,
                        task,
                        "failed",
                        {"reason": str(exc)},
                    )
            else:
                if task is not None:
                    transition_task(
                        config.runtime_directory,
                        task,
                        "merged",
                        {"integration": integrated},
                    )
    tasks = load_tasks(config.runtime_directory)
    running = sum(1 for task in tasks if str(task.get("status")) in ACTIVE_STATES)
    slots = max(config.worker_concurrency - running, 0)
    launched: list[dict[str, Any]] = []
    if slots == 0:
        return {"status": "ok", "launched": launched, "integrated": integrated}
    for task in tasks:
        if slots <= 0:
            break
        if str(task.get("status")) not in {"ready", "handoff_needed", "cooldown_wait"}:
            continue
        package = _package_for_task(files, task)
        if package is None:
            transition_task(
                config.runtime_directory,
                task,
                "failed",
                {"reason": "package removed"},
            )
            continue
        providers = [
            name for name in eligible_providers(config, package) if name in adapters
        ]
        provider_override = str(task.get("provider_override") or "")
        if provider_override in providers:
            providers = [
                provider_override,
                *[name for name in providers if name != provider_override],
            ]
        if not providers:
            transition_task(
                config.runtime_directory,
                task,
                "blocked",
                {"reason": "no eligible provider"},
            )
            continue
        provider = providers[0]
        agent = _agent_for_provider(files, provider)
        if agent is not None:
            allowed, quota, reason = quota_allows_start(config.runtime_directory, agent)
            if not allowed:
                _pause_for_quota(
                    files,
                    task,
                    package,
                    provider,
                    agent,
                    quota.__dict__,
                    reason,
                )
                continue
        try:
            launch_task(files, package, task, provider, adapters, launch=launch)
        except (GitError, RuntimeError) as exc:
            transition_task(
                config.runtime_directory,
                task,
                "blocked",
                {"reason": str(exc), "provider": provider},
            )
            continue
        launched.append({"task_id": task["id"], "provider": provider})
        slots -= 1
    return {"status": "ok", "launched": launched, "integrated": integrated}


def integration_candidates(files: ProjectFiles) -> list[dict[str, Any]]:
    states = classify_packages(files.project, files.packages)
    candidates = [
        {
            "package_id": state.package_id,
            "name": state.name,
            "branch": state.branch,
            "head": state.head or head(files.project.repository_root, state.branch),
            "task_id": state.task_id,
        }
        for state in states
        if state.state == "integration_ready"
    ]
    return sorted(candidates, key=lambda item: str(item["package_id"]))


def save_all_tasks(config: ProjectConfig, tasks: list[dict[str, Any]]) -> None:
    save_tasks(config.runtime_directory, tasks)
