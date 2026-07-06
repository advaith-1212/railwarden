from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from lfg.config.loader import load_project_files
from lfg.engine.controller import controller_tick, integration_candidates
from lfg.errors import LfgError
from lfg.git import changed_files_in_commit, head
from lfg.integration.manager import integrate_one
from lfg.planning.jobs import planning_status, start_planning_job
from lfg.planning.pipeline import approve_latest_plan
from lfg.runtime.checkpoints import create_checkpoint_commit
from lfg.runtime.context import context_status, write_context_file
from lfg.runtime.decisions import inspect_failure, record_decision
from lfg.runtime.events import read_events
from lfg.runtime.quota import load_quota
from lfg.runtime.results import normalize_result
from lfg.runtime.session import (
    AgentInstance,
    load_session_profile,
    model_profile_from_ref,
    save_session_profile,
    update_agent,
)
from lfg.runtime.skills import create_runtime_skill, promote_runtime_skill
from lfg.runtime.tasks import load_tasks, transition_task
from lfg.scheduler.classifier import package_branch, package_worktree
from lfg.tmux.session import send_worker_message
from lfg.util.atomic import atomic_write_text
from lfg.validation.contract import validate_packages_for_freeze
from lfg.validation.package import run_package_validation
from lfg.validation.review import run_package_review


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]


NonEmptyString = Annotated[str, Field(min_length=1)]
TaskRouteStatus = Literal["ready", "handoff_needed", "paused"]


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in tools(Path.cwd()).values()
    ]


