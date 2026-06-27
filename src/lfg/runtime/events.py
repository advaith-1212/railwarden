from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def events_path(runtime_dir: Path) -> Path:
    return runtime_dir / "events.jsonl"


def append_event(
    runtime_dir: Path,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    event = {
        "ts": time.time(),
        "type": event_type,
        "task_id": task_id,
        "payload": payload or {},
    }
    path = events_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return event


def read_events(runtime_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = events_path(runtime_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows[-limit:] if limit is not None else rows

