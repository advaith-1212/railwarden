from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from lfg import __version__
from lfg.config.init import initialize_project
from lfg.config.loader import load_project_files
from lfg.engine.controller import controller_tick
from lfg.engine.dashboard import render_dashboard
from lfg.errors import LfgError
from lfg.git import discover_repo
from lfg.hermes.mailbox import (
    append_message,
    messages_for,
    parse_directive,
    read_messages,
)
from lfg.hermes.profile import generate_hermes_profile
from lfg.integration.manager import integrate_one
from lfg.mcp.server import serve as serve_mcp
from lfg.migration.tmom import dry_run_tmom_adoption
from lfg.models.registry import list_models, validate_model_refs
from lfg.planning.antigravity import AntigravityClaudePlanner
from lfg.planning.pipeline import approve_latest_plan, create_pending_plan
from lfg.providers.adapters import default_adapters
from lfg.runtime.checkpoints import create_checkpoint_commit
from lfg.runtime.doctor import doctor_report
from lfg.runtime.events import read_events
from lfg.runtime.handoff import create_handoff_packet
from lfg.runtime.model_refs import parse_model_ref
from lfg.runtime.quota import load_quota, update_usage
from lfg.runtime.secrets import ensure_runtime_secrets_file
from lfg.runtime.session import (
    AgentInstance,
    QuotaPolicy,
    SessionProfile,
    load_session_profile,
    model_profile_from_ref,
    save_session_profile,
    update_agent,
)
from lfg.runtime.tasks import load_tasks, transition_task
from lfg.runtime.workflow import advance_workflow, load_workflow
from lfg.scheduler.classifier import (
    classify_packages,
    execution_plan,
    package_branch,
    package_worktree,
)
from lfg.scheduler.dag import Dag
from lfg.tmux.session import create_session, panes, session_name, stop_session


def configured_project(start: Path) -> tuple[Path, Any]:
    root = discover_repo(start)
    return root, load_project_files(root)


def default_legacy_source(target: Path, explicit_source: str | None) -> Path:
    if explicit_source:
        return Path(explicit_source).resolve()
    env_source = os.environ.get("LFG_TMOM_SOURCE") or os.environ.get("LFG_TMON_SOURCE")
    if env_source:
        return Path(env_source).resolve()
    candidate = target.parent / "tmom-worktrees" / "orchestrator"
    if candidate.exists():
        return candidate.resolve()
    raise LfgError(
        "Legacy adoption requires --source when no sibling prototype exists."
    )


def cmd_init(args: argparse.Namespace) -> int:
    root = discover_repo(Path.cwd())
    if not args.yes:
        result = initialize_project(root, yes=False)
        print("Proposed .gitignore additions:")
        print(result["gitignore_proposal"] or "none")
        print("Run `lfg init --yes` to apply.")
        return 0
    print(json.dumps(initialize_project(root, yes=True), indent=2, sort_keys=True))
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    target = (
        Path(args.repository).resolve()
        if args.repository
        else discover_repo(Path.cwd())
    )
    source = default_legacy_source(target, args.source)
    report = dry_run_tmom_adoption(target, source)
    markdown = report.markdown()
    if args.dry_run:
        output_path = Path(args.report).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        print(markdown)
        print(f"Report written: {output_path}")
        return 0
    raise LfgError(
        "Non-dry-run adopt is intentionally approval-gated; run with --dry-run first."
    )