def tools(start: Path) -> dict[str, Tool]:
    def no_args(
        properties: dict[str, Any] | None = None, required: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties or {},
            "required": required or [],
        }

    return {
        "lfg.goal.submit": Tool(
            "lfg.goal.submit",
            "Create a pending LFG plan from a goal.",
            no_args(
                {
                    "goal": {"type": "string", "minLength": 1},
                    "replace": {"type": "boolean"},
                },
                ["goal"],
            ),
            lambda payload: _goal_submit(start, payload),
        ),
        "lfg.plan.create": Tool(
            "lfg.plan.create",
            "Create a pending LFG plan from a goal.",
            no_args({"goal": {"type": "string", "minLength": 1}}, ["goal"]),
            lambda payload: _goal_submit(start, payload),
        ),
        "lfg.plan.approve": Tool(
            "lfg.plan.approve",
            "Approve the latest pending LFG plan.",
            no_args(),
            lambda _payload: _plan_approve(start),
        ),
        "lfg.plan.show": Tool(
            "lfg.plan.show",
            "Show the latest pending LFG plan and work packages.",
            no_args(),
            lambda _payload: _plan_show(start),
        ),
        "lfg.plan.status": Tool(
            "lfg.plan.status",
            "Return async planning job status.",
            no_args({"run_id": {"type": "string"}}, []),
            lambda payload: _plan_status(start, payload),
        ),
        "lfg.plan.reject": Tool(
            "lfg.plan.reject",
            "Reject the latest pending LFG plan.",
            no_args({"reason": {"type": "string"}}, []),
            lambda payload: _plan_reject(start, payload),
        ),
        "lfg.contracts.freeze": Tool(
            "lfg.contracts.freeze",
            "Freeze approved contracts and begin execution.",
            no_args(),
            lambda _payload: _plan_approve(start),
        ),
        "lfg.task.list": Tool(
            "lfg.task.list",
            "List durable LFG tasks.",
            no_args(),
            lambda _payload: _task_list(start),
        ),
        "lfg.state.snapshot": Tool(
            "lfg.state.snapshot",
            "Return an LFG source-of-truth snapshot.",
            no_args(),
            lambda _payload: _state_snapshot(start),
        ),
        "lfg.events.tail": Tool(
            "lfg.events.tail",
            "Tail LFG events after an integer cursor.",
            no_args({"cursor": {"type": "integer", "minimum": 0}}, []),
            lambda payload: _events_tail(start, payload),
        ),
        "lfg.task.route": Tool(
            "lfg.task.route",
            "Route a task to ready or handoff-needed state.",
            no_args(
                {
                    "task_id": {"type": "string", "minLength": 1},
                    "status": {"enum": ["ready", "handoff_needed", "paused"]},
                },
                ["task_id", "status"],
            ),
            lambda payload: _task_route(start, payload),
        ),
        "lfg.task.inspect": Tool(
            "lfg.task.inspect",
            "Inspect one task or work package.",
            no_args({"task_id": {"type": "string", "minLength": 1}}, ["task_id"]),
            lambda payload: _task_inspect(start, payload),
        ),
        "lfg.task.retry": Tool(
            "lfg.task.retry",
            "Mark a task or work package ready for retry.",
            no_args({"task_id": {"type": "string", "minLength": 1}}, ["task_id"]),
            lambda payload: _task_set_status(start, payload, "ready"),
        ),
        "lfg.task.assign": Tool(
            "lfg.task.assign",
            "Assign a task to a provider and make it ready.",
            no_args(
                {
                    "task_id": {"type": "string", "minLength": 1},
                    "provider": {"type": "string", "minLength": 1},
                },
                ["task_id", "provider"],
            ),
            lambda payload: _task_assign(start, payload),
        ),
        "lfg.task.handoff": Tool(
            "lfg.task.handoff",
            "Hand off a task to a provider.",
            no_args(
                {
                    "task_id": {"type": "string", "minLength": 1},
                    "provider": {"type": "string", "minLength": 1},
                },
                ["task_id", "provider"],
            ),
            lambda payload: _task_handoff(start, payload),
        ),
        "lfg.task.reject": Tool(
            "lfg.task.reject",
            "Reject a task or work package.",
            no_args({"task_id": {"type": "string", "minLength": 1}}, ["task_id"]),
            lambda payload: _task_set_status(start, payload, "rejected"),
        ),
        "lfg.agent.swap": Tool(
            "lfg.agent.swap",
            "Swap an agent to a new model ref.",
            no_args(
                {
                    "agent_id": {"type": "string", "minLength": 1},
                    "to": {"type": "string", "minLength": 1},
                },
                ["agent_id", "to"],
            ),
            lambda payload: _agent_swap(start, payload),
        ),
        "lfg.agent.pause": Tool(
            "lfg.agent.pause",
            "Pause an agent.",
            no_args({"agent_id": {"type": "string"}}, ["agent_id"]),
            lambda payload: _agent_state(start, payload, "paused"),
        ),
        "lfg.agent.resume": Tool(
            "lfg.agent.resume",
            "Resume an agent.",
            no_args({"agent_id": {"type": "string"}}, ["agent_id"]),
            lambda payload: _agent_state(start, payload, "ready"),
        ),
        "lfg.quota.status": Tool(
            "lfg.quota.status",
            "Return quota state for all agents.",
            no_args(),
            lambda _payload: _quota_status(start),
        ),
        "lfg.checkpoint.create": Tool(
            "lfg.checkpoint.create",
            "Create a checkpoint commit for a task branch.",
            no_args({"task_id": {"type": "string"}}, ["task_id"]),
            lambda payload: _checkpoint_create(start, payload),
        ),
        "lfg.integration.status": Tool(
            "lfg.integration.status",
            "Return current integration candidates.",
            no_args(),
            lambda _payload: _integration_status(start),
        ),
        "lfg.integration.reconcile": Tool(
            "lfg.integration.reconcile",
            "Merge and validate the next integration candidate.",
            no_args(),
            lambda _payload: _integration_reconcile(start),
        ),
        "lfg.worker.message": Tool(
            "lfg.worker.message",
            "Send guidance to a live worker tmux pane.",
            no_args(
                {
                    "message": {"type": "string", "minLength": 1},
                    "agent_id": {"type": "string"},
                    "task_id": {"type": "string"},
                },
                ["message"],
            ),
            lambda payload: _worker_message(start, payload),
        ),
        "lfg.validation.run": Tool(
            "lfg.validation.run",
            "Run LFG-owned package validation.",
            no_args({"task_id": {"type": "string", "minLength": 1}}, ["task_id"]),
            lambda payload: _validation_run(start, payload),
        ),
        "lfg.review.run": Tool(
            "lfg.review.run",
            "Run independent package review.",
            no_args({"task_id": {"type": "string", "minLength": 1}}, ["task_id"]),
            lambda payload: _review_run(start, payload),
        ),
        "lfg.failure.inspect": Tool(
            "lfg.failure.inspect",
            "Inspect the latest structured failure for a task.",
            no_args({"task_id": {"type": "string", "minLength": 1}}, ["task_id"]),
            lambda payload: _failure_inspect(start, payload),
        ),
        "lfg.result.normalize": Tool(
            "lfg.result.normalize",
            "Normalize or synthesize an LFG-owned task result.",
            no_args({"task_id": {"type": "string", "minLength": 1}}, ["task_id"]),
            lambda payload: _result_normalize(start, payload),
        ),
        "lfg.context.status": Tool(
            "lfg.context.status",
            "Report context memory files and missing work-package refs.",
            no_args(),
            lambda _payload: _context_status(start),
        ),
        "lfg.context.write": Tool(
            "lfg.context.write",
            "Write one LFG context memory file.",
            no_args(
                {
                    "file": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                },
                ["file", "content"],
            ),
            lambda payload: _context_write(start, payload),
        ),
        "lfg.contract.repair": Tool(
            "lfg.contract.repair",
            "Record a contract repair patch for operator review.",
            no_args(
                {
                    "package_id": {"type": "string", "minLength": 1},
                    "patch": {"type": "object"},
                },
                ["package_id", "patch"],
            ),
            lambda payload: _contract_repair(start, payload),
        ),
        "lfg.contract.repair.apply": Tool(
            "lfg.contract.repair.apply",
            "Apply a recorded contract repair patch to work packages.",
            no_args({"package_id": {"type": "string", "minLength": 1}}, ["package_id"]),
            lambda payload: _contract_repair_apply(start, payload),
        ),
        "lfg.decision.record": Tool(
            "lfg.decision.record",
            "Append a Hermes supervisor decision record.",
            no_args(
                {
                    "observed_event": {"type": "object"},
                    "diagnosis": {"type": "string"},
                    "allowed_actions": {"type": "array", "items": {"type": "string"}},
                    "chosen_action": {"type": "string"},
                    "rationale": {"type": "string"},
                    "tool_call": {"type": "object"},
                    "result": {"type": "object"},
                },
                ["diagnosis", "allowed_actions", "chosen_action", "rationale"],
            ),
            lambda payload: _decision_record(start, payload),
        ),
        "lfg.merge.approve": Tool(
            "lfg.merge.approve",
            "Approve a package for merge.",
            no_args({"task_id": {"type": "string", "minLength": 1}}, ["task_id"]),
            lambda payload: _task_set_status(start, payload, "merge_approved"),
        ),
        "lfg.goal.abort": Tool(
            "lfg.goal.abort",
            "Abort active non-terminal tasks for the current goal.",
            no_args({"reason": {"type": "string"}}, []),
            lambda payload: _goal_abort(start, payload),
        ),
        "lfg.skill.create": Tool(
            "lfg.skill.create",
            "Create a runtime-only LFG skill for the current session.",
            no_args(
                {
                    "name": {"type": "string", "minLength": 1},
                    "text": {"type": "string", "minLength": 1},
                },
                ["name", "text"],
            ),
            lambda payload: _skill_create(start, payload),
        ),
        "lfg.skill.promote": Tool(
            "lfg.skill.promote",
            "Promote a runtime LFG skill into committed project skills.",
            no_args({"name": {"type": "string", "minLength": 1}}, ["name"]),
            lambda payload: _skill_promote(start, payload),
        ),
    }


