from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from lfg.util.atomic import atomic_write_json


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


def launch_managed(
    command: list[str], *, cwd: Path, log_path: Path, pid_path: Path
) -> ManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        bufsize=1,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("Process pipes were not created")
    threading.Thread(
        target=_drain, args=(process.stdout, log_handle), daemon=True
    ).start()
    threading.Thread(
        target=_drain, args=(process.stderr, log_handle), daemon=True
    ).start()
    pgid = os.getpgid(process.pid)
    managed = ManagedProcess(
        pid=process.pid, pgid=pgid, command=tuple(command), log_path=log_path
    )
    atomic_write_json(
        pid_path,
        {
            "pid": managed.pid,
            "pgid": managed.pgid,
            "command": list(command),
            "log_path": str(log_path),
        },
    )
    return managed


def process_alive(pid: int) -> bool:
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
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
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    return False
