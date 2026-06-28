from __future__ import annotations

import json
import shlex
import time
from pathlib import Path

from lfg.processes.supervisor import ManagedProcess
from lfg.tmux.session import tmux
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
    if agent_id and agent_id in panes:
        return panes[agent_id]
    for key, pane in panes.items():
        if key.startswith(provider):
            return pane
    return None


def launch_tmux_managed(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    pid_path: Path,
    script_path: Path,
    pane_id: str,
    provider: str,
) -> ManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": 0,
        "pgid": 0,
        "command": command,
        "log_path": str(log_path),
        "provider": provider,
        "mode": "tmux",
        "pane_id": pane_id,
        "status": "launching",
        "created_at": time.time(),
    }
    atomic_write_json(pid_path, payload)
    atomic_write_text(script_path, _script(command, cwd, log_path, pid_path, provider))
    script_path.chmod(0o700)
    tmux(["send-keys", "-t", pane_id, f"bash {shlex.quote(str(script_path))}", "C-m"])
    return ManagedProcess(pid=0, pgid=0, command=tuple(command), log_path=log_path)


def _script(
    command: list[str],
    cwd: Path,
    log_path: Path,
    pid_path: Path,
    provider: str,
) -> str:
    quoted = " ".join(shlex.quote(item) for item in command)
    update_running = _python_update(
        pid_path,
        {
            "provider": provider,
            "mode": "tmux",
            "status": "running",
            "log_path": str(log_path),
            "command": command,
        },
        pid_expr="child",
        pgid_expr="pgid",
    )
    update_exited = _python_update(
        pid_path,
        {
            "provider": provider,
            "mode": "tmux",
            "status": "exited",
            "log_path": str(log_path),
            "command": command,
        },
        returncode_expr="rc",
    )
    return f"""#!/usr/bin/env bash
set +e
cd {shlex.quote(str(cwd))}
mkdir -p {shlex.quote(str(log_path.parent))} {shlex.quote(str(pid_path.parent))}
echo "[LFG] starting {provider}: {quoted}" | tee -a {shlex.quote(str(log_path))}
({quoted}) 2>&1 | tee -a {shlex.quote(str(log_path))} &
child=$!
pgid=$(python3 -c 'import os,sys; print(os.getpgid(int(sys.argv[1])))' "$child" 2>/dev/null || echo "$child")
{update_running}
wait "$child"
rc=$?
{update_exited}
echo "[LFG] {provider} exited with $rc" | tee -a {shlex.quote(str(log_path))}
exit "$rc"
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
