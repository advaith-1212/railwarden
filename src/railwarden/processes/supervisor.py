from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from railwarden.util.atomic import atomic_write_json


@dataclass(frozen=True)
class ManagedCommand:
    argv: tuple[str, ...]
    cwd: Path
    log_path: Path
    stdin_path: Path | None = None
    env: Mapping[str, str] | None = None


@dataclass(frozen=True)
class ManagedProcess:
    pid: int
    pgid: int
    command: tuple[str, ...]
    log_path: Path


def _drain(stream: object, log_handle: object) -> None:
    for line in iter(stream.readline, ""):  # type: ignore[attr-defined]
        log_handle.write(line)  # type: ignore[attr-defined]
        log_handle.flush()  # type: ignore[attr-defined]


def coerce_command(
    command: ManagedCommand | list[str],
    *,
    cwd: Path | None = None,
    log_path: Path | None = None,
) -> ManagedCommand:
    if isinstance(command, ManagedCommand):
        return command
    if cwd is None or log_path is None:
        raise RuntimeError("cwd and log_path are required for raw argv commands")
    return ManagedCommand(argv=tuple(command), cwd=cwd, log_path=log_path)


def launch_managed(
    command: ManagedCommand | list[str],
    *,
    cwd: Path | None = None,
    log_path: Path | None = None,
    pid_path: Path,
) -> ManagedProcess:
    managed_command = coerce_command(command, cwd=cwd, log_path=log_path)
    managed_command.log_path.parent.mkdir(parents=True, exist_ok=True)
    stdin_handle = (
        managed_command.stdin_path.open("r", encoding="utf-8")
        if managed_command.stdin_path is not None
        else None
    )
    log_handle = managed_command.log_path.open("a", encoding="utf-8")
    env = os.environ.copy()
    if managed_command.env:
        env.update(managed_command.env)
    process = subprocess.Popen(
        list(managed_command.argv),
        cwd=managed_command.cwd,
        stdin=stdin_handle,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        bufsize=1,
        env=env,
    )
    if stdin_handle is not None:
        stdin_handle.close()
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("Process pipes were not created")
    threading.Thread(
        target=_drain, args=(process.stdout, log_handle), daemon=True
    ).start()
    threading.Thread(
        target=_drain, args=(process.stderr, log_handle), daemon=True
    ).start()
    getpgid = getattr(os, "getpgid", None)
    pgid = int(getpgid(process.pid)) if getpgid is not None else process.pid
    managed = ManagedProcess(
        pid=process.pid,
        pgid=pgid,
        command=managed_command.argv,
        log_path=managed_command.log_path,
    )
    atomic_write_json(
        pid_path,
        {
            "pid": managed.pid,
            "pgid": managed.pgid,
            "command": list(managed.command),
            "cwd": str(managed_command.cwd),
            "stdin_path": str(managed_command.stdin_path)
            if managed_command.stdin_path is not None
            else None,
            "log_path": str(managed.log_path),
            "status": "running",
            "started_at": time.time(),
            "updated_at": time.time(),
        },
    )
    return managed


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            text=True,
            capture_output=True,
            check=False,
        )
        return result.returncode == 0 and f'"{pid}"' in result.stdout
    try:
        waited, _status = os.waitpid(pid, getattr(os, "WNOHANG", 1))
        if waited == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_group(pgid: int, *, timeout: float = 5.0) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(pgid), "/T", "/F"],
            text=True,
            capture_output=True,
            check=False,
        )
        return result.returncode == 0 or not process_alive(pgid)
    kill_group: Any = getattr(os, "killpg", os.kill)
    try:
        kill_group(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # ``killpg(pgid, 0)`` still succeeds for an exited child that remains a
        # zombie. Reap the process-group leader when it is our child before
        # probing the group, so an otherwise empty group is observed as gone.
        try:
            os.waitpid(pgid, getattr(os, "WNOHANG", 1))
        except ChildProcessError:
            pass
        try:
            kill_group(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        time.sleep(0.1)
    try:
        kill_group(pgid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except ProcessLookupError:
        return True
    kill_deadline = time.monotonic() + timeout
    while time.monotonic() < kill_deadline:
        try:
            os.waitpid(pgid, getattr(os, "WNOHANG", 1))
        except ChildProcessError:
            pass
        try:
            kill_group(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        time.sleep(0.1)
    return False
