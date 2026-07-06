from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from lfg.config.models import ProjectConfig
from lfg.errors import LfgError
from lfg.planning.pipeline import create_pending_plan, new_run_id
from lfg.runtime.events import append_event
from lfg.util.atomic import atomic_write_json


def planning_job_path(runtime_dir: Path, run_id: str) -> Path:
    return runtime_dir / "planning-jobs" / f"{run_id}.json"


def pending_plan_path(runtime_dir: Path) -> Path:
    return runtime_dir / "state" / "pending-plan.json"


def active_planning_job(runtime_dir: Path) -> dict[str, Any] | None:
    jobs_dir = runtime_dir / "planning-jobs"
    if not jobs_dir.exists():
        return None
    active: list[dict[str, Any]] = []
    for path in sorted(jobs_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("status") == "planning":
            active.append(payload)
    return active[-1] if active else None


def _write_job(runtime_dir: Path, run_id: str, payload: dict[str, Any]) -> None:
    path = planning_job_path(runtime_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)


def start_planning_job(
    config: ProjectConfig,
    goal: str,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    runtime_dir = config.runtime_directory
    pending = pending_plan_path(runtime_dir)
    if pending.exists() and not replace:
        payload = json.loads(pending.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and not payload.get("approved"):
            raise LfgError(
                "A pending plan already exists. Approve, reject, or pass replace=true."
            )
    active = active_planning_job(runtime_dir)
    if active is not None:
        return {
            "run_id": str(active["run_id"]),
            "status": "planning",
            "goal": active.get("goal"),
        }

    run_id = new_run_id()
    job = {
        "run_id": run_id,
        "goal": goal.strip(),
        "status": "planning",
        "error": None,
    }
    _write_job(runtime_dir, run_id, job)
    append_event(runtime_dir, "planning_started", {"run_id": run_id, "goal": goal})
    _spawn_planning_worker(config, run_id)
    return {"run_id": run_id, "status": "planning", "goal": goal.strip()}


def execute_planning_job(config: ProjectConfig, run_id: str) -> None:
    runtime_dir = config.runtime_directory
    path = planning_job_path(runtime_dir, run_id)
    if not path.exists():
        raise LfgError(f"Planning job not found: {run_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LfgError(f"Invalid planning job: {path}")
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        raise LfgError(f"Planning job {run_id} is missing a goal")
    try:
        fixture_path = os.environ.get("LFG_PLANNER_OUTPUT")
        fixture = (
            Path(fixture_path).read_text(encoding="utf-8") if fixture_path else None
        )
        pending_plan = create_pending_plan(
            config, goal, planner_output_text=fixture
        )
        finished = {
            "run_id": pending_plan.run_id,
            "goal": pending_plan.goal,
            "status": "ready",
            "error": None,
        }
        _write_job(runtime_dir, run_id, finished)
        append_event(
            runtime_dir,
            "planning_finished",
            {"run_id": pending_plan.run_id},
        )
    except Exception as exc:
        failed = {
            "run_id": run_id,
            "goal": goal,
            "status": "failed",
            "error": str(exc),
        }
        _write_job(runtime_dir, run_id, failed)
        append_event(
            runtime_dir,
            "planning_failed",
            {"run_id": run_id, "error": str(exc)},
        )
        raise


def _spawn_planning_worker(config: ProjectConfig, run_id: str) -> None:
    log_dir = config.runtime_directory / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"planning-{run_id}.log"
    with log_path.open("ab") as log_handle:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "lfg.cli.main",
                "planning-worker",
                "--run-id",
                run_id,
            ],
            cwd=str(config.repository_root),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def planning_status(
    config: ProjectConfig, *, run_id: str | None = None
) -> dict[str, Any]:
    runtime_dir = config.runtime_directory
    if run_id:
        path = planning_job_path(runtime_dir, run_id)
        if not path.exists():
            return {"status": "missing", "run_id": run_id}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
        return {"status": "invalid", "run_id": run_id}

    active = active_planning_job(runtime_dir)
    if active is not None:
        return active

    pending = pending_plan_path(runtime_dir)
    if pending.exists():
        payload = json.loads(pending.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            if payload.get("approved"):
                return {"status": "approved", "run_id": payload.get("run_id")}
            if payload.get("rejected"):
                return {"status": "rejected", "run_id": payload.get("run_id")}
            return {
                "status": "ready",
                "run_id": payload.get("run_id"),
                "goal": payload.get("goal"),
            }
    return {"status": "idle"}