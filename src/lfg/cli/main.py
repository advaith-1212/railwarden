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
        print("LFG project setup preview")
        print()
        print(f"Repository: {root}")
        print(
            "Files to create: .lfg/project.yaml, .lfg/work_packages.yaml, .lfg/validation.yaml"
        )
        print("Runtime state: .lfg-runtime/ (ignored by git)")
        print()
        print("Proposed .gitignore additions:")
        print(result["gitignore_proposal"] or "  none")
        print()
        print("Run `lfg init --yes` to write these files.")
        return 0
    result = initialize_project(root, yes=True)
    print("LFG project initialized")
    print()
    for key in ("config", "work_packages", "validation", "state_schema"):
        if key in result:
            print(f"{key.replace('_', ' ').title()}: {result[key]}")
    print()
    print("Next steps:")
    print(
        "  1. Run `lfg doctor` to check Hermes, tmux, providers, MCP, and runtime ignore rules."
    )
    print("  2. Run `lfg launch` to start the factory tmux session.")
    print("  3. Tell Hermes what to build in the factory window.")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    root = discover_repo(Path.cwd())
    configured = (root / ".lfg" / "project.yaml").exists() or (
        root / ".lfg" / "factory.yaml"
    ).exists()
    print("LFG setup")
    print()
    print(f"Repository: {root}")
    if not configured:
        if not args.yes:
            print("This repository is not configured yet.")
            print(
                "Run `lfg setup --yes` to create project config and runtime ignore rules."
            )
            print("Then run `lfg doctor` to verify Hermes, tmux, providers, and MCP.")
            return 0
        initialize_project(root, yes=True)
        print("Created .lfg project configuration.")
    else:
        print("Project configuration already exists.")
    print()
    print("Recommended next steps:")
    print("  1. lfg doctor")
    print("  2. lfg launch")
    print("  3. Tell Hermes what to build in the factory window")
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


def _prompt_choice(default: str, label: str, choices: dict[str, str]) -> str:
    if not sys.stdin.isatty():
        return default
    print(label)
    for key, description in choices.items():
        marker = " (default)" if key == default else ""
        print(f"  {key}{marker}: {description}")
    value = input(f"Choose [{default}]: ").strip()
    if not value:
        return default
    if value not in choices:
        raise LfgError(f"Expected one of: {', '.join(choices)}")
    return value


def _section(title: str) -> None:
    print(title)
    print("-" * len(title))


