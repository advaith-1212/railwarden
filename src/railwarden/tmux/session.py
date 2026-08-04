from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

from railwarden.config.models import ProjectConfig
from railwarden.hermes.profile import HermesRuntimeProfile
from railwarden.providers.adapters import default_adapters
from railwarden.runtime.session import AgentInstance, SessionProfile
from railwarden.util.atomic import atomic_write_json
from railwarden.workers.pane_runtime import idle_pane_command


def normalized_project_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def session_name(config: ProjectConfig) -> str:
    digest = hashlib.sha1(
        str(config.repository_root.resolve()).encode("utf-8")
    ).hexdigest()[:8]
    return f"railwarden-{normalized_project_name(config.name)}-{digest}"


def tmux(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *command], text=True, capture_output=True, check=check
    )


def has_session(name: str) -> bool:
    return tmux(["has-session", "-t", name], check=False).returncode == 0


def pane_alive(pane_id: str) -> bool:
    return (
        tmux(
            ["list-panes", "-a", "-F", "#{pane_id}\t#{pane_dead}", "-f", f"#{pane_id}"],
            check=False,
        ).returncode
        == 0
    )


@dataclass(frozen=True)
class PaneSpec:
    key: str
    title: str
    command: str
    window: str


def launch_layout(
    config: ProjectConfig,
    profile: SessionProfile,
    hermes_profile: HermesRuntimeProfile,
) -> list[PaneSpec]:
    root = shlex.quote(str(config.repository_root))
    log_dir = shlex.quote(str(config.runtime_directory / "logs"))
    source_env = _source_env_command(hermes_profile.env_path)
    hermes_command = " ".join(shlex.quote(item) for item in hermes_profile.command)
    observability_loop = _portable_status_loop("warden observability")
    controller_command = (
        f"{source_env} && cd {root} && mkdir -p {log_dir} && "
        f"PYTHONUNBUFFERED=1 warden controller 2>&1 | tee -a "
        f"{shlex.quote(str(config.runtime_directory / 'logs' / 'controller.log'))}"
    )
    adapters = default_adapters()
    specs = [
        PaneSpec(
            "hermes",
            _pane_title(
                profile.orchestrator, label="Hermes", budget=profile.budget_label
            ),
            f"{source_env} && cd {root} && {hermes_command}",
            "factory",
        ),
        PaneSpec(
            "controller",
            "Controller | role=factory exec=warden provider=local model=deterministic | healthy",
            controller_command,
            "observability",
        ),
        PaneSpec(
            "observability",
            "Observability | role=monitor exec=warden provider=langgraph model=state | healthy",
            f"{source_env} && cd {root} && {observability_loop}",
            "observability",
        ),
    ]
    for agent in profile.workers[:5]:
        adapter = adapters.get(agent.executor_adapter)
        if adapter is None:
            idle = (
                f"{source_env} && cd {root} && "
                f"echo {shlex.quote(f'{agent.agent_id} ready')} && exec ${{SHELL:-/bin/sh}}"
            )
        else:
            idle = f"{source_env} && {idle_pane_command(config, agent, adapter)}"
        specs.append(
            PaneSpec(
                agent.agent_id,
                _pane_title(agent, label=agent.agent_id, budget=profile.budget_label),
                idle,
                "factory",
            )
        )
    return specs


def send_worker_message(
    config: ProjectConfig,
    *,
    agent_id: str | None = None,
    task_id: str | None = None,
    message: str,
) -> dict[str, Any]:
    state_path = config.runtime_directory / "state" / "tmux-session.json"
    if not state_path.exists():
        raise RuntimeError("No tmux session metadata found")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    panes = payload.get("panes", {})
    if not isinstance(panes, dict):
        raise RuntimeError("Invalid tmux session metadata")
    pane_key = agent_id
    if pane_key is None and task_id:
        tasks_path = config.runtime_directory / "state" / "tasks.json"
        if tasks_path.exists():
            tasks_payload = json.loads(tasks_path.read_text(encoding="utf-8"))
            for task in tasks_payload.get("tasks", []):
                if str(task.get("id")) == task_id:
                    pane_key = str(task.get("agent_id") or task.get("provider") or "")
                    break
    if not pane_key:
        raise RuntimeError("agent_id or task_id is required")
    pane_id = panes.get(pane_key)
    if pane_id is None:
        raise RuntimeError(f"No pane registered for worker: {pane_key}")
    tmux(["send-keys", "-t", str(pane_id), message, "C-m"])
    return {"status": "sent", "agent_id": pane_key, "pane": str(pane_id)}


