from __future__ import annotations

import hashlib
import re
import subprocess

from lfg.config.models import ProjectConfig
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


def create_session(config: ProjectConfig, *, attach: bool) -> str:
    name = session_name(config)
    if has_session(name):
        if attach:
            subprocess.run(["tmux", "attach-session", "-t", name], check=False)
        return name
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
