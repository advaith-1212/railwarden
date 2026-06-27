from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from lfg.config.init import initialize_project
from lfg.processes.supervisor import (
    launch_managed,
    process_alive,
    terminate_process_group,
)
from lfg.tmux.session import normalized_project_name
from lfg.util.atomic import atomic_write_json


def test_runtime_atomic_writes(tmp_path: Path) -> None:
    path = tmp_path / "state" / "x.json"
    atomic_write_json(path, {"b": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"b": 1}


def test_process_ownership_stop(tmp_path: Path) -> None:
    proc = launch_managed(
        ["python3", "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        log_path=tmp_path / "p.log",
        pid_path=tmp_path / "p.json",
    )
    assert process_alive(proc.pid)
    assert terminate_process_group(proc.pgid)
    time.sleep(0.2)
    assert not process_alive(proc.pid)


def test_unrelated_process_untouched(tmp_path: Path) -> None:
    other = subprocess.Popen(["python3", "-c", "import time; time.sleep(3)"])
    try:
        proc = launch_managed(
            ["python3", "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            log_path=tmp_path / "p.log",
            pid_path=tmp_path / "p.json",
        )
        terminate_process_group(proc.pgid)
        assert other.poll() is None
    finally:
        other.terminate()


def test_session_naming() -> None:
    assert normalized_project_name("My Project!") == "my-project"


def test_configuration_loading_init(git_repo: Path) -> None:
    result = initialize_project(git_repo, yes=True)
    assert result["repository"] == str(git_repo)
    assert (git_repo / ".lfg" / "project.yaml").exists()
