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
from lfg.integration.manager import integrate_one
from lfg.migration.tmom import dry_run_tmom_adoption
from lfg.planning.antigravity import AntigravityClaudePlanner
from lfg.planning.pipeline import approve_latest_plan, create_pending_plan
from lfg.providers.adapters import default_adapters
from lfg.runtime.events import read_events
from lfg.runtime.tasks import load_tasks, transition_task
from lfg.scheduler.classifier import classify_packages, execution_plan
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


def cmd_dashboard(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(render_dashboard(files), end="")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(json.dumps(read_events(files.project.runtime_directory, limit=args.limit), indent=2))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    adapters = default_adapters()
    provider_status = {
        name: adapters[name].health_check()
        for name in files.project.worker_providers
        if name in adapters
    }
    planner = AntigravityClaudePlanner(files.project.planner_model).doctor()
    payload = {
        "providers": provider_status,
        "planning_architect": planner.__dict__,
        "coordination": {
            "agent": "Hermes",
            "mailbox": str(
                files.project.runtime_directory / "state" / "hermes-mailbox.jsonl"
            ),
        },
    }
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
        if str(task.get("id")) == args.task_id or str(task.get("package_id")) == args.task_id:
            payload: dict[str, object] = {"manual": True}
            if args.provider:
                payload["provider_override"] = args.provider
            transition_task(files.project.runtime_directory, task, "handoff_needed", payload)
            print(f"Marked {task.get('id')} for handoff.")
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
    print("Commands: goal <text>, approve plan, status, dag, agents, tasks, logs, pause, resume, stop-after-current, quit")
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
            pending = create_pending_plan(files.project, line.removeprefix("goal ").strip())
            print(f"Created pending plan {pending.run_id}. Run `approve plan` to execute.")
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
            cmd_handoff(argparse.Namespace(task_id=parts[1], provider=parts[2] if len(parts) > 2 else None))
        elif line.startswith("retry ") or line.startswith("unblock "):
            task_id = line.split()[1]
            tasks = load_tasks(files.project.runtime_directory)
            for task in tasks:
                if str(task.get("id")) == task_id or str(task.get("package_id")) == task_id:
                    transition_task(files.project.runtime_directory, task, "ready")
                    print(f"Marked {task.get('id')} ready.")
                    break
        elif line.startswith("block "):
            task_id = line.split()[1]
            tasks = load_tasks(files.project.runtime_directory)
            for task in tasks:
                if str(task.get("id")) == task_id or str(task.get("package_id")) == task_id:
                    transition_task(files.project.runtime_directory, task, "blocked", {"manual": True})
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