def _table(headers: list[str], rows: list[list[object]]) -> None:
    if not rows:
        print("  none")
        return
    text_rows = [[_cell(item) for item in row] for row in rows]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in text_rows))
        for index in range(len(headers))
    ]
    print("  " + "  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  " + "  ".join("-" * width for width in widths))
    for row in text_rows:
        print("  " + "  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _status(value: object) -> str:
    text = str(value)
    if text in {"healthy", "available", "ok", "reachable"}:
        return f"OK {text}"
    if text in {"missing", "failed", "unavailable", "unreachable"}:
        return f"FAIL {text}"
    if text in {"skipped", "external-or-not-required", "external"}:
        return f"INFO {text}"
    return text


def _print_doctor(payload: dict[str, Any]) -> None:
    _section("LFG doctor")
    print(
        "Checks local tools, providers, credentials, Hermes profile, MCP, and git ignore rules."
    )
    print()

    tools = payload.get("tools", {})
    if isinstance(tools, dict):
        _section("Tools")
        _table(
            ["Tool", "Status", "Path/Detail"],
            [
                [
                    name,
                    _status(_mapping_or_empty(row).get("status", "-")),
                    _mapping_or_empty(row).get("path")
                    or _mapping_or_empty(row).get("available")
                    or "-",
                ]
                for name, row in tools.items()
            ],
        )
        print()

    providers = payload.get("providers", {})
    if isinstance(providers, dict):
        _section("Provider CLIs")
        _table(
            ["Provider", "Status", "Executable", "Model"],
            [
                [
                    name,
                    _status(_mapping_or_empty(row).get("status", "-")),
                    _mapping_or_empty(row).get("executable")
                    or _mapping_or_empty(row).get("reason")
                    or "-",
                    _mapping_or_empty(row).get("model", "-"),
                ]
                for name, row in providers.items()
            ],
        )
        print()

    _section("Credentials")
    _table(
        ["Agent", "Provider", "Status", "Auth ref"],
        [
            [
                row.get("agent_id", "-"),
                row.get("provider", "-"),
                _status(row.get("status", "-")),
                row.get("auth_ref", "-"),
            ]
            for row in _list_of_dicts(payload.get("credentials"))
        ],
    )
    print()

    endpoints = _list_of_dicts(payload.get("endpoints"))
    if endpoints:
        _section("Endpoints")
        _table(
            ["Agent", "Provider", "Status", "Base URL", "Detail"],
            [
                [
                    row.get("agent_id", "-"),
                    row.get("provider", "-"),
                    _status(row.get("status", "-")),
                    row.get("base_url", "-"),
                    row.get("reason") or row.get("http_status") or "-",
                ]
                for row in endpoints
            ],
        )
        print()

    coordination = _mapping_or_empty(payload.get("coordination"))
    mcp = _mapping_or_empty(coordination.get("mcp"))
    hermes_profile = _mapping_or_empty(coordination.get("hermes_profile"))
    _section("Coordination")
    _table(
        ["Check", "Status", "Detail"],
        [
            [
                "LFG MCP stdio",
                _status(mcp.get("status", "-")),
                f"{mcp.get('tool_count', 0)} tools",
            ],
            [
                "Hermes generated profile",
                _status(hermes_profile.get("status", "-")),
                hermes_profile.get("home") or hermes_profile.get("reason") or "-",
            ],
            [
                "Runtime ignored by git",
                "OK yes" if coordination.get("runtime_ignored") else "FAIL no",
                ".lfg-runtime",
            ],
        ],
    )
    if hermes_profile.get("mcp_test"):
        print()
        print("Hermes MCP test:")
        for line in str(hermes_profile["mcp_test"]).splitlines()[-8:]:
            print(f"  {line}")
    print()

    planner = _mapping_or_empty(payload.get("planning_architect"))
    if planner:
        _section("Planning architect")
        _table(
            ["Detected", "Authenticated", "Model", "Limitation"],
            [
                [
                    planner.get("detected"),
                    planner.get("authenticated"),
                    planner.get("model_identifier", "-"),
                    planner.get("remaining_limitation", "-"),
                ]
            ],
        )


def _mapping_or_empty(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


LAUNCH_PRESETS: dict[str, dict[str, str]] = {
    "default-dev-shop": {
        "description": "Hermes on Codex, Antigravity architect, Codex/Antigravity/Composer workers.",
        "orchestrator": "codex:gpt-5.5?reasoning=high",
        "architect": "antigravity:claude-opus-4.6-thinking",
        "reviewer": "codex:gpt-5.5?reasoning=high",
    },
    "codex-antigravity": {
        "description": "Codex for Hermes and coding, Antigravity for planning.",
        "orchestrator": "codex:gpt-5.5?reasoning=high",
        "architect": "antigravity:claude-opus-4.6-thinking",
        "reviewer": "codex:gpt-5.5?reasoning=high",
    },
    "local-only": {
        "description": "Prefer local Ollama for API-backed review/repair roles.",
        "orchestrator": "codex:gpt-5.5?reasoning=high",
        "architect": "antigravity:claude-opus-4.6-thinking",
        "reviewer": "ollama:qwen3-coder@http://localhost:11434",
    },
    "advanced": {
        "description": "Ask for every model ref and quota setting.",
        "orchestrator": "",
        "architect": "",
        "reviewer": "",
    },
}


FALLBACK_POLICIES = {
    "prompt-before-swap": "Pause and ask before moving active work to a different model.",
    "auto-swap": "Automatically hand off low-quota or failed work to the next eligible provider.",
    "manual-only": "Never swap automatically; only explicit `lfg agent swap` changes models.",
}


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
    profile: SessionProfile, *, name: str | None, preset_name: str | None = None
) -> SessionProfile:
    profile_name = name or _prompt(profile.name, "Session profile")
    if preset_name is None:
        preset_name = _prompt_choice(
            "default-dev-shop",
            "Launch preset",
            {key: value["description"] for key, value in LAUNCH_PRESETS.items()},
        )
    elif preset_name not in LAUNCH_PRESETS:
        raise LfgError(f"Expected one of: {', '.join(LAUNCH_PRESETS)}")
    preset = LAUNCH_PRESETS[preset_name]
    advanced = preset_name == "advanced"

    orchestrator_ref = (
        preset["orchestrator"] or profile.orchestrator.model_profile.model_ref
    )
    architect_ref = preset["architect"] or profile.architect.model_profile.model_ref
    worker_refs = [_default_worker_ref(worker) for worker in profile.workers]
    reviewer_ref = (
        preset["reviewer"] or profile.reviewer.model_profile.model_ref
        if profile.reviewer is not None
        else None
    )
    validator_ref = (
        profile.validator.model_profile.model_ref
        if profile.validator is not None
        else None
    )

    if advanced:
        orchestrator_ref = _prompt(orchestrator_ref, "Hermes orchestrator model ref")
        architect_ref = _prompt(architect_ref, "Architect model ref")
        worker_refs = [
            _prompt(worker_refs[index], f"{worker.agent_id} model ref")
            for index, worker in enumerate(profile.workers)
        ]
        reviewer_ref = (
            _prompt(reviewer_ref, "Reviewer model ref")
            if profile.reviewer is not None and reviewer_ref is not None
            else None
        )
        validator_ref = (
            _prompt(validator_ref, "Validator model ref")
            if profile.validator is not None and validator_ref is not None
            else None
        )

    budget_label = _prompt(
        profile.budget_label,
        "Budget/session label (shown in panes and quota reports)",
    )
    fallback_policy = _prompt_choice(
        profile.fallback_policy
        if profile.fallback_policy in FALLBACK_POLICIES
        else "prompt-before-swap",
        "Fallback/swap policy",
        FALLBACK_POLICIES,
    )
    default_policy = profile.orchestrator.quota_policy
    if advanced:
        quota_policy = QuotaPolicy(
            warning_threshold_percent=_prompt_float(
                default_policy.warning_threshold_percent,
                "Quota warning threshold percent (warn below this)",
            ),
            pause_threshold_percent=_prompt_float(
                default_policy.pause_threshold_percent,
                "Quota pause threshold percent (stop new work below this)",
            ),
            hard_stop_below_pause=_prompt_bool(
                default_policy.hard_stop_below_pause,
                "Hard stop below pause threshold",
            ),
            manual_token_limit=_prompt_optional_int(
                default_policy.manual_token_limit,
                "Manual token budget limit (blank when provider reports quota)",
            ),
        )
    else:
        quota_policy = default_policy
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


def _default_worker_ref(worker: AgentInstance) -> str:
    if worker.executor_adapter == "codex":
        return "codex:gpt-5.5?reasoning=high"
    if worker.executor_adapter == "antigravity":
        return "antigravity:gemini-3.5-flash-low"
    if worker.executor_adapter == "composer":
        return "composer:grok-composer-2.5-fast"
    return worker.model_profile.model_ref


def cmd_launch(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    profile = _build_launch_profile(profile, name=args.profile, preset_name=args.preset)
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
    print("LFG factory launched")
    print()
    print(f"Tmux session: {name}")
    print(
        f"Session profile: {files.project.runtime_directory / 'state' / 'session-profile.json'}"
    )
    print(f"Hermes home: {hermes_profile.home}")
    print(f"Hermes command: {' '.join(hermes_profile.command)}")
    print()
    print("Windows:")
    print("  factory       Hermes, controller, workers, integration")
    print("  observability DAG, workflow, git, quotas, events, logs")
    if args.no_attach:
        print()
        print(f"Attach with: tmux attach -t {name}")
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
    pane_rows = panes(files.project)
    _section("LFG status")
    print(f"Session: {session_name(files.project)}")
    print(f"Runtime: {files.project.runtime_directory}")
    print()
    _section("Tmux panes")
    _table(
        ["Pane", "Title", "PID", "Dead"],
        [[row["pane"], row["title"], row["pid"], row["dead"]] for row in pane_rows],
    )
    return 0


def cmd_observability(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    workflow = load_workflow(files.project.runtime_directory)
    profile = load_session_profile(files.project)
    print(render_dashboard(files), end="")
    print()
    _section("Workflow")
    print(f"State: {workflow.node}")
    print(f"Updated: {time.ctime(workflow.updated_at)}")
    print()
    _section("Agents and quotas")
    _table(
        ["Agent", "Role", "State", "Model", "Remaining", "Confidence"],
        [
            [
                agent.agent_id,
                agent.role,
                agent.state,
                agent.model_profile.model_ref,
                load_quota(files.project.runtime_directory, agent).remaining_percent,
                load_quota(files.project.runtime_directory, agent).confidence,
            ]
            for agent in profile.agents
        ],
    )
    print()
    _section("Tmux")
    _table(
        ["Pane", "Title", "Dead"],
        [[row["pane"], row["title"], row["dead"]] for row in panes(files.project)],
    )
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
    _print_doctor(payload)
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
    models = list_models()
    antigravity = AntigravityClaudePlanner().doctor()
    for model in antigravity.available_models:
        ref = f"antigravity:{_model_slug(model)}"
        if ref not in {item["ref"] for item in models}:
            models.append(
                {
                    "ref": ref,
                    "role_hint": "coder/planner",
                    "transport": "cli",
                    "notes": f"Discovered by agy models: {model}",
                }
            )
    _section("Available model refs")
    _table(
        ["Model ref", "Role", "Transport", "Notes"],
        [
            [item["ref"], item["role_hint"], item["transport"], item["notes"]]
            for item in models
        ],
    )
    return 0


def _model_slug(model: str) -> str:
    return (
        model.lower()
        .replace("(", "")
        .replace(")", "")
        .replace("/", "-")
        .replace(" ", "-")
        .replace("--", "-")
    )


def cmd_model_doctor(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    refs = [agent.model_profile.model_ref for agent in profile.agents]
    _section("Configured model refs")
    _table(
        ["Ref", "Status", "Provider", "Model", "Base URL"],
        [
            [
                item["ref"],
                _status(item["status"]),
                item.get("provider", "-"),
                item.get("model", "-"),
                item.get("base_url", "-"),
            ]
            for item in validate_model_refs(refs)
        ],
    )
    return 0


def cmd_model_configure(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    if args.model_ref:
        parse_model_ref(args.model_ref)
    path = ensure_runtime_secrets_file(files.project.runtime_directory)
    print("Model configuration")
    print()
    print(f"Validated model ref: {args.model_ref or 'none supplied'}")
    print(f"Runtime secrets file: {path}")
    print()
    print(
        "Put provider API keys in your shell environment or this ignored runtime file."
    )
    print("LFG stores env references such as env:OPENAI_API_KEY, not raw secrets.")
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
    _section("Agents")
    _table(
        ["Agent", "Role", "State", "Executor", "Model", "Active task"],
        [
            [
                agent.agent_id,
                agent.role,
                agent.state,
                agent.executor_adapter,
                agent.model_profile.model_ref,
                agent.active_task,
            ]
            for agent in profile.agents
        ],
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
    _section("Quota status")
    _table(
        ["Agent", "Provider", "Model", "Used", "Limit", "Remaining", "Confidence"],
        [
            [
                agent.agent_id,
                quota.provider,
                quota.model,
                quota.used_tokens,
                quota.limit_tokens,
                quota.remaining_percent,
                quota.confidence,
            ]
            for agent in profile.agents
            for quota in [load_quota(files.project.runtime_directory, agent)]
        ],
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
    print(f"Updated quota for {args.agent_id}")
    print(f"Provider/model: {quota.provider}:{quota.model}")
    print(f"Remaining: {quota.remaining_percent:g}% ({quota.confidence})")
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
    setup = sub.add_parser("setup")
    setup.add_argument("--yes", action="store_true")
    setup.set_defaults(func=cmd_setup)
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
        ("observe", cmd_observability),
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
    launch.add_argument("--preset", choices=sorted(LAUNCH_PRESETS))
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
