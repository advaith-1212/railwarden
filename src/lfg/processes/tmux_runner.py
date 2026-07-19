from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path

from lfg.processes.supervisor import ManagedCommand, ManagedProcess, coerce_command
from lfg.processes.supervisor import launch_managed
from lfg.tmux.session import pane_alive, tmux
from lfg.util.atomic import atomic_write_json, atomic_write_text


def pane_map(runtime_dir: Path) -> dict[str, str]:
    path = runtime_dir / "state" / "tmux-session.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    panes = payload.get("panes", {}) if isinstance(payload, dict) else {}
    if not isinstance(panes, dict):
        return {}
    return {str(key): str(value) for key, value in panes.items()}


def pane_for_worker(
    runtime_dir: Path, *, agent_id: str | None, provider: str
) -> str | None:
    panes = pane_map(runtime_dir)
    candidates: list[str] = []
    if agent_id and agent_id in panes:
        candidates.append(panes[agent_id])
    candidates.extend(pane for key, pane in panes.items() if key.startswith(provider))
    seen: set[str] = set()
    for pane in candidates:
        if pane in seen:
            continue
        seen.add(pane)
        if pane_alive(pane):
            return pane
    return None


def launch_tmux_managed(
    command: ManagedCommand | list[str],
    *,
    cwd: Path | None = None,
    log_path: Path | None = None,
    pid_path: Path,
    script_path: Path,
    pane_id: str,
    provider: str,
) -> ManagedProcess:
    """Run a provider command in a worker pane so output is visible.

    Always prefers the pane: writes a small runner script, clears the idle shell
    line, and ``send-keys`` ``bash <script>``. The script streams provider
    stdout/stderr to both the pane and the task log.

    Headless ``launch_managed`` is only used when the pane is dead or tmux
    injection fails.
    """
    managed_command = coerce_command(command, cwd=cwd, log_path=log_path)
    managed_command.log_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": 0,
        "pgid": 0,
        "command": list(managed_command.argv),
        "cwd": str(managed_command.cwd),
        "stdin_path": str(managed_command.stdin_path)
        if managed_command.stdin_path is not None
        else None,
        "log_path": str(managed_command.log_path),
        "provider": provider,
        "mode": "tmux",
        "pane_id": pane_id,
        "status": "launching",
        "started_at": time.time(),
        "updated_at": time.time(),
    }
    atomic_write_json(pid_path, payload)
    if not pane_alive(pane_id):
        return launch_managed(
            managed_command,
            pid_path=pid_path,
        )
    try:
        atomic_write_text(script_path, _visible_runner_script(managed_command, pid_path, provider))
        script_path.chmod(0o700)
        # Idle panes are shells; interrupt any prior task and clear the line.
        tmux(["send-keys", "-t", pane_id, "C-c"], check=False)
        tmux(["send-keys", "-t", pane_id, "C-c"], check=False)
        tmux(
            [
                "send-keys",
                "-t",
                pane_id,
                "C-u",
                f"bash {shlex.quote(str(script_path))}",
                "C-m",
            ]
        )
    except subprocess.CalledProcessError:
        return launch_managed(
            managed_command,
            pid_path=pid_path,
        )
    return ManagedProcess(
        pid=0,
        pgid=0,
        command=managed_command.argv,
        log_path=managed_command.log_path,
    )


def _visible_runner_script(
    command: ManagedCommand,
    pid_path: Path,
    provider: str,
) -> str:
    """Bash entry that runs the provider in-pane with live output + log tee."""
    payload = {
        "argv": list(command.argv),
        "cwd": str(command.cwd),
        "stdin_path": str(command.stdin_path) if command.stdin_path else None,
        "env": dict(command.env or {}),
        "log_path": str(command.log_path),
        "pid_path": str(pid_path),
        "provider": provider,
    }
    payload_json = json.dumps(payload)
    return f"""#!/usr/bin/env bash
set +e
python3 - <<'PY'
import json
import os
import pathlib
import subprocess
import sys
import time

payload = json.loads({payload_json!r})
pid_path = pathlib.Path(payload["pid_path"])
log_path = pathlib.Path(payload["log_path"])
log_path.parent.mkdir(parents=True, exist_ok=True)
pid_path.parent.mkdir(parents=True, exist_ok=True)

def write_state(**updates):
    state = {{
        "provider": payload["provider"],
        "mode": "tmux",
        "command": payload["argv"],
        "cwd": payload["cwd"],
        "stdin_path": payload["stdin_path"],
        "log_path": str(log_path),
        "updated_at": time.time(),
    }}
    state.update(updates)
    pid_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\\n")

env = os.environ.copy()
env.update(payload.get("env") or {{}})
stdin_handle = None
if payload["stdin_path"]:
    stdin_handle = pathlib.Path(payload["stdin_path"]).open("r", encoding="utf-8")

banner = f"[LFG] starting {{payload['provider']}}: {{payload['argv']}}\\n"
sys.stdout.write(banner)
sys.stdout.flush()
with log_path.open("a", encoding="utf-8") as log:
    log.write(banner)
    log.flush()
    process = subprocess.Popen(
        payload["argv"],
        cwd=payload["cwd"],
        stdin=stdin_handle,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    if stdin_handle is not None:
        stdin_handle.close()
    try:
        pgid = os.getpgid(process.pid)
    except OSError:
        pgid = process.pid
    write_state(
        status="running",
        pid=process.pid,
        pgid=pgid,
        started_at=time.time(),
    )
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        log.write(line)
        log.flush()
    rc = process.wait()
    footer = f"[LFG] {{payload['provider']}} exited with {{rc}}\\n"
    sys.stdout.write(footer)
    sys.stdout.flush()
    log.write(footer)
    write_state(
        status="exited",
        pid=process.pid,
        pgid=pgid,
        returncode=rc,
        exited_at=time.time(),
    )
sys.exit(rc)
PY
"""
