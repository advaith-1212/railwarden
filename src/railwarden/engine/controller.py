from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from railwarden.compat import (
    LEGACY_RESULTS_DIR,
    PREFERRED_RESULTS_DIR,
    preferred_or_legacy_path,
)
from railwarden.config.models import ProjectConfig, ProjectFiles, WorkPackage
from railwarden.errors import GitError, ValidationError
from railwarden.git import branch_exists, head, is_clean, tracked_is_clean
from railwarden.integration.manager import integrate_one
from railwarden.planning.pipeline import plan_is_approved
from railwarden.processes.supervisor import launch_managed, process_alive
from railwarden.processes.tmux_runner import launch_tmux_managed, pane_for_worker
from railwarden.providers.adapters import ProviderAdapter, default_adapters
from railwarden.providers.health import (
    is_available,
    load_state,
    record_failure,
    record_success,
    refresh_state,
    save_state,
)
from railwarden.provisioning.worktrees import ensure_worktree
from railwarden.runtime.checkpoints import CheckpointResult, create_checkpoint_commit
from railwarden.runtime.context import context_status, resolve_context_refs
from railwarden.runtime.decisions import emit_decision_required, record_decision
from railwarden.runtime.events import append_event
from railwarden.runtime.handoff import create_handoff_packet
from railwarden.runtime.quota import quota_allows_start
from railwarden.runtime.results import normalize_result
from railwarden.runtime.session import (
    AgentInstance,
    load_session_profile,
    save_session_profile,
    update_agent,
)
from railwarden.runtime.skills import load_skills
from railwarden.runtime.tasks import (
    ensure_task,
    load_tasks,
    save_tasks,
    task_id_for_package,
    transition_task,
)
from railwarden.runtime.workflow import advance_workflow
from railwarden.scheduler.classifier import (
    classify_packages,
    package_branch,
    package_worktree,
)
from railwarden.validation.package import run_package_validation
from railwarden.validation.review import (
    requires_human_merge_approval,
    run_package_review,
)
from railwarden.validation.worker_result import (
    load_worker_result,
    validate_completed_package,
)
from railwarden.workers.completion import complete_worker_task

TERMINAL_STATES = {"merged", "blocked", "failed"}
ACTIVE_STATES = {"assigned", "running", "validating", "reviewing", "integrating"}
TMUX_STALE_SECONDS = 30 * 60


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


def _worker_result_path(workspace: Path, task_id: str) -> Path:
    result_dir = preferred_or_legacy_path(
        workspace / PREFERRED_RESULTS_DIR, workspace / LEGACY_RESULTS_DIR
    )
    return result_dir / f"{task_id}.json"


def _log_path(config: ProjectConfig, task_id: str, attempt: int, provider: str) -> Path:
    return config.runtime_directory / "logs" / f"{task_id}-{attempt}-{provider}.log"


def _prompt_path(config: ProjectConfig, task_id: str, attempt: int) -> Path:
    return config.runtime_directory / "prompts" / f"{task_id}-{attempt}.md"


def _tmux_script_path(config: ProjectConfig, task_id: str, attempt: int) -> Path:
    return config.runtime_directory / "processes" / f"{task_id}-{attempt}.sh"