def _pane_title(agent: AgentInstance, *, label: str, budget: str) -> str:
    return (
        f"{label} | role={agent.role} exec={agent.executor_adapter} "
        f"provider={agent.model_profile.provider} model={agent.model_profile.model_ref} "
        f"| {agent.state} | budget={budget}"
    )


def create_session(
    config: ProjectConfig,
    *,
    attach: bool,
    profile: SessionProfile | None = None,
    hermes_profile: HermesRuntimeProfile | None = None,
) -> str:
    name = session_name(config)
    if has_session(name):
        if attach:
            subprocess.run(["tmux", "attach-session", "-t", name], check=False)
        return name
    if profile is not None and hermes_profile is not None:
        return _create_v2_session(
            config,
            name=name,
            attach=attach,
            specs=launch_layout(config, profile, hermes_profile),
        )
    root = str(config.repository_root)
    runtime = config.runtime_directory
    log_dir = runtime / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    controller = tmux(
        [
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            name,
            "-n",
            "factory",
            "-c",
            root,
        ]
    ).stdout.strip()
    hermes = tmux(
        [
            "split-window",
            "-v",
            "-p",
            "35",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            controller,
            "-c",
            root,
        ]
    ).stdout.strip()
    codex = tmux(
        [
            "split-window",
            "-h",
            "-p",
            "62",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            controller,
            "-c",
            root,
        ]
    ).stdout.strip()
    gemini = tmux(
        [
            "split-window",
            "-v",
            "-p",
            "75",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            codex,
            "-c",
            root,
        ]
    ).stdout.strip()
    composer = tmux(
        [
            "split-window",
            "-v",
            "-p",
            "66",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            gemini,
            "-c",
            root,
        ]
    ).stdout.strip()
    status = tmux(
        [
            "split-window",
            "-v",
            "-p",
            "50",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            composer,
            "-c",
            root,
        ]
    ).stdout.strip()
    titles = {
        controller: "Factory Controller",
        hermes: "Hermes Console",
        codex: "Codex - GPT-5.5 High",
        gemini: "Antigravity - Gemini 3.1 Pro High",
        composer: "Grok Composer 2.5",
        status: "DAG / Queue / Integration Status",
    }
    for pane, title in titles.items():
        tmux(["select-pane", "-t", pane, "-T", title])
    tmux(["set-option", "-t", name, "mouse", "on"])
    tmux(["set-option", "-t", name, "remain-on-exit", "on"])
    tmux(["set-option", "-t", name, "pane-border-status", "top"])
    tmux(["set-option", "-t", name, "pane-border-format", " #{pane_title} "])
    commands = {
        controller: f"cd {root!r} && PYTHONUNBUFFERED=1 warden controller 2>&1 | tee -a {str(log_dir / 'controller.log')!r}",
        hermes: f"cd {root!r} && warden hermes",
        codex: f"cd {root!r} && warden worker codex 2>&1 | tee -a {str(log_dir / 'codex-worker.log')!r}",
        gemini: f"cd {root!r} && warden worker antigravity 2>&1 | tee -a {str(log_dir / 'antigravity-worker.log')!r}",
        composer: f"cd {root!r} && warden worker composer 2>&1 | tee -a {str(log_dir / 'composer-worker.log')!r}",
        status: f"cd {root!r} && {_portable_status_loop('warden dashboard')}",
    }
    for pane, command in commands.items():
        tmux(["send-keys", "-t", pane, command, "C-m"])
    atomic_write_json(
        runtime / "state" / "tmux-session.json",
        {
            "session": name,
            "panes": {
                "controller": controller,
                "hermes": hermes,
                "codex": codex,
                "antigravity": gemini,
                "composer": composer,
                "status": status,
            },
        },
    )
    if attach:
        tmux(["select-window", "-t", f"{name}:factory"])
        subprocess.run(["tmux", "attach-session", "-t", name], check=False)
    return name


