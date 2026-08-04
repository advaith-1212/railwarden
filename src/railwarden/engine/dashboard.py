from __future__ import annotations

import json
from pathlib import Path

from railwarden.config.models import ProjectFiles
from railwarden.git import run_git
from railwarden.providers.health import load_state, refresh_state
from railwarden.runtime.events import read_events
from railwarden.runtime.tasks import load_tasks
from railwarden.scheduler.dag import Dag

STATE_MARKERS = {
    "planned": ".",
    "ready": "R",
    "assigned": "A",
    "running": "*",
    "handoff_needed": "H",
    "cooldown_wait": "C",
    "validating": "V",
    "validated": "v",
    "review_ready": "Q",
    "reviewing": "R",
    "review_passed": "P",
    "merge_ready": "!",
    "merge_approved": "G",
    "integration_ready": "I",
    "integrating": "M",
    "merged": "D",
    "rejected": "X",
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
    lines = [f"RailWarden Dashboard: {config.name}", ""]
    workflow_path = config.runtime_directory / "langgraph" / "default.json"
    if workflow_path.exists():
        try:
            payload = json.loads(workflow_path.read_text(encoding="utf-8"))
            lines.extend([f"Factory State: {payload.get('node', '-')}", ""])
        except Exception:
            lines.extend(["Factory State: unreadable", ""])
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
            f"- {task.get('id')} {task.get('status')} provider={task.get('provider', '-')} "
            f"validation={_evidence_status(task.get('package_validation'))} "
            f"review={_evidence_status(task.get('review'))}"
        )
    if not tasks:
        lines.append("- empty")
    lines.extend(["", "Providers"])
    for provider in config.worker_providers:
        state = refresh_state(load_state(config.runtime_directory, provider))
        cooldown = f" until {int(state.cooldown_until)}" if state.cooldown_until else ""
        lines.append(f"- {provider}: {state.status}{cooldown}")
    lines.extend(["", "Validation / Review"])
    for task in tasks:
        validation = _evidence_path(task.get("package_validation"))
        review = _evidence_path(task.get("review"))
        if validation or review:
            lines.append(
                f"- {task.get('id')}: validation={validation or '-'} review={review or '-'}"
            )
    lines.extend(["", "Recent Events"])
    for event in read_events(config.runtime_directory, limit=8):
        payload = json.dumps(event.get("payload", {}), sort_keys=True)
        lines.append(f"- {event.get('type')} task={event.get('task_id')} {payload}")
    if config.monitoring_git_graph:
        lines.extend(["", "Git"])
        graph = _git_graph(config.repository_root)
        lines.extend(graph.splitlines() if graph else ["(no git history)"])
    return "\n".join(lines) + "\n"


def _evidence_status(value: object) -> str:
    if not isinstance(value, dict):
        return "-"
    return str(value.get("status", "-"))


def _evidence_path(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    path = value.get("evidence_path")
    return str(path) if path else None
