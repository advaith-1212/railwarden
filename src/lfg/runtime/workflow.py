from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from lfg.runtime.events import append_event
from lfg.util.atomic import atomic_write_json

WorkflowNode = Literal[
    "GOAL_RECEIVED",
    "REPOSITORY_ANALYZED",
    "PLAN_CREATED",
    "CONTRACTS_FROZEN",
    "WORK_PACKAGES_CREATED",
    "DAG_VALIDATED",
    "AGENTS_ASSIGNED",
    "WORK_EXECUTING",
    "PACKAGE_VALIDATION",
    "PACKAGE_REVIEW",
    "DEPENDENCY_SAFE_MERGE",
    "INTEGRATION_VALIDATION",
    "RELEASE_REVIEW",
    "GOAL_COMPLETE",
    "RECOVERY_OR_SWAP",
]

WORKFLOW_ORDER: tuple[WorkflowNode, ...] = (
    "GOAL_RECEIVED",
    "REPOSITORY_ANALYZED",
    "PLAN_CREATED",
    "CONTRACTS_FROZEN",
    "WORK_PACKAGES_CREATED",
    "DAG_VALIDATED",
    "AGENTS_ASSIGNED",
    "WORK_EXECUTING",
    "PACKAGE_VALIDATION",
    "PACKAGE_REVIEW",
    "DEPENDENCY_SAFE_MERGE",
    "INTEGRATION_VALIDATION",
    "RELEASE_REVIEW",
    "GOAL_COMPLETE",
    "RECOVERY_OR_SWAP",
)


@dataclass(frozen=True)
class WorkflowCheckpoint:
    thread_id: str
    node: WorkflowNode
    payload: dict[str, Any]
    updated_at: float


def workflow_path(runtime_dir: Path, thread_id: str = "default") -> Path:
    return runtime_dir / "langgraph" / f"{thread_id}.json"


def load_workflow(runtime_dir: Path, thread_id: str = "default") -> WorkflowCheckpoint:
    path = workflow_path(runtime_dir, thread_id)
    if not path.exists():
        return WorkflowCheckpoint(
            thread_id=thread_id,
            node="GOAL_RECEIVED",
            payload={},
            updated_at=time.time(),
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid workflow checkpoint: {path}")
    return WorkflowCheckpoint(
        thread_id=str(payload["thread_id"]),
        node=str(payload["node"]),  # type: ignore[arg-type]
        payload=_mapping(payload.get("payload", {})),
        updated_at=float(payload.get("updated_at", time.time())),
    )


def save_workflow(runtime_dir: Path, checkpoint: WorkflowCheckpoint) -> None:
    atomic_write_json(
        workflow_path(runtime_dir, checkpoint.thread_id),
        {
            "thread_id": checkpoint.thread_id,
            "node": checkpoint.node,
            "payload": checkpoint.payload,
            "updated_at": checkpoint.updated_at,
            "langgraph_scope": "thread",
        },
    )
    append_event(
        runtime_dir,
        "workflow_checkpoint",
        {"thread_id": checkpoint.thread_id, "node": checkpoint.node},
    )


def advance_workflow(
    runtime_dir: Path,
    node: WorkflowNode,
    *,
    thread_id: str = "default",
    payload: dict[str, Any] | None = None,
) -> WorkflowCheckpoint:
    checkpoint = WorkflowCheckpoint(
        thread_id=thread_id,
        node=node,
        payload=payload or {},
        updated_at=time.time(),
    )
    save_workflow(runtime_dir, checkpoint)
    return checkpoint


def langgraph_available() -> bool:
    try:
        import langgraph.graph  # noqa: F401
    except Exception:
        return False
    return True


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return value