def _merge_process_launch_record(
    process_path: Path, base: dict[str, Any]
) -> dict[str, Any]:
    """Merge launcher defaults with any in-pane runner updates already on disk."""
    if not process_path.exists():
        return base
    try:
        existing = json.loads(process_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(existing, dict):
        return base
    merged = dict(base)
    for key in (
        "pid",
        "pgid",
        "status",
        "returncode",
        "started_at",
        "exited_at",
        "mode",
        "pane_id",
        "command",
        "cwd",
        "stdin_path",
        "log_path",
        "provider",
    ):
        if key not in existing:
            continue
        if key in {"pid", "pgid"}:
            try:
                value = int(existing[key] or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                merged[key] = value
            continue
        if key == "status":
            existing_status = str(existing.get("status") or "")
            base_status = str(base.get("status") or "")
            # Prefer progressive runner states over a fresh launching stamp.
            rank = {
                "launching": 0,
                "running": 1,
                "exited": 2,
            }
            if rank.get(existing_status, -1) >= rank.get(base_status, -1):
                merged[key] = existing_status
            continue
        if existing[key] is not None:
            merged[key] = existing[key]
    if "updated_at" in existing:
        try:
            existing_updated = float(existing["updated_at"])
            base_updated = float(base.get("updated_at") or 0)
            merged["updated_at"] = max(existing_updated, base_updated)
        except (TypeError, ValueError):
            merged["updated_at"] = base.get("updated_at")
    return merged


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
            base = "# RailWarden Worker Task\n"
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

Acceptance criteria:
{chr(10).join(f"- {item}" for item in package.acceptance_criteria) or "- none"}

Validation commands:
{chr(10).join(f"- {command.name}: {' '.join(command.argv)}" for command in package.validation_commands) or "- none"}

Context refs:
{chr(10).join(f"- {item}" for item in package.context_refs) or "- none"}

Context content:
{resolve_context_refs(config.repository_root, package.context_refs) or "No context content resolved."}

Context rules:
- Read every listed context ref before editing.
- Do not modify context/* unless this package explicitly owns those paths.

Risk / merge policy:
- risk_level: {package.risk_level}
- merge_policy: {package.merge_policy}
- review_required: {package.review_required}
- approval_required: {package.approval_required}

Workspace: {workspace}
Expected result JSON path: {result_path}

Result JSON schema:
{{
  "schema_version": "1.0.0",
  "task_id": "{task["id"]}",
  "worker": "<agent id or provider>",
  "model": "<model identifier>",
  "status": "completed",
  "summary": "<brief implementation summary>",
  "workspace": "{workspace}",
  "branch": "<current branch>",
  "commit_hash": "<HEAD commit hash after committing owned changes>",
  "changed_files": ["<paths changed in the commit>"],
  "tests": [
    {{"command": "<command>", "status": "passed", "summary": "<short result>"}}
  ],
  "blockers": [],
  "evidence": []
}}

Rules for worker result:
- Write JSON exactly to the expected path above.
- Commit completed owned-path changes before writing a completed result.
- Do not commit the result JSON file.
- Use `status: "blocked"` with blockers if the task cannot be completed.
- `tests` must not contain `failed` or `not_run` entries for a completed task.

Available skills:
{skill_refs or "- none"}

RailWarden MCP routing:
- Agents with native MCP support should use the generated RailWarden MCP config.
- Agents without native MCP support must ask Hermes/RailWarden to execute MCP operations.

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
        elif state.state in {"execution_ready", "repair_required"}:
            transition_task(config.runtime_directory, existing_task, "ready")
    return load_tasks(config.runtime_directory)


def _package_for_task(files: ProjectFiles, task: dict[str, Any]) -> WorkPackage | None:
    package_id = str(task.get("package_id", ""))
    return files.packages.get(package_id)


def handoff_or_block(
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
    _set_provider_agent_ready(files, provider, active_task=str(task.get("id")))
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


def _allowed_actions_for_failure(failure_kind: str) -> list[str]:
    if failure_kind in {"quota_exhausted", "provider_quota_exhausted", "rate_limited"}:
        return ["handoff_provider", "ask_user"]
    if failure_kind in {"authentication", "provider_auth_required"}:
        return ["handoff_provider", "ask_user"]
    if failure_kind == "wrapper_quoting_failure":
        return ["handoff_provider", "repair_adapter", "ask_user"]
    if failure_kind == "missing_worker_result_with_commit":
        return ["normalize_result", "retry_same_provider", "handoff_provider"]
    if failure_kind == "missing_worker_result":
        return ["retry_same_provider", "handoff_provider", "ask_user"]
    if failure_kind == "validation_command_invalid":
        return ["repair_contract", "ask_user"]
    if failure_kind == "contract_ownership_gap":
        return ["repair_contract", "ask_user"]
    if failure_kind == "merge_branch_divergence":
        return ["reconcile_branch", "ask_user"]
    return ["retry_same_provider", "handoff_provider", "ask_user"]


def _require_decision(
    files: ProjectFiles,
    task: dict[str, Any],
    *,
    failure_kind: str,
    facts: dict[str, Any],
) -> dict[str, Any]:
    config = files.project
    allowed_actions = _allowed_actions_for_failure(failure_kind)
    event = emit_decision_required(
        config.runtime_directory,
        task_id=str(task["id"]),
        failure_kind=failure_kind,
        facts=facts,
        allowed_actions=allowed_actions,
    )
    return transition_task(
        config.runtime_directory,
        task,
        "decision_required",
        {
            "failure_kind": failure_kind,
            "decision_event": event,
            "allowed_actions": allowed_actions,
            "facts": facts,
        },
    )


def _has_clean_task_commit(config: ProjectConfig, workspace: Path, branch: str) -> bool:
    if not workspace.exists():
        return False
    try:
        return (
            tracked_is_clean(workspace)
            and branch_exists(workspace, branch)
            and head(workspace)
            != head(config.repository_root, config.integration_branch)
        )
    except GitError:
        return False


def _tmux_process_stale(process: dict[str, Any]) -> bool:
    if process.get("returncode") is not None or process.get("status") == "exited":
        return False
    updated_at = float(process.get("updated_at") or process.get("started_at") or 0)
    return updated_at > 0 and time.time() - updated_at > TMUX_STALE_SECONDS


def _default_supervisor_decisions(files: ProjectFiles) -> None:
    config = files.project
    if config.supervision_mode == "hermes":
        return
    for task in load_tasks(config.runtime_directory):
        if str(task.get("status")) != "decision_required":
            continue
        package = _package_for_task(files, task)
        if package is None:
            continue
        event = task.get("decision_event")
        observed_event = event if isinstance(event, dict) else {}
        failure_kind = str(task.get("failure_kind", "decision_required"))
        allowed = [str(item) for item in task.get("allowed_actions", [])]
        provider = str(task.get("provider") or task.get("last_provider") or "")
        branch = str(task.get("branch", package_branch(package)))
        workspace = Path(str(task.get("worktree", package_worktree(config, package))))
        result: dict[str, Any]
        if (
            failure_kind == "missing_worker_result_with_commit"
            and "normalize_result" in allowed
        ):
            normalized = normalize_result(config, task=task, package=package)
            result = transition_task(
                config.runtime_directory,
                task,
                "validating",
                {"runtime_result_path": normalized["path"]},
            )
            chosen = "normalize_result"
            rationale = "clean committed work can be normalized by RailWarden"
        elif "handoff_provider" in allowed and provider:
            result = handoff_or_block(
                files,
                task,
                package,
                provider=provider,
                failure_text=str(task.get("failure", "")),
                failure_kind=failure_kind,
                workspace=workspace,
                branch=branch,
                log_path=Path(str(task["log_path"])) if task.get("log_path") else None,
            )
            chosen = "handoff_provider"
            rationale = "default supervisor handoff for recoverable failure"
        elif "retry_same_provider" in allowed:
            result = transition_task(config.runtime_directory, task, "ready")
            chosen = "retry_same_provider"
            rationale = "default supervisor retry for permitted transient failure"
        else:
            result = transition_task(
                config.runtime_directory,
                task,
                "blocked",
                {"reason": "supervisor requires user input"},
            )
            chosen = "ask_user"
            rationale = "no safe automatic recovery action is available"
        record_decision(
            config.runtime_directory,
            observed_event=observed_event,
            diagnosis=failure_kind,
            allowed_actions=allowed,
            chosen_action=chosen,
            rationale=rationale,
            tool_call={"name": f"railwarden.{chosen}", "task_id": task.get("id")},
            result=result,
        )


def reconcile_processes(files: ProjectFiles) -> list[dict[str, Any]]:
    config = files.project
    tasks = load_tasks(config.runtime_directory)
    for task in tasks:
        status = str(task.get("status", ""))
        if status not in ACTIVE_STATES:
            continue
        process_path = _process_path(config, str(task["id"]))
        if not process_path.exists():
            if status in ACTIVE_STATES:
                transition_task(
                    config.runtime_directory,
                    task,
                    "decision_required",
                    {
                        "failure_kind": "missing_process_record",
                        "reason": "active task has no process metadata",
                    },
                )
            continue
        process = json.loads(process_path.read_text(encoding="utf-8"))
        if not isinstance(process, dict):
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
        runtime_path = Path(
            str(task.get("runtime_result_path", _result_path(config, str(task["id"]))))
        )
        if result_path.exists() or runtime_path.exists():
            if result_path.exists():
                normalized = normalize_result(config, task=task, package=package)
                runtime_path = Path(str(normalized["path"]))
            task["runtime_result_path"] = str(runtime_path)
            transition_task(config.runtime_directory, task, "validating")
            advance_workflow(config.runtime_directory, "PACKAGE_VALIDATION")
            result = load_worker_result(runtime_path)
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
                    commit_hash = str(result.get("commit_hash") or head(workspace))
                    validation = run_package_validation(
                        config, package, workspace, commit_hash
                    )
                    transition_task(
                        config.runtime_directory,
                        task,
                        "reviewing",
                        {
                            "commit_hash": commit_hash,
                            "package_validation": validation,
                        },
                    )
                    advance_workflow(config.runtime_directory, "PACKAGE_REVIEW")
                    review = run_package_review(
                        config,
                        package,
                        task=task,
                        worker_provider=provider,
                        reviewer_provider=_reviewer_provider_for(
                            files, package, provider
                        ),
                        changed_files=[str(item) for item in result["changed_files"]],
                        validation_evidence=validation,
                    )
                except ValidationError as exc:
                    handoff_or_block(
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
                if review["status"] != "passed":
                    findings = review.get("findings", [])
                    finding_text = (
                        "; ".join(str(item) for item in findings)
                        if isinstance(findings, list)
                        else str(findings)
                    )
                    handoff_or_block(
                        files,
                        task,
                        package,
                        provider=provider,
                        failure_text=finding_text,
                        failure_kind="review_failed",
                        workspace=workspace,
                        branch=branch,
                        log_path=Path(str(process.get("log_path", ""))),
                    )
                    continue
                record_success(config.runtime_directory, provider)
                _set_provider_agent_ready(files, provider, active_task=str(task["id"]))
                next_status = (
                    "merge_ready"
                    if requires_human_merge_approval(package)
                    else "review_passed"
                )
                transition_task(
                    config.runtime_directory,
                    task,
                    next_status,
                    {
                        "commit_hash": result.get("commit_hash"),
                        "review": review,
                        "merge_approval_required": requires_human_merge_approval(
                            package
                        ),
                    },
                )
            elif result.get("status") == "blocked":
                _set_provider_agent_ready(files, provider, active_task=str(task["id"]))
                transition_task(
                    config.runtime_directory,
                    task,
                    "blocked",
                    {"blockers": result.get("blockers", [])},
                )
            else:
                _set_provider_agent_ready(files, provider, active_task=str(task["id"]))
                transition_task(
                    config.runtime_directory, task, "failed", {"result": result}
                )
            continue
        pid = int(process.get("pid", 0))
        if task.get("pid", 0) == 0 and pid > 0:
            task["pid"] = pid
        if pid > 0 and process_alive(pid):
            continue
        if process.get("mode") == "tmux" and process.get("status") in {
            "launching",
            "running",
        }:
            if not _tmux_process_stale(process):
                continue
            _require_decision(
                files,
                task,
                failure_kind="stale_running_process",
                facts={
                    "provider": provider,
                    "status": process.get("status"),
                    "pid": pid,
                    "updated_at": process.get("updated_at"),
                    "workspace": str(workspace),
                    "result_json_exists": False,
                },
            )
            continue
        log_path = Path(str(process.get("log_path", "")))
        failure_text = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.exists()
            else ""
        )
        if _has_clean_task_commit(config, workspace, branch):
            if config.supervision_mode == "hermes":
                _require_decision(
                    files,
                    task,
                    failure_kind="missing_worker_result_with_commit",
                    facts={
                        "provider": provider,
                        "commit_exists": True,
                        "result_json_exists": False,
                        "worktree_dirty": False,
                        "workspace": str(workspace),
                        "branch": branch,
                    },
                )
                continue
            outcome = complete_worker_task(
                config,
                task=task,
                package=package,
                workspace=workspace,
                branch=branch,
                provider=provider,
                log_text=failure_text,
                result_path=result_path,
                runtime_result_path=runtime_path,
            )
            if outcome["status"] == "validated":
                runtime_path = Path(str(outcome["result_path"]))
                task["runtime_result_path"] = str(runtime_path)
            else:
                _require_decision(
                    files,
                    task,
                    failure_kind=str(outcome.get("failure_kind", "adapter_failure")),
                    facts={
                        "provider": provider,
                        "commit_exists": bool(outcome.get("commit_exists")),
                        "result_json_exists": bool(outcome.get("result_json_exists")),
                        "workspace": str(outcome.get("workspace", workspace)),
                        "branch": str(outcome.get("branch", branch)),
                        "failure": str(outcome.get("failure", failure_text[:1000])),
                    },
                )
            continue
        provider_config = config.provider_configs.get(provider)
        state = record_failure(
            config.runtime_directory,
            provider,
            failure_text,
            cooldown_seconds=provider_config.cooldown_seconds
            if provider_config is not None
            else 3600,
        )
        failure_kind = state.failure_kind or "adapter_failure"
        if "unexpected eof while looking for matching" in failure_text.lower() or (
            provider == "antigravity" and "syntax error" in failure_text.lower()
        ):
            failure_kind = "wrapper_quoting_failure"
        dirty_or_branch = workspace.exists() and (
            not is_clean(workspace) or branch_exists(config.repository_root, branch)
        )
        if dirty_or_branch and config.execution_preserve_partial_work_on_handoff:
            _require_decision(
                files,
                task,
                failure_kind=failure_kind,
                facts={
                    "provider": provider,
                    "commit_exists": branch_exists(config.repository_root, branch),
                    "result_json_exists": False,
                    "worktree_dirty": workspace.exists() and not is_clean(workspace),
                    "log_path": str(log_path),
                    "failure": failure_text[:1000],
                },
            )
        else:
            _require_decision(
                files,
                task,
                failure_kind=failure_kind,
                facts={
                    "provider": provider,
                    "commit_exists": False,
                    "result_json_exists": False,
                    "worktree_dirty": workspace.exists() and not is_clean(workspace),
                    "log_path": str(log_path),
                    "provider_status": state.status,
                    "failure": failure_text[:1000],
                },
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
    action = (
        "repair"
        if str(task.get("status")) == "handoff_needed" and workspace.exists()
        else "execute"
    )
    ensure_worktree(
        repository=config.repository_root,
        integration_branch=config.integration_branch,
        workspace=workspace,
        branch=branch,
        action=action,
    )
    attempt = int(task.get("attempt", 0)) + 1
    task["attempt"] = attempt
    runtime_result_path = _result_path(config, str(task["id"]))
    result_path = _worker_result_path(workspace, str(task["id"]))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path = _write_prompt(config, package, task, workspace, result_path)
    log_path = _log_path(config, str(task["id"]), attempt, provider)
    command = replace(
        adapter.launch_command(workspace, prompt_path, result_path),
        cwd=workspace,
        log_path=log_path,
    )
    payload = {
        "provider": provider,
        "branch": branch,
        "worktree": str(workspace),
        "prompt_path": str(prompt_path),
        "result_path": str(result_path),
        "runtime_result_path": str(runtime_result_path),
        "log_path": str(log_path),
    }
    agent = _agent_for_provider(files, provider)
    if not launch:
        transition_task(config.runtime_directory, task, "ready", payload)
        if agent is not None:
            _set_agent_runtime_state(
                config,
                agent,
                state="ready",
                active_task=str(task["id"]),
            )
        append_event(
            config.runtime_directory,
            "task_launch_planned",
            {"provider": provider, "package_id": package.package_id},
            task_id=str(task["id"]),
        )
        return task
    transition_task(config.runtime_directory, task, "assigned", payload)
    if agent is not None:
        _set_agent_runtime_state(
            config,
            agent,
            state="running",
            active_task=str(task["id"]),
        )
    pane_id = pane_for_worker(
        config.runtime_directory,
        agent_id=agent.agent_id if agent is not None else None,
        provider=provider,
    )
    process_path = _process_path(config, str(task["id"]))
    if pane_id:
        managed = launch_tmux_managed(
            command,
            pid_path=process_path,
            script_path=_tmux_script_path(config, str(task["id"]), attempt),
            pane_id=pane_id,
            provider=provider,
        )
    else:
        managed = launch_managed(
            command,
            pid_path=process_path,
        )
    # Prefer process metadata already written by the launcher/runner. Do not
    # clobber in-pane pid/status updates with a zeroed "launching" record.
    process_payload = _merge_process_launch_record(
        process_path,
        {
            "pid": managed.pid,
            "pgid": managed.pgid,
            "command": list(managed.command),
            "cwd": str(command.cwd),
            "stdin_path": str(command.stdin_path) if command.stdin_path else None,
            "log_path": str(managed.log_path),
            "provider": provider,
            "status": "running" if not pane_id else "launching",
            "started_at": time.time(),
            "updated_at": time.time(),
            **(
                {"mode": "tmux", "pane_id": pane_id, "status": "launching"}
                if pane_id
                else {}
            ),
        },
    )
    process_path.write_text(json.dumps(process_payload, indent=2), encoding="utf-8")
    transition_task(
        config.runtime_directory,
        task,
        "running",
        {"pid": int(process_payload.get("pid") or managed.pid or 0)},
    )
    append_event(
        config.runtime_directory,
        "task_launched",
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
    model = agent.model_profile.model
    if provider == "antigravity":
        from railwarden.planning.antigravity import resolve_antigravity_model

        model = resolve_antigravity_model(model)
    return ProviderAdapter(
        name=adapter.name,
        executable=adapter.executable,
        model=model,
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


def _set_provider_agent_ready(
    files: ProjectFiles, provider: str, *, active_task: str
) -> None:
    agent = _agent_for_provider(files, provider)
    if agent is None or agent.active_task not in {None, active_task}:
        return
    _set_agent_runtime_state(files.project, agent, state="ready", active_task=None)


def _reviewer_provider_for(
    files: ProjectFiles, package: WorkPackage, worker_provider: str
) -> str:
    profile = load_session_profile(files.project)
    if (
        profile.reviewer is not None
        and profile.reviewer.executor_adapter != worker_provider
    ):
        return profile.reviewer.executor_adapter
    if package.reviewer_profile and package.reviewer_profile != worker_provider:
        return package.reviewer_profile
    providers = eligible_providers(files.project, package, exclude={worker_provider})
    return providers[0] if providers else "railwarden-local-reviewer"


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
    context_stat = context_status(config, files.packages)
    if context_stat.get("status") == "needs_population":
        append_event(
            config.runtime_directory, "controller_waiting_for_context_population"
        )
        return {"status": "waiting_for_context_population", "launched": []}
    refresh_health = launch or adapters is not None
    adapters = adapters or default_adapters()
    if refresh_health:
        _refresh_provider_health(files, adapters)
    for provider in config.worker_providers:
        state = refresh_state(load_state(config.runtime_directory, provider))
        if state.status == "probe":
            save_state(config.runtime_directory, state)
    hydrate_task_state(files)
    reconcile_processes(files)
    _default_supervisor_decisions(files)
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
            advance_workflow(config.runtime_directory, "DEPENDENCY_SAFE_MERGE")
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
                advance_workflow(config.runtime_directory, "INTEGRATION_VALIDATION")
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
        advance_workflow(config.runtime_directory, "WORK_EXECUTING")
        slots -= 1
    return {"status": "ok", "launched": launched, "integrated": integrated}


def _refresh_provider_health(
    files: ProjectFiles, adapters: dict[str, ProviderAdapter]
) -> None:
    config = files.project
    for provider in config.worker_providers:
        adapter = adapters.get(provider)
        if adapter is None:
            continue
        health = adapter.health_check()
        state = load_state(config.runtime_directory, provider)
        status = str(health.get("status", "unavailable"))
        if status == "healthy":
            if state.status != "healthy":
                record_success(config.runtime_directory, provider)
            agent = _agent_for_provider(files, provider)
            if (
                agent is not None
                and agent.state in {"unavailable", "rate_limited"}
                and agent.active_task is None
            ):
                _set_agent_runtime_state(config, agent, state="ready", active_task=None)
            continue
        if status == "unavailable":
            state.status = "unavailable"
            state.last_error = str(health.get("reason", "provider unavailable"))
            save_state(config.runtime_directory, state)
            agent = _agent_for_provider(files, provider)
            if agent is not None and agent.active_task is None:
                _set_agent_runtime_state(
                    config, agent, state="unavailable", active_task=None
                )


def integration_candidates(files: ProjectFiles) -> list[dict[str, Any]]:
    tasks_by_package = {
        str(task.get("package_id")): task
        for task in load_tasks(files.project.runtime_directory)
    }
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
        if str(tasks_by_package.get(state.package_id, {}).get("status"))
        in {"review_passed", "merge_approved", "integration_ready"}
    ]
    return sorted(candidates, key=lambda item: str(item["package_id"]))


def save_all_tasks(config: ProjectConfig, tasks: list[dict[str, Any]]) -> None:
    save_tasks(config.runtime_directory, tasks)
