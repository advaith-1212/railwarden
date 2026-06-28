from __future__ import annotations

import json
from pathlib import Path

from lfg.config.models import ProjectFiles
from lfg.git import run_git
from lfg.providers.health import load_state, refresh_state
from lfg.runtime.events import read_events
from lfg.runtime.tasks import load_tasks
from lfg.scheduler.dag import Dag

STATE_MARKERS = {
    "planned": ".",
    "ready": "R",
    "assigned": "A",
    "running": "*",
    "handoff_needed": "H",
    "cooldown_wait": "C",
    "validating": "V",
    "integration_ready": "I",
    "integrating": "M",
    "merged": "D",
    "blocked": "B",
    "failed": "F",
}


def _task_status(tasks: list[dict[str, object]], package_id: str) -> str:
    for task in tasks:
        if str(task.get("package_id", "")) == package_id:
            return str(task.get("status", "planned"))
    return "planned"


def _git_graph(repository: Path) -> str:
    result = run_git(
        repository,
        "log",
        "--graph",
        "--oneline",
        "--decorate",
        "--all",
        "-n",
        "10",
        check=False,
    )
    return result.stdout.strip() or result.stderr.strip()


def render_dashboard(files: ProjectFiles) -> str:
    config = files.project
    tasks = load_tasks(config.runtime_directory)
    lines = [f"LFG Dashboard: {config.name}", ""]
    lines.append("DAG")
    for package_id in Dag(files.packages).topological() if files.packages else ():
        package = files.packages[package_id]
        status = _task_status(tasks, package_id)
        marker = STATE_MARKERS.get(status, "?")
        deps = ", ".join(package.dependencies) if package.dependencies else "-"
        lines.append(
            f"[{marker}] {package_id} {package.name} deps={deps} status={status}"
        )
    if not files.packages:
        lines.append("(no work packages)")
    lines.extend(["", "Queue"])
    for task in tasks:
        lines.append(
            f"- {task.get('id')} {task.get('status')} provider={task.get('provider', '-')}"
        )
    if not tasks:
        lines.append("- empty")
    lines.extend(["", "Providers"])
    for provider in config.worker_providers:
        state = refresh_state(load_state(config.runtime_directory, provider))
        cooldown = f" until {int(state.cooldown_until)}" if state.cooldown_until else ""
        lines.append(f"- {provider}: {state.status}{cooldown}")
    lines.extend(["", "Recent Events"])
    for event in read_events(config.runtime_directory, limit=8):
        payload = json.dumps(event.get("payload", {}), sort_keys=True)
        lines.append(f"- {event.get('type')} task={event.get('task_id')} {payload}")
    if config.monitoring_git_graph:
        lines.extend(["", "Git"])
        graph = _git_graph(config.repository_root)
        lines.extend(graph.splitlines() if graph else ["(no git history)"])
    return "\n".join(lines) + "\n"
