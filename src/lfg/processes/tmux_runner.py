from __future__ import annotations

import json
import shlex
import time
from pathlib import Path

from lfg.processes.supervisor import ManagedCommand, ManagedProcess, coerce_command
from lfg.tmux.session import tmux
from lfg.util.atomic import atomic_write_json, atomic_write_text
from lfg.workers.pane_runtime import task_pane_command


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
    if agent_id and agent_id in panes:
        return panes[agent_id]
    for key, pane in panes.items():
        if key.startswith(provider):
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
    pane_command = task_pane_command(
        argv=managed_command.argv,
        cwd=managed_command.cwd,
        log_path=managed_command.log_path,
    )
    if managed_command.stdin_path is not None:
        atomic_write_text(script_path, _script(managed_command, pid_path, provider))
        script_path.chmod(0o700)
        tmux(["send-keys", "-t", pane_id, f"bash {shlex.quote(str(script_path))}", "C-m"])
    else:
        tmux(["send-keys", "-t", pane_id, "C-c"])
        tmux(["send-keys", "-t", pane_id, pane_command, "C-m"])
    return ManagedProcess(
        pid=0,
        pgid=0,
        command=managed_command.argv,
        log_path=managed_command.log_path,
    )


def _script(
    command: ManagedCommand,
    pid_path: Path,
    provider: str,
) -> str:
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

with log_path.open("a", encoding="utf-8") as log:
    log.write(f"[LFG] starting {{payload['provider']}}: {{payload['argv']}}\\n")
    log.flush()
    process = subprocess.Popen(
        payload["argv"],
        cwd=payload["cwd"],
        stdin=stdin_handle,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
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
    rc = process.wait()
    write_state(
        status="exited",
        pid=process.pid,
        pgid=pgid,
        returncode=rc,
        exited_at=time.time(),
    )
    log.write(f"[LFG] {{payload['provider']}} exited with {{rc}}\\n")
sys.exit(rc)
PY
"""


def _python_update(
    pid_path: Path,
    payload: dict[str, object],
    *,
    pid_expr: str | None = None,
    pgid_expr: str | None = None,
    returncode_expr: str | None = None,
) -> str:
    body = dict(payload)
    body["updated_at"] = time.time()
    json_payload = json.dumps(body)
    env_parts: list[str] = []
    if pid_expr:
        env_parts.append(f'LFG_PID_VALUE="${{{pid_expr}}}"')
    if pgid_expr:
        env_parts.append(f'LFG_PGID_VALUE="${{{pgid_expr}}}"')
    if returncode_expr:
        env_parts.append(f'LFG_RETURNCODE_VALUE="${{{returncode_expr}}}"')
    prefix = " ".join(env_parts)
    command = f"{prefix} python3 - <<'PY'" if prefix else "python3 - <<'PY'"
    lines = [
        command,
        "import json, pathlib",
        "import os",
        f"path = pathlib.Path({str(pid_path)!r})",
        f"payload = json.loads({json_payload!r})",
    ]
    if pid_expr:
        lines.append("payload['pid'] = int(os.environ['LFG_PID_VALUE'])")
    if pgid_expr:
        lines.append("payload['pgid'] = int(os.environ['LFG_PGID_VALUE'])")
    if returncode_expr:
        lines.append("payload['returncode'] = int(os.environ['LFG_RETURNCODE_VALUE'])")
    lines.extend(
        [
            "path.parent.mkdir(parents=True, exist_ok=True)",
            "path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\\n')",
            "PY",
        ]
    )
    return "\n".join(lines)