def _create_v2_session(
    config: ProjectConfig,
    *,
    name: str,
    attach: bool,
    specs: list[PaneSpec],
) -> str:
    root = str(config.repository_root)
    config.runtime_directory.mkdir(parents=True, exist_ok=True)
    (config.runtime_directory / "logs").mkdir(parents=True, exist_ok=True)
    factory_specs = [spec for spec in specs if spec.window == "factory"]
    hermes_spec = next((spec for spec in factory_specs if spec.key == "hermes"), None)
    worker_specs = [spec for spec in factory_specs if spec.key != "hermes"]
    if hermes_spec is None:
        raise RuntimeError("Factory layout requires a Hermes pane")

    hermes_pane = tmux(
        [
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            name,
            "-n",
            "factory",
            "-c",
            root,
        ]
    ).stdout.strip()
    pane_ids = {"hermes": hermes_pane}
    worker_area = tmux(
        [
            "split-window",
            "-h",
            "-p",
            "50",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            hermes_pane,
            "-c",
            root,
        ]
    ).stdout.strip()
    if worker_specs:
        pane_ids[worker_specs[0].key] = worker_area
        for index in range(1, len(worker_specs)):
            target = worker_specs[index - 1].key
            pane_ids[worker_specs[index].key] = tmux(
                [
                    "split-window",
                    "-v",
                    "-P",
                    "-F",
                    "#{pane_id}",
                    "-t",
                    pane_ids[target],
                    "-c",
                    root,
                ]
            ).stdout.strip()

    observability_specs = [spec for spec in specs if spec.window == "observability"]
    observability_root = tmux(
        [
            "new-window",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            name,
            "-n",
            "observability",
            "-c",
            root,
        ]
    ).stdout.strip()
    if observability_specs:
        pane_ids[observability_specs[0].key] = observability_root
        for index in range(1, len(observability_specs)):
            pane_ids[observability_specs[index].key] = tmux(
                [
                    "split-window",
                    "-v",
                    "-P",
                    "-F",
                    "#{pane_id}",
                    "-t",
                    pane_ids[observability_specs[index - 1].key],
                    "-c",
                    root,
                ]
            ).stdout.strip()

    for spec in specs:
        pane = pane_ids[spec.key]
        tmux(["select-pane", "-t", pane, "-T", spec.title])
        tmux(["send-keys", "-t", pane, spec.command, "C-m"])

    tmux(["set-option", "-t", name, "mouse", "on"])
    tmux(["set-option", "-t", name, "remain-on-exit", "on"])
    tmux(["set-option", "-t", name, "pane-border-status", "top"])
    tmux(["set-option", "-t", name, "pane-border-format", " #{pane_title} "])
    atomic_write_json(
        config.runtime_directory / "state" / "tmux-session.json",
        {
            "session": name,
            "windows": ["factory", "observability"],
            "panes": pane_ids,
            "layout": [spec.__dict__ for spec in specs],
            "factory_layout": "hermes_left_workers_right",
        },
    )
    if attach:
        tmux(["select-window", "-t", f"{name}:factory"])
        tmux(["select-pane", "-t", hermes_pane])
        subprocess.run(["tmux", "attach-session", "-t", name], check=False)
    return name


def _portable_status_loop(command: str) -> str:
    quoted = shlex.quote(command)
    return (
        "if command -v watch >/dev/null 2>&1; then "
        f"watch -n 5 {quoted}; "
        "else "
        f"while true; do clear; date; sh -c {quoted}; sleep 5; done; "
        "fi"
    )


def _source_env_command(path: object) -> str:
    quoted = shlex.quote(str(path))
    return f"set -a; if [ -f {quoted} ]; then . {quoted}; fi; set +a"


def stop_session(config: ProjectConfig) -> bool:
    name = session_name(config)
    if not has_session(name):
        return False
    import json

    from railwarden.processes.supervisor import terminate_process_group

    processes_dir = config.runtime_directory / "processes"
    if processes_dir.exists():
        for process_file in processes_dir.glob("*.json"):
            try:
                payload = json.loads(process_file.read_text(encoding="utf-8"))
                pgid = int(payload.get("pgid", payload.get("pid", 0)))
                if pgid > 0:
                    terminate_process_group(pgid)
            except Exception:
                pass
    tmux(["kill-session", "-t", name])
    return True


def panes(config: ProjectConfig) -> list[dict[str, str]]:
    name = session_name(config)
    if not has_session(name):
        return []
    text = tmux(
        [
            "list-panes",
            "-t",
            f"{name}:factory",
            "-F",
            "#{pane_id}\t#{pane_title}\t#{pane_pid}\t#{pane_dead}",
        ]
    ).stdout
    result = []
    for line in text.splitlines():
        pane_id, title, pid, dead = line.split("\t")
        result.append({"pane": pane_id, "title": title, "pid": pid, "dead": dead})
    return result