def cmd_plan(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    states = classify_packages(files.project, files.packages)
    plan = execution_plan(files.project, states)
    critical_path = Dag(files.packages).critical_path() if files.packages else ()
    payload = {
        "packages": [state.to_dict() for state in states],
        "plan": plan,
        "critical_path": list(critical_path),
        "expected_concurrency": files.project.worker_concurrency,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    fixture_path = os.environ.get("LFG_PLANNER_OUTPUT")
    fixture = Path(fixture_path).read_text(encoding="utf-8") if fixture_path else None
    pending = create_pending_plan(files.project, args.goal, planner_output_text=fixture)
    print(f"Created pending plan: {pending.run_id}")
    print(pending.plan_markdown)
    print("Run `lfg approve plan` to begin execution.")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    if args.target != "plan":
        raise LfgError("Only `lfg approve plan` is supported")
    _, files = configured_project(Path.cwd())
    payload = approve_latest_plan(files.project)
    print(f"Approved plan: {payload['run_id']}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    name = create_session(files.project, attach=not args.no_attach)
    print(f"Started LFG session: {name}")
    return 0


def _prompt(default: str, label: str) -> str:
    if not sys.stdin.isatty():
        return default
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def _prompt_float(default: float, label: str) -> float:
    return float(_prompt(str(default), label))


def _prompt_bool(default: bool, label: str) -> bool:
    value = _prompt("yes" if default else "no", label).lower()
    if value in {"1", "true", "yes", "y"}:
        return True
    if value in {"0", "false", "no", "n"}:
        return False
    raise LfgError(f"Expected yes/no for {label}")


def _prompt_optional_int(default: int | None, label: str) -> int | None:
    value = _prompt("" if default is None else str(default), label)
    return int(value) if value else None


def _agent_with_model_and_policy(
    agent: AgentInstance,
    *,
    model_ref: str,
    quota_policy: QuotaPolicy,
) -> AgentInstance:
    return AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=model_profile_from_ref(model_ref),
        executor_adapter=agent.executor_adapter,
        state=agent.state,
        quota_policy=quota_policy,
        active_task=agent.active_task,
    )


def _build_launch_profile(
    profile: SessionProfile, *, name: str | None
) -> SessionProfile:
    profile_name = name or _prompt(profile.name, "Session profile")
    orchestrator_ref = _prompt(
        profile.orchestrator.model_profile.model_ref,
        "Hermes orchestrator model ref",
    )
    architect_ref = _prompt(
        profile.architect.model_profile.model_ref, "Architect model ref"
    )
    worker_refs = [
        _prompt(worker.model_profile.model_ref, f"{worker.agent_id} model ref")
        for worker in profile.workers
    ]
    reviewer_ref = (
        _prompt(profile.reviewer.model_profile.model_ref, "Reviewer model ref")
        if profile.reviewer is not None
        else None
    )
    validator_ref = (
        _prompt(profile.validator.model_profile.model_ref, "Validator model ref")
        if profile.validator is not None
        else None
    )
    budget_label = _prompt(profile.budget_label, "Budget label")
    fallback_policy = _prompt(profile.fallback_policy, "Fallback/swap policy")
    default_policy = profile.orchestrator.quota_policy
    quota_policy = QuotaPolicy(
        warning_threshold_percent=_prompt_float(
            default_policy.warning_threshold_percent,
            "Quota warning threshold percent",
        ),
        pause_threshold_percent=_prompt_float(
            default_policy.pause_threshold_percent,
            "Quota pause threshold percent",
        ),
        hard_stop_below_pause=_prompt_bool(
            default_policy.hard_stop_below_pause,
            "Hard stop below pause threshold",
        ),
        manual_token_limit=_prompt_optional_int(
            default_policy.manual_token_limit,
            "Manual token budget limit",
        ),
    )
    workers = tuple(
        _agent_with_model_and_policy(
            worker,
            model_ref=worker_refs[index],
            quota_policy=quota_policy,
        )
        for index, worker in enumerate(profile.workers)
    )
    return SessionProfile(
        name=profile_name,
        project=profile.project,
        created_at=profile.created_at,
        updated_at=time.time(),
        orchestrator=_agent_with_model_and_policy(
            profile.orchestrator,
            model_ref=orchestrator_ref,
            quota_policy=quota_policy,
        ),
        architect=_agent_with_model_and_policy(
            profile.architect,
            model_ref=architect_ref,
            quota_policy=quota_policy,
        ),
        workers=workers,
        reviewer=_agent_with_model_and_policy(
            profile.reviewer,
            model_ref=reviewer_ref,
            quota_policy=quota_policy,
        )
        if profile.reviewer is not None and reviewer_ref is not None
        else None,
        validator=_agent_with_model_and_policy(
            profile.validator,
            model_ref=validator_ref,
            quota_policy=quota_policy,
        )
        if profile.validator is not None and validator_ref is not None
        else None,
        fallback_policy=fallback_policy,
        budget_label=budget_label,
    )


def cmd_launch(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    profile = _build_launch_profile(profile, name=args.profile)
    save_session_profile(files.project, profile)
    hermes_profile = generate_hermes_profile(files.project, profile)
    advance_workflow(
        files.project.runtime_directory,
        "goal_received",
        payload={"session_profile": profile.name},
    )
    name = create_session(
        files.project,
        attach=not args.no_attach,
        profile=profile,
        hermes_profile=hermes_profile,
    )
    print(
        json.dumps(
            {
                "session": name,
                "profile": str(
                    files.project.runtime_directory / "state" / "session-profile.json"
                ),
                "hermes": hermes_profile.to_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_attach(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    name = session_name(files.project)
    subprocess.run(["tmux", "attach-session", "-t", name], check=False)
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    stopped = stop_session(files.project)
    print("Stopped." if stopped else "LFG session is not running.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    payload = {"session": session_name(files.project), "panes": panes(files.project)}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_observability(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    workflow = load_workflow(files.project.runtime_directory)
    profile = load_session_profile(files.project)
    payload = {
        "dashboard": render_dashboard(files),
        "workflow": workflow.__dict__,
        "agents": [
            {
                "agent_id": agent.agent_id,
                "role": agent.role,
                "state": agent.state,
                "model_ref": agent.model_profile.model_ref,
                "quota": load_quota(files.project.runtime_directory, agent).__dict__,
            }
            for agent in profile.agents
        ],
        "tmux": {"session": session_name(files.project), "panes": panes(files.project)},
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def cmd_dashboard(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(render_dashboard(files), end="")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(
        json.dumps(
            read_events(files.project.runtime_directory, limit=args.limit), indent=2
        )
    )
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    adapters = default_adapters()
    planner = AntigravityClaudePlanner(files.project.planner_model).doctor()
    payload = doctor_report(files.project, adapters=adapters)
    payload["planning_architect"] = planner.__dict__
    payload["coordination"]["agent"] = "Hermes"
    payload["coordination"]["mailbox"] = str(
        files.project.runtime_directory / "state" / "hermes-mailbox.jsonl"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_config(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(json.dumps(files.project.__dict__, indent=2, sort_keys=True, default=str))
    return 0


def cmd_logs(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    log_dir = files.project.runtime_directory / "logs"
    print(log_dir)
    return 0


def cmd_controller(args: argparse.Namespace) -> int:
    root = discover_repo(Path.cwd())
    while True:
        files = load_project_files(root)
        result = controller_tick(
            files,
            launch=not args.no_launch,
            integrate=not args.no_integrate,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.once:
            return 0
        time.sleep(args.interval)


def cmd_handoff(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    tasks = load_tasks(files.project.runtime_directory)
    for task in tasks:
        if (
            str(task.get("id")) == args.task_id
            or str(task.get("package_id")) == args.task_id
        ):
            payload: dict[str, object] = {"manual": True}
            if args.provider:
                payload["provider_override"] = args.provider
            transition_task(
                files.project.runtime_directory, task, "handoff_needed", payload
            )
            print(f"Marked {task.get('id')} for handoff.")
            return 0
    raise LfgError(f"Unknown task: {args.task_id}")


def cmd_model_list(_args: argparse.Namespace) -> int:
    print(json.dumps(list_models(), indent=2, sort_keys=True))
    return 0


def cmd_model_doctor(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    refs = [agent.model_profile.model_ref for agent in profile.agents]
    print(json.dumps(validate_model_refs(refs), indent=2, sort_keys=True))
    return 0


def cmd_model_configure(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    if args.model_ref:
        parse_model_ref(args.model_ref)
    path = ensure_runtime_secrets_file(files.project.runtime_directory)
    print(json.dumps({"secrets_env": str(path), "model_ref": args.model_ref}, indent=2))
    return 0


def _find_agent(profile: SessionProfile, agent_id: str) -> AgentInstance:
    for agent in profile.agents:
        if agent.agent_id == agent_id:
            return agent
    raise LfgError(f"Unknown agent: {agent_id}")


def _current_goal(runtime_dir: Path) -> str:
    path = runtime_dir / "state" / "pending-plan.json"
    if not path.exists():
        return ""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("goal", "")) if isinstance(payload, dict) else ""


def _active_task_for_agent(
    tasks: list[dict[str, Any]], agent: AgentInstance
) -> dict[str, Any] | None:
    if agent.active_task:
        for task in tasks:
            if str(task.get("id")) == agent.active_task:
                return task
    active_statuses = {
        "assigned",
        "running",
        "handoff_needed",
        "cooldown_wait",
        "validating",
    }
    for task in tasks:
        if (
            str(task.get("provider")) == agent.executor_adapter
            and str(task.get("status")) in active_statuses
        ):
            return task
    return None


def _create_swap_handoff(
    files: Any,
    task: dict[str, Any],
    agent: AgentInstance,
    *,
    target_provider: str,
) -> dict[str, Any]:
    package = files.packages.get(str(task.get("package_id", "")))
    if package is None:
        raise LfgError(f"Task package is not available: {task.get('id')}")
    workspace = Path(
        str(task.get("worktree", package_worktree(files.project, package)))
    )
    branch = str(task.get("branch", package_branch(package)))
    checkpoint: dict[str, object] | None = None
    if workspace.exists():
        try:
            checkpoint = create_checkpoint_commit(
                files.project,
                task_id=str(task["id"]),
                workspace=workspace,
                attempt=int(task.get("attempt", 0)),
                allowed_paths=package.owned_paths,
            ).to_dict()
        except LfgError:
            checkpoint = None
    packet = create_handoff_packet(
        runtime_dir=files.project.runtime_directory,
        task=task,
        goal=_current_goal(files.project.runtime_directory),
        objective=package.objective,
        workspace=workspace,
        branch=branch,
        provider=agent.executor_adapter,
        failure_kind="agent_swap",
        log_path=Path(str(task["log_path"])) if task.get("log_path") else None,
        next_provider=target_provider,
    )
    payload: dict[str, Any] = {
        "handoff_packet": str(packet),
        "last_provider": agent.executor_adapter,
        "provider_override": target_provider,
        "failure_kind": "agent_swap",
    }
    if checkpoint is not None:
        payload["checkpoint"] = checkpoint
    return transition_task(
        files.project.runtime_directory,
        task,
        "handoff_needed",
        payload,
    )


def cmd_agent_list(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    print(
        json.dumps(
            [
                {
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "state": agent.state,
                    "executor_adapter": agent.executor_adapter,
                    "model_ref": agent.model_profile.model_ref,
                    "active_task": agent.active_task,
                }
                for agent in profile.agents
            ],
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_agent_swap(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    agent = _find_agent(profile, args.agent_id)
    model_profile = model_profile_from_ref(args.to)
    target_provider = (
        model_profile.provider
        if model_profile.provider in files.project.worker_providers
        else agent.executor_adapter
    )
    tasks = load_tasks(files.project.runtime_directory)
    active_task = _active_task_for_agent(tasks, agent)
    updated_task = (
        _create_swap_handoff(
            files,
            active_task,
            agent,
            target_provider=target_provider,
        )
        if active_task is not None
        else None
    )
    updated = AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=model_profile,
        executor_adapter=target_provider,
        state="handoff_needed" if updated_task is not None else "ready",
        quota_policy=agent.quota_policy,
        active_task=str(updated_task["id"]) if updated_task is not None else None,
    )
    save_session_profile(files.project, update_agent(profile, updated))
    advance_workflow(
        files.project.runtime_directory,
        "recovery_or_swap",
        payload={"agent_id": args.agent_id, "to": args.to},
    )
    print(
        json.dumps(
            {
                "agent_id": updated.agent_id,
                "model_ref": updated.model_profile.model_ref,
                "executor_adapter": updated.executor_adapter,
                "task_id": updated_task.get("id") if updated_task else None,
                "handoff_packet": updated_task.get("handoff_packet")
                if updated_task
                else None,
            },
            indent=2,
        )
    )
    return 0


def cmd_agent_state(args: argparse.Namespace, state: str) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    agent = _find_agent(profile, args.agent_id)
    updated = AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=agent.model_profile,
        executor_adapter=agent.executor_adapter,
        state=state,  # type: ignore[arg-type]
        quota_policy=agent.quota_policy,
        active_task=agent.active_task,
    )
    save_session_profile(files.project, update_agent(profile, updated))
    print(json.dumps({"agent_id": updated.agent_id, "state": updated.state}, indent=2))
    return 0


def cmd_quota_status(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    print(
        json.dumps(
            [
                {
                    "agent_id": agent.agent_id,
                    **load_quota(files.project.runtime_directory, agent).__dict__,
                }
                for agent in profile.agents
            ],
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def cmd_quota_set(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    agent = _find_agent(profile, args.agent_id)
    quota = update_usage(
        files.project.runtime_directory,
        agent,
        remaining_percent=args.remaining_percent,
        confidence="manual",
    )
    print(json.dumps(quota.__dict__, indent=2, sort_keys=True))
    return 0


def cmd_checkpoint_create(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    tasks = load_tasks(files.project.runtime_directory)
    for task in tasks:
        if (
            str(task.get("id")) != args.task_id
            and str(task.get("package_id")) != args.task_id
        ):
            continue
        package = files.packages[str(task["package_id"])]
        workspace = Path(
            str(task.get("worktree", package_worktree(files.project, package)))
        )
        result = create_checkpoint_commit(
            files.project,
            task_id=str(task["id"]),
            workspace=workspace,
            attempt=int(task.get("attempt", 0)),
            allowed_paths=package.owned_paths,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0
    raise LfgError(f"Unknown task: {args.task_id}")


def cmd_worker(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    provider = args.provider
    print(f"LFG worker adapter pane ready: {provider}")
    print("Waiting for Hermes directives. Press Ctrl-C to stop this pane.")
    seen = 0
    try:
        while True:
            all_messages = read_messages(files.project.runtime_directory)
            for message in messages_for(
                files.project.runtime_directory,
                provider,
                after=seen,
            ):
                print(
                    f"[Hermes -> {provider}] {message.get('body', '')}",
                    flush=True,
                )
            seen = len(all_messages)
            time.sleep(2)
    except KeyboardInterrupt:
        return 0


def cmd_hermes(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print("LFG Hermes console.")
    print(
        "Commands: goal <text>, approve plan, status, dag, agents, tasks, logs, pause, resume, stop-after-current, quit"
    )
    print(
        "Route messages with `codex: ...`, `gemini: ...`, `composer: ...`, or `broadcast: ...`."
    )
    while True:
        try:
            line = input("hermes> ").strip()
        except EOFError:
            return 0
        if line in {"quit", "exit"}:
            return 0
        if line.startswith("goal "):
            pending = create_pending_plan(
                files.project, line.removeprefix("goal ").strip()
            )
            print(
                f"Created pending plan {pending.run_id}. Run `approve plan` to execute."
            )
        elif line == "approve plan":
            payload = approve_latest_plan(files.project)
            print(f"Approved plan {payload['run_id']}.")
        elif line in {"status", "plan", "dag", "agents", "tasks"}:
            cmd_plan(argparse.Namespace())
        elif line == "logs":
            cmd_logs(argparse.Namespace())
        elif line in {"pause", "resume", "stop-after-current"}:
            append_message(
                files.project.runtime_directory,
                sender="hermes",
                recipient="broadcast",
                body=line,
            )
            print(f"Recorded {line}.")
        elif line.startswith("handoff "):
            parts = line.split()
            cmd_handoff(
                argparse.Namespace(
                    task_id=parts[1], provider=parts[2] if len(parts) > 2 else None
                )
            )
        elif line.startswith("retry ") or line.startswith("unblock "):
            task_id = line.split()[1]
            tasks = load_tasks(files.project.runtime_directory)
            for task in tasks:
                if (
                    str(task.get("id")) == task_id
                    or str(task.get("package_id")) == task_id
                ):
                    transition_task(files.project.runtime_directory, task, "ready")
                    print(f"Marked {task.get('id')} ready.")
                    break
        elif line.startswith("block "):
            task_id = line.split()[1]
            tasks = load_tasks(files.project.runtime_directory)
            for task in tasks:
                if (
                    str(task.get("id")) == task_id
                    or str(task.get("package_id")) == task_id
                ):
                    transition_task(
                        files.project.runtime_directory,
                        task,
                        "blocked",
                        {"manual": True},
                    )
                    print(f"Blocked {task.get('id')}.")
                    break
        elif directive := parse_directive(line):
            recipient, body = directive
            append_message(
                files.project.runtime_directory,
                sender="hermes",
                recipient=recipient,
                body=body,
            )
            print(f"Sent to {recipient}.")
        elif line:
            print(f"Recorded coordinator instruction: {line}")


def cmd_integrate(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    states = classify_packages(files.project, files.packages)
    plan = execution_plan(files.project, states)
    queue = plan.get("integration_queue", [])
    if not isinstance(queue, list) or not queue:
        print("Integration queue is empty.")
        return 0
    print(
        json.dumps(
            integrate_one(
                config=files.project,
                candidate=queue[0],
                validation_commands=files.validation,
                execute=args.execute,
            ),
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    if args.mcp_command != "serve":
        raise LfgError("Only `lfg mcp serve` is supported")
    return serve_mcp(Path.cwd())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lfg")
    parser.add_argument("--version", action="store_true", help="Show version and exit.")
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init")
    init.add_argument("--yes", action="store_true")
    init.set_defaults(func=cmd_init)
    adopt = sub.add_parser("adopt")
    adopt.add_argument("repository", nargs="?")
    adopt.add_argument("--dry-run", action="store_true")
    adopt.add_argument("--source")
    adopt.add_argument("--report", default="artifacts/tmom-adoption-dry-run.md")
    adopt.set_defaults(func=cmd_adopt)
    for name, func in [
        ("plan", cmd_plan),
        ("replan", cmd_plan),
        ("status", cmd_status),
        ("dashboard", cmd_dashboard),
        ("observability", cmd_observability),
        ("logs", cmd_logs),
        ("doctor", cmd_doctor),
        ("config", cmd_config),
    ]:
        item = sub.add_parser(name)
        item.set_defaults(func=func)
    run = sub.add_parser("run")
    run.add_argument("goal")
    run.set_defaults(func=cmd_run)
    approve = sub.add_parser("approve")
    approve.add_argument("target")
    approve.set_defaults(func=cmd_approve)
    events = sub.add_parser("events")
    events.add_argument("--limit", type=int, default=50)
    events.set_defaults(func=cmd_events)
    start = sub.add_parser("start")
    start.add_argument("--no-attach", action="store_true")
    start.set_defaults(func=cmd_start)
    launch = sub.add_parser("launch")
    launch.add_argument("--profile")
    launch.add_argument("--no-attach", action="store_true")
    launch.set_defaults(func=cmd_launch)
    sub.add_parser("attach").set_defaults(func=cmd_attach)
    sub.add_parser("stop").set_defaults(func=cmd_stop)
    restart = sub.add_parser("restart")
    restart.add_argument("--no-attach", action="store_true")
    restart.set_defaults(func=lambda args: (cmd_stop(args), cmd_start(args))[1])
    integrate = sub.add_parser("integrate")
    integrate.add_argument("--execute", action="store_true")
    integrate.set_defaults(func=cmd_integrate)
    controller = sub.add_parser("controller")
    controller.add_argument("--once", action="store_true")
    controller.add_argument("--no-launch", action="store_true")
    controller.add_argument("--no-integrate", action="store_true")
    controller.add_argument("--interval", type=float, default=5.0)
    controller.set_defaults(func=cmd_controller)
    handoff = sub.add_parser("handoff")
    handoff.add_argument("task_id")
    handoff.add_argument("provider", nargs="?")
    handoff.set_defaults(func=cmd_handoff)
    worker = sub.add_parser("worker")
    worker.add_argument("provider")
    worker.set_defaults(func=cmd_worker)
    model = sub.add_parser("model")
    model_sub = model.add_subparsers(dest="model_command", required=True)
    model_sub.add_parser("list").set_defaults(func=cmd_model_list)
    model_sub.add_parser("doctor").set_defaults(func=cmd_model_doctor)
    configure = model_sub.add_parser("configure")
    configure.add_argument("model_ref", nargs="?")
    configure.set_defaults(func=cmd_model_configure)
    agent = sub.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("list").set_defaults(func=cmd_agent_list)
    swap = agent_sub.add_parser("swap")
    swap.add_argument("agent_id")
    swap.add_argument("--to", required=True)
    swap.set_defaults(func=cmd_agent_swap)
    pause = agent_sub.add_parser("pause")
    pause.add_argument("agent_id")
    pause.set_defaults(func=lambda args: cmd_agent_state(args, "paused"))
    resume = agent_sub.add_parser("resume")
    resume.add_argument("agent_id")
    resume.set_defaults(func=lambda args: cmd_agent_state(args, "ready"))
    quota = sub.add_parser("quota")
    quota_sub = quota.add_subparsers(dest="quota_command", required=True)
    quota_sub.add_parser("status").set_defaults(func=cmd_quota_status)
    quota_set = quota_sub.add_parser("set")
    quota_set.add_argument("agent_id")
    quota_set.add_argument("--remaining-percent", type=float, required=True)
    quota_set.set_defaults(func=cmd_quota_set)
    checkpoint = sub.add_parser("checkpoint")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_create = checkpoint_sub.add_parser("create")
    checkpoint_create.add_argument("task_id")
    checkpoint_create.set_defaults(func=cmd_checkpoint_create)
    mcp = sub.add_parser("mcp")
    mcp.add_argument("mcp_command", choices=["serve"])
    mcp.set_defaults(func=cmd_mcp)
    sub.add_parser("hermes").set_defaults(func=cmd_hermes)
    sub.add_parser("version").set_defaults(func=lambda _args: print(__version__) or 0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.command is None:
        return cmd_start(argparse.Namespace(no_attach=False))
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    try:
        return int(func(args))
    except LfgError as exc:
        print(f"lfg: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
