from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from dataclasses import dataclass

from lfg.config.models import ProjectConfig
from lfg.hermes.profile import HermesRuntimeProfile
from lfg.runtime.session import AgentInstance, SessionProfile
from lfg.util.atomic import atomic_write_json


def normalized_project_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def session_name(config: ProjectConfig) -> str:
    digest = hashlib.sha1(
        str(config.repository_root.resolve()).encode("utf-8")
    ).hexdigest()[:8]
    return f"lfg-{normalized_project_name(config.name)}-{digest}"


def tmux(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *command], text=True, capture_output=True, check=check
    )


def has_session(name: str) -> bool:
    return tmux(["has-session", "-t", name], check=False).returncode == 0


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
    hermes_command = " ".join(shlex.quote(item) for item in hermes_profile.command)
    specs = [
        PaneSpec(
            "controller",
            "Controller | role=factory exec=lfg provider=local model=deterministic | healthy",
            f"cd {root} && mkdir -p {log_dir} && PYTHONUNBUFFERED=1 lfg controller 2>&1 | tee -a {shlex.quote(str(config.runtime_directory / 'logs' / 'controller.log'))}",
            "factory",
        ),
        PaneSpec(
            "hermes",
            _pane_title(
                profile.orchestrator, label="Hermes", budget=profile.budget_label
            ),
            f"cd {root} && {hermes_command}",
            "factory",
        ),
        PaneSpec(
            "integration",
            "Integration | role=integrator exec=lfg provider=git model=deterministic | healthy",
            f"cd {root} && watch -n 5 lfg observability",
            "factory",
        ),
        PaneSpec(
            "observability",
            "Observability | role=monitor exec=lfg provider=langgraph model=state | healthy",
            f"cd {root} && watch -n 5 lfg observability",
            "observability",
        ),
    ]
    for agent in profile.workers:
        specs.append(
            PaneSpec(
                agent.agent_id,
                _pane_title(agent, label=agent.agent_id, budget=profile.budget_label),
                f"cd {root} && lfg worker {shlex.quote(agent.executor_adapter)} 2>&1 | tee -a {shlex.quote(str(config.runtime_directory / 'logs' / f'{agent.agent_id}.log'))}",
                "factory",
            )
        )
    return specs


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
        controller: f"cd {root!r} && PYTHONUNBUFFERED=1 lfg controller 2>&1 | tee -a {str(log_dir / 'controller.log')!r}",
        hermes: f"cd {root!r} && lfg hermes",
        codex: f"cd {root!r} && lfg worker codex 2>&1 | tee -a {str(log_dir / 'codex-worker.log')!r}",
        gemini: f"cd {root!r} && lfg worker antigravity 2>&1 | tee -a {str(log_dir / 'antigravity-worker.log')!r}",
        composer: f"cd {root!r} && lfg worker composer 2>&1 | tee -a {str(log_dir / 'composer-worker.log')!r}",
        status: f"cd {root!r} && watch -n 5 lfg dashboard",
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
    factory = tmux(
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
    pane_ids = {"controller": factory}
    factory_specs = [spec for spec in specs if spec.window == "factory"]
    for index, spec in enumerate(factory_specs[1:], start=1):
        target = factory if index == 1 else pane_ids[factory_specs[index - 1].key]
        split_args = [
            "split-window",
            "-v" if index % 2 else "-h",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            target,
            "-c",
            root,
        ]
        pane_ids[spec.key] = tmux(split_args).stdout.strip()
    observability_window = tmux(
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
    for spec in specs:
        if spec.window == "observability":
            pane_ids[spec.key] = observability_window
            break
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
        },
    )
    if attach:
        subprocess.run(["tmux", "attach-session", "-t", name], check=False)
    return name


def stop_session(config: ProjectConfig) -> bool:
    name = session_name(config)
    if not has_session(name):
        return False
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
