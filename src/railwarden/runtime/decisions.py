from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from railwarden.runtime.events import append_event


def decisions_path(runtime_dir: Path) -> Path:
    return runtime_dir / "decisions.jsonl"


def failures_dir(runtime_dir: Path) -> Path:
    return runtime_dir / "failures"


def failure_path(runtime_dir: Path, task_id: str) -> Path:
    return failures_dir(runtime_dir) / f"{task_id}.json"


def record_decision(
    runtime_dir: Path,
    *,
    observed_event: dict[str, Any],
    diagnosis: str,
    allowed_actions: list[str],
    chosen_action: str,
    rationale: str,
    tool_call: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = {
        "ts": time.time(),
        "observed_event": observed_event,
        "diagnosis": diagnosis,
        "allowed_actions": allowed_actions,
        "chosen_action": chosen_action,
        "rationale": rationale,
        "tool_call": tool_call or {},
        "result": result or {},
    }
    path = decisions_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(decision, sort_keys=True) + "\n")
    return decision


def emit_decision_required(
    runtime_dir: Path,
    *,
    task_id: str,
    failure_kind: str,
    facts: dict[str, Any],
    allowed_actions: list[str],
) -> dict[str, Any]:
    payload = {
        "type": "decision_required",
        "task_id": task_id,
        "failure_kind": failure_kind,
        "facts": facts,
        "allowed_actions": allowed_actions,
    }
    path = failure_path(runtime_dir, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return append_event(
        runtime_dir,
        "decision_required",
        payload,
        task_id=task_id,
    )


def inspect_failure(runtime_dir: Path, task_id: str) -> dict[str, Any]:
    path = failure_path(runtime_dir, task_id)
    if not path.exists():
        return {"status": "missing", "task_id": task_id}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"status": "invalid", "task_id": task_id, "path": str(path)}
    return {"status": "ok", "path": str(path), **payload}