def fastmcp_server(start: Path | None = None) -> FastMCP:
    root = start or Path.cwd()
    server = FastMCP(
        name="lfg",
        instructions=(
            "LFG is authoritative for durable goals, task routing, quota state, "
            "checkpoints, handoffs, skills, and integration status."
        ),
    )

    @server.tool(
        name="lfg.goal.submit",
        description="Create a pending LFG plan from a goal.",
    )
    def goal_submit(goal: NonEmptyString) -> dict[str, Any]:
        return _goal_submit(root, {"goal": goal})

    @server.tool(
        name="lfg.plan.create",
        description="Create a pending LFG plan from a goal.",
    )
    def plan_create(goal: NonEmptyString) -> dict[str, Any]:
        return _goal_submit(root, {"goal": goal})

    @server.tool(
        name="lfg.plan.approve",
        description="Approve the latest pending LFG plan.",
    )
    def plan_approve() -> dict[str, Any]:
        return _plan_approve(root)

    @server.tool(
        name="lfg.plan.show",
        description="Show the latest pending LFG plan and work packages.",
    )
    def plan_show() -> dict[str, Any]:
        return _plan_show(root)

    @server.tool(
        name="lfg.plan.status",
        description="Return async planning job status.",
    )
    def plan_status(run_id: str = "") -> dict[str, Any]:
        return _plan_status(root, {"run_id": run_id} if run_id else {})

    @server.tool(
        name="lfg.plan.reject",
        description="Reject the latest pending LFG plan.",
    )
    def plan_reject(reason: str = "") -> dict[str, Any]:
        return _plan_reject(root, {"reason": reason})

    @server.tool(
        name="lfg.contracts.freeze",
        description="Freeze approved contracts and begin execution.",
    )
    def contracts_freeze() -> dict[str, Any]:
        return _plan_approve(root)

    @server.tool(
        name="lfg.task.list",
        description="List durable LFG tasks.",
    )
    def task_list() -> dict[str, Any]:
        return _task_list(root)

    @server.tool(
        name="lfg.state.snapshot",
        description="Return an LFG source-of-truth snapshot.",
    )
    def state_snapshot() -> dict[str, Any]:
        return _state_snapshot(root)

    @server.tool(
        name="lfg.events.tail",
        description="Tail LFG events after an integer cursor.",
    )
    def events_tail(cursor: int = 0) -> dict[str, Any]:
        return _events_tail(root, {"cursor": cursor})

    @server.tool(
        name="lfg.task.route",
        description="Route a task to ready or handoff-needed state.",
    )
    def task_route(task_id: NonEmptyString, status: TaskRouteStatus) -> dict[str, Any]:
        return _task_route(root, {"task_id": task_id, "status": status})

    @server.tool(
        name="lfg.task.inspect",
        description="Inspect one task or work package.",
    )
    def task_inspect(task_id: NonEmptyString) -> dict[str, Any]:
        return _task_inspect(root, {"task_id": task_id})

    @server.tool(
        name="lfg.task.retry",
        description="Mark a task or work package ready for retry.",
    )
    def task_retry(task_id: NonEmptyString) -> dict[str, Any]:
        return _task_set_status(root, {"task_id": task_id}, "ready")

    @server.tool(
        name="lfg.task.assign",
        description="Assign a task to a provider and make it ready.",
    )
    def task_assign(task_id: NonEmptyString, provider: NonEmptyString) -> dict[str, Any]:
        return _task_assign(root, {"task_id": task_id, "provider": provider})

    @server.tool(
        name="lfg.task.handoff",
        description="Hand off a task to a provider.",
    )
    def task_handoff(task_id: NonEmptyString, provider: NonEmptyString) -> dict[str, Any]:
        return _task_handoff(root, {"task_id": task_id, "provider": provider})

    @server.tool(
        name="lfg.task.reject",
        description="Reject a task or work package.",
    )
    def task_reject(task_id: NonEmptyString) -> dict[str, Any]:
        return _task_set_status(root, {"task_id": task_id}, "rejected")

    @server.tool(
        name="lfg.agent.swap",
        description="Swap an agent to a new model ref.",
    )
    def agent_swap(agent_id: NonEmptyString, to: NonEmptyString) -> dict[str, Any]:
        return _agent_swap(root, {"agent_id": agent_id, "to": to})

    @server.tool(
        name="lfg.agent.pause",
        description="Pause an agent.",
    )
    def agent_pause(agent_id: NonEmptyString) -> dict[str, Any]:
        return _agent_state(root, {"agent_id": agent_id}, "paused")

    @server.tool(
        name="lfg.agent.resume",
        description="Resume an agent.",
    )
    def agent_resume(agent_id: NonEmptyString) -> dict[str, Any]:
        return _agent_state(root, {"agent_id": agent_id}, "ready")

    @server.tool(
        name="lfg.quota.status",
        description="Return quota state for all agents.",
    )
    def quota_status() -> dict[str, Any]:
        return _quota_status(root)

    @server.tool(
        name="lfg.checkpoint.create",
        description="Create a checkpoint commit for a task branch.",
    )
    def checkpoint_create(task_id: NonEmptyString) -> dict[str, Any]:
        return _checkpoint_create(root, {"task_id": task_id})

    @server.tool(
        name="lfg.integration.status",
        description="Return current integration candidates.",
    )
    def integration_status() -> dict[str, Any]:
        return _integration_status(root)

    @server.tool(
        name="lfg.validation.run",
        description="Run LFG-owned package validation.",
    )
    def validation_run(task_id: NonEmptyString) -> dict[str, Any]:
        return _validation_run(root, {"task_id": task_id})

    @server.tool(
        name="lfg.review.run",
        description="Run independent package review.",
    )
    def review_run(task_id: NonEmptyString) -> dict[str, Any]:
        return _review_run(root, {"task_id": task_id})

    @server.tool(
        name="lfg.failure.inspect",
        description="Inspect the latest structured failure for a task.",
    )
    def failure_inspect(task_id: NonEmptyString) -> dict[str, Any]:
        return _failure_inspect(root, {"task_id": task_id})

    @server.tool(
        name="lfg.result.normalize",
        description="Normalize or synthesize an LFG-owned task result.",
    )
    def result_normalize(task_id: NonEmptyString) -> dict[str, Any]:
        return _result_normalize(root, {"task_id": task_id})

    @server.tool(
        name="lfg.context.status",
        description="Report context memory files and missing work-package refs.",
    )
    def lfg_context_status() -> dict[str, Any]:
        return _context_status(root)

    @server.tool(
        name="lfg.context.write",
        description="Write one LFG context memory file.",
    )
    def context_write(file: NonEmptyString, content: str) -> dict[str, Any]:
        return _context_write(root, {"file": file, "content": content})

    @server.tool(
        name="lfg.contract.repair",
        description="Record a contract repair patch for operator review.",
    )
    def contract_repair(package_id: NonEmptyString, patch: dict[str, Any]) -> dict[str, Any]:
        return _contract_repair(root, {"package_id": package_id, "patch": patch})

    @server.tool(
        name="lfg.contract.repair.apply",
        description="Apply a recorded contract repair patch to work packages.",
    )
    def contract_repair_apply(package_id: NonEmptyString) -> dict[str, Any]:
        return _contract_repair_apply(root, {"package_id": package_id})

    @server.tool(
        name="lfg.integration.reconcile",
        description="Merge and validate the next integration candidate.",
    )
    def integration_reconcile() -> dict[str, Any]:
        return _integration_reconcile(root)

    @server.tool(
        name="lfg.worker.message",
        description="Send guidance to a live worker tmux pane.",
    )
    def worker_message(
        message: NonEmptyString,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        return _worker_message(
            root,
            {"message": message, "agent_id": agent_id, "task_id": task_id},
        )

    @server.tool(
        name="lfg.decision.record",
        description="Append a Hermes supervisor decision record.",
    )
    def decision_record(
        diagnosis: str,
        allowed_actions: list[str],
        chosen_action: str,
        rationale: str,
        observed_event: dict[str, Any] | None = None,
        tool_call: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _decision_record(
            root,
            {
                "observed_event": observed_event or {},
                "diagnosis": diagnosis,
                "allowed_actions": allowed_actions,
                "chosen_action": chosen_action,
                "rationale": rationale,
                "tool_call": tool_call or {},
                "result": result or {},
            },
        )

    @server.tool(
        name="lfg.merge.approve",
        description="Approve a package for merge.",
    )
    def merge_approve(task_id: NonEmptyString) -> dict[str, Any]:
        return _task_set_status(root, {"task_id": task_id}, "merge_approved")

    @server.tool(
        name="lfg.goal.abort",
        description="Abort active non-terminal tasks for the current goal.",
    )
    def goal_abort(reason: str = "") -> dict[str, Any]:
        return _goal_abort(root, {"reason": reason})

    @server.tool(
        name="lfg.skill.create",
        description="Create a runtime-only LFG skill for the current session.",
    )
    def skill_create(name: NonEmptyString, text: NonEmptyString) -> dict[str, Any]:
        return _skill_create(root, {"name": name, "text": text})

    @server.tool(
        name="lfg.skill.promote",
        description="Promote a runtime LFG skill into committed project skills.",
    )
    def skill_promote(name: NonEmptyString) -> dict[str, Any]:
        return _skill_promote(root, {"name": name})

    return server


def serve(start: Path | None = None) -> int:
    fastmcp_server(start).run("stdio")
    return 0


def _goal_submit(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    replace = bool(payload.get("replace", False))
    fixture = os.environ.get("LFG_PLANNER_OUTPUT")
    if fixture:
        from lfg.planning.pipeline import create_pending_plan

        pending = create_pending_plan(
            files.project, str(payload["goal"]), planner_output_text=fixture
        )
        return {"run_id": pending.run_id, "status": "ready"}
    return start_planning_job(
        files.project, str(payload["goal"]), replace=replace
    )


def _plan_status(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    run_id = payload.get("run_id")
    return planning_status(
        files.project,
        run_id=str(run_id) if run_id else None,
    )


def _plan_approve(start: Path) -> dict[str, Any]:
    files = load_project_files(start)
    return approve_latest_plan(files.project)


def _plan_show(start: Path) -> dict[str, Any]:
    files = load_project_files(start)
    path = files.project.runtime_directory / "state" / "pending-plan.json"
    if not path.exists():
        return {"status": "missing"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LfgError("Pending plan state is invalid")
    plan_path = Path(str(payload.get("plan_path", "")))
    return {
        "status": "pending",
        "run_id": payload.get("run_id"),
        "goal": payload.get("goal"),
        "approved": payload.get("approved", False),
        "rejected": payload.get("rejected", False),
        "plan_markdown": plan_path.read_text(encoding="utf-8")
        if plan_path.exists()
        else "",
        "work_packages": payload.get("work_packages", []),
    }


def _plan_reject(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    path = files.project.runtime_directory / "state" / "pending-plan.json"
    if not path.exists():
        raise LfgError("No pending plan exists")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise LfgError("Pending plan state is invalid")
    state["approved"] = False
    state["rejected"] = True
    state["rejection_reason"] = str(payload.get("reason", ""))
    state["rejected_at"] = time.time()
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"status": "rejected", "run_id": state.get("run_id")}


def _task_list(start: Path) -> dict[str, Any]:
    files = load_project_files(start)
    return {"tasks": load_tasks(files.project.runtime_directory)}


def _state_snapshot(start: Path) -> dict[str, Any]:
    files = load_project_files(start)
    return {
        "project": files.project.__dict__,
        "tasks": load_tasks(files.project.runtime_directory),
        "events": read_events(files.project.runtime_directory, limit=50),
        "context": context_status(files.project, files.packages),
    }


def _events_tail(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    cursor = int(payload.get("cursor") or 0)
    events = read_events(files.project.runtime_directory)
    return {"cursor": len(events), "events": events[cursor:]}


def _task_route(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    for task in load_tasks(files.project.runtime_directory):
        if str(task.get("id")) == str(payload["task_id"]):
            updated = transition_task(
                files.project.runtime_directory, task, str(payload["status"])
            )
            return {"task": updated}
    raise LfgError(f"Unknown task: {payload['task_id']}")


def _task_assign(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    task, _package = _task_match(files, str(payload["task_id"]))
    updated = transition_task(
        files.project.runtime_directory,
        task,
        "ready",
        {"provider_override": str(payload["provider"])},
    )
    return {"task": updated}


def _task_handoff(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    task, _package = _task_match(files, str(payload["task_id"]))
    updated = transition_task(
        files.project.runtime_directory,
        task,
        "handoff_needed",
        {"provider_override": str(payload["provider"])},
    )
    return {"task": updated}


def _task_match(files: Any, task_id: str) -> tuple[dict[str, Any], Any]:
    for task in load_tasks(files.project.runtime_directory):
        if str(task.get("id")) == task_id or str(task.get("package_id")) == task_id:
            package = files.packages.get(str(task.get("package_id", "")))
            if package is None:
                raise LfgError(f"Package is not loaded for task: {task_id}")
            return task, package
    raise LfgError(f"Unknown task: {task_id}")


def _task_inspect(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    task, package = _task_match(files, str(payload["task_id"]))
    return {"task": task, "package": package.__dict__}


def _task_set_status(
    start: Path, payload: dict[str, Any], status: str
) -> dict[str, Any]:
    files = load_project_files(start)
    task, _package = _task_match(files, str(payload["task_id"]))
    updated = transition_task(files.project.runtime_directory, task, status)
    return {"task": updated}


def _validation_run(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    task, package = _task_match(files, str(payload["task_id"]))
    workspace = Path(
        str(task.get("worktree", package_worktree(files.project, package)))
    )
    commit_hash = str(task.get("commit_hash") or head(workspace))
    evidence = run_package_validation(files.project, package, workspace, commit_hash)
    transition_task(
        files.project.runtime_directory,
        task,
        "validated" if evidence["status"] == "passed" else "blocked",
        {"package_validation": evidence},
    )
    return evidence


def _review_run(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    task, package = _task_match(files, str(payload["task_id"]))
    workspace = Path(
        str(task.get("worktree", package_worktree(files.project, package)))
    )
    commit_hash = str(task.get("commit_hash") or head(workspace))
    validation = task.get("package_validation")
    if not isinstance(validation, dict):
        validation = run_package_validation(
            files.project, package, workspace, commit_hash
        )
    review = run_package_review(
        files.project,
        package,
        task=task,
        worker_provider=str(task.get("provider", "")),
        reviewer_provider=None,
        changed_files=changed_files_in_commit(workspace, commit_hash),
        validation_evidence=validation,
    )
    transition_task(
        files.project.runtime_directory,
        task,
        "review_passed" if review["status"] == "passed" else "blocked",
        {"review": review},
    )
    return review


def _failure_inspect(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    return inspect_failure(files.project.runtime_directory, str(payload["task_id"]))


def _result_normalize(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    task, package = _task_match(files, str(payload["task_id"]))
    return normalize_result(files.project, task=task, package=package)


def _context_status(start: Path) -> dict[str, Any]:
    files = load_project_files(start)
    return context_status(files.project, files.packages)


def _context_write(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    return write_context_file(
        files.project,
        str(payload["file"]),
        str(payload.get("content", "")),
    )


def _contract_repair(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    package_id = str(payload["package_id"])
    if package_id not in files.packages:
        raise LfgError(f"Unknown package: {package_id}")
    path = files.project.runtime_directory / "contract-repairs" / f"{package_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload.get("patch", {}), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"status": "recorded", "package_id": package_id, "path": str(path)}


def _contract_repair_apply(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    package_id = str(payload["package_id"])
    repair_path = files.project.runtime_directory / "contract-repairs" / f"{package_id}.json"
    if not repair_path.exists():
        raise LfgError(f"No recorded repair patch for {package_id}")
    patch = json.loads(repair_path.read_text(encoding="utf-8"))
    if not isinstance(patch, dict):
        raise LfgError(f"Invalid repair patch for {package_id}")
    packages_path = start / ".lfg" / "work_packages.yaml"
    packages_payload = yaml.safe_load(packages_path.read_text(encoding="utf-8"))
    if not isinstance(packages_payload, dict):
        raise LfgError("work_packages.yaml must contain a mapping")
    raw_packages = packages_payload.get("work_packages", [])
    if not isinstance(raw_packages, list):
        raise LfgError("work_packages.yaml must contain work_packages list")
    updated = False
    for item in raw_packages:
        if isinstance(item, dict) and str(item.get("id")) == package_id:
            item.update(patch)
            updated = True
            break
    if not updated:
        raise LfgError(f"Unknown package: {package_id}")
    validate_packages_for_freeze(
        start,
        [item for item in raw_packages if isinstance(item, dict)],
        require_context_refs=False,
    )
    atomic_write_text(packages_path, yaml.safe_dump(packages_payload, sort_keys=False))
    return {"status": "applied", "package_id": package_id, "patch": patch}


def _integration_reconcile(start: Path) -> dict[str, Any]:
    files = load_project_files(start)
    candidates = integration_candidates(files)
    if not candidates:
        return {"status": "idle", "integrated": None}
    integrated = integrate_one(
        config=files.project,
        candidate=candidates[0],
        validation_commands=files.validation,
        execute=True,
    )
    controller_tick(files, launch=False, integrate=False)
    return {"status": "integrated", "integrated": integrated}


def _worker_message(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    message = str(payload["message"])
    agent_id = str(payload["agent_id"]) if payload.get("agent_id") else None
    task_id = str(payload["task_id"]) if payload.get("task_id") else None
    if not agent_id and not task_id:
        raise LfgError("agent_id or task_id is required")
    return send_worker_message(
        files.project,
        agent_id=agent_id,
        task_id=task_id,
        message=message,
    )


def _decision_record(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    return record_decision(
        files.project.runtime_directory,
        observed_event=_dict(payload.get("observed_event")),
        diagnosis=str(payload["diagnosis"]),
        allowed_actions=[str(item) for item in payload["allowed_actions"]],
        chosen_action=str(payload["chosen_action"]),
        rationale=str(payload["rationale"]),
        tool_call=_dict(payload.get("tool_call")),
        result=_dict(payload.get("result")),
    )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _goal_abort(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    reason = str(payload.get("reason", "manual abort") or "manual abort")
    updated = []
    for task in load_tasks(files.project.runtime_directory):
        if str(task.get("status")) not in {"merged", "blocked", "failed", "rejected"}:
            updated.append(
                transition_task(
                    files.project.runtime_directory,
                    task,
                    "blocked",
                    {"goal_aborted": True, "reason": reason},
                )
            )
    return {"status": "aborted", "reason": reason, "tasks": updated}


def _agent_swap(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    profile = load_session_profile(files.project)
    agent = _find_agent(profile.agents, str(payload["agent_id"]))
    replacement = AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=model_profile_from_ref(str(payload["to"])),
        executor_adapter=agent.executor_adapter,
        state="handoff_needed" if agent.active_task else "ready",
        quota_policy=agent.quota_policy,
        active_task=agent.active_task,
    )
    save_session_profile(files.project, update_agent(profile, replacement))
    return {
        "agent_id": replacement.agent_id,
        "model_ref": replacement.model_profile.model_ref,
    }


def _agent_state(start: Path, payload: dict[str, Any], state: str) -> dict[str, Any]:
    files = load_project_files(start)
    profile = load_session_profile(files.project)
    agent = _find_agent(profile.agents, str(payload["agent_id"]))
    updated = AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=agent.model_profile,
        executor_adapter=agent.executor_adapter,
        state=state,  # type: ignore[arg-type]
        quota_policy=agent.quota_policy,
        active_task=agent.active_task,
    )
    save_session_profile(files.project, update_agent(profile, updated))
    return {"agent_id": updated.agent_id, "state": updated.state}


def _quota_status(start: Path) -> dict[str, Any]:
    files = load_project_files(start)
    profile = load_session_profile(files.project)
    return {
        "quotas": [
            {
                "agent_id": agent.agent_id,
                **load_quota(files.project.runtime_directory, agent).__dict__,
            }
            for agent in profile.agents
        ]
    }


def _checkpoint_create(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    tasks = load_tasks(files.project.runtime_directory)
    for task in tasks:
        if str(task.get("id")) != str(payload["task_id"]):
            continue
        package = files.packages[str(task["package_id"])]
        workspace = Path(
            str(task.get("worktree", package_worktree(files.project, package)))
        )
        branch = str(task.get("branch", package_branch(package)))
        if not workspace.exists():
            raise LfgError(f"Task worktree does not exist: {workspace}")
        if branch == files.project.integration_branch:
            raise LfgError("Refusing checkpoint on integration branch")
        result = create_checkpoint_commit(
            files.project,
            task_id=str(task["id"]),
            workspace=workspace,
            attempt=int(task.get("attempt", 0)),
            allowed_paths=package.owned_paths,
        )
        return result.to_dict()
    raise LfgError(f"Unknown task: {payload['task_id']}")


def _integration_status(start: Path) -> dict[str, Any]:
    files = load_project_files(start)
    return {"candidates": integration_candidates(files)}


def _skill_create(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    path = create_runtime_skill(
        files.project, str(payload["name"]), str(payload["text"])
    )
    return {"path": str(path), "runtime_only": True}


def _skill_promote(start: Path, payload: dict[str, Any]) -> dict[str, Any]:
    files = load_project_files(start)
    path = promote_runtime_skill(files.project, str(payload["name"]))
    return {"path": str(path), "runtime_only": False}


def _find_agent(agents: tuple[AgentInstance, ...], agent_id: str) -> AgentInstance:
    for agent in agents:
        if agent.agent_id == agent_id:
            return agent
    raise LfgError(f"Unknown agent: {agent_id}")
