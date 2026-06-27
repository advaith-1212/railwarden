from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import yaml

from lfg.config.models import ProjectConfig
from lfg.errors import LfgError
from lfg.planning.antigravity import AntigravityClaudePlanner
from lfg.runtime.events import append_event
from lfg.runtime.tasks import ensure_task
from lfg.util.atomic import atomic_write_json, atomic_write_text


@dataclass(frozen=True)
class PendingPlan:
    run_id: str
    goal: str
    plan_markdown: str
    work_packages: list[dict[str, Any]]
    planner_output: dict[str, Any]


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def build_planning_prompt(goal: str) -> str:
    return f"""You are the LFG planning architect.

Create a software implementation plan and DAG work packages for this goal.

Goal:
{goal}

Return strict JSON with:
- plan_markdown: human-readable implementation plan.
- work_packages: array of objects with id, name, objective, dependencies,
  owned_paths, forbidden_paths, acceptance_tests, preferred_providers,
  optional branch, optional worktree, optional status_notes.
"""


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first == -1 or last == -1 or last < first:
        raise LfgError("Planner output did not contain a JSON object")
    payload = json.loads(stripped[first : last + 1])
    if not isinstance(payload, dict):
        raise LfgError("Planner output JSON must be an object")
    return payload


def parse_planner_output(text: str) -> PendingPlan:
    payload = _extract_json(text)
    plan_markdown = payload.get("plan_markdown") or payload.get("plan")
    packages = payload.get("work_packages")
    if not isinstance(plan_markdown, str) or not plan_markdown.strip():
        raise LfgError("Planner output requires plan_markdown")
    if not isinstance(packages, list):
        raise LfgError("Planner output requires work_packages list")
    normalized: list[dict[str, Any]] = []
    for raw in packages:
        if not isinstance(raw, dict):
            raise LfgError("Each work package must be an object")
        package_id = str(raw.get("id", "")).strip()
        if not package_id:
            raise LfgError("Each work package requires id")
        normalized.append(
            {
                "id": package_id,
                "name": str(raw.get("name", package_id)),
                "objective": str(raw.get("objective", "")),
                "dependencies": list(raw.get("dependencies", [])),
                "owned_paths": list(raw.get("owned_paths", [])),
                "forbidden_paths": list(raw.get("forbidden_paths", [])),
                "acceptance_tests": list(raw.get("acceptance_tests", [])),
                "preferred_providers": list(raw.get("preferred_providers", [])),
                **({"branch": str(raw["branch"])} if raw.get("branch") else {}),
                **({"worktree": str(raw["worktree"])} if raw.get("worktree") else {}),
                **(
                    {"status_notes": str(raw["status_notes"])}
                    if raw.get("status_notes") is not None
                    else {}
                ),
            }
        )
    return PendingPlan(
        run_id="",
        goal="",
        plan_markdown=plan_markdown.strip() + "\n",
        work_packages=normalized,
        planner_output=payload,
    )


def _planner_text(config: ProjectConfig, prompt: str) -> tuple[str, dict[str, Any]]:
    planner = AntigravityClaudePlanner(config.planner_model)
    command = planner.command(repository=config.repository_root, prompt=prompt)
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    evidence = {
        "provider": config.planner_provider,
        "model": config.planner_model,
        "command": [*command[:6], "<prompt>"],
        "returncode": completed.returncode,
        "stderr": completed.stderr,
    }
    if completed.returncode != 0:
        raise LfgError(f"Planner failed: {completed.stderr.strip()}")
    return completed.stdout, evidence


def create_pending_plan(
    config: ProjectConfig,
    goal: str,
    *,
    planner_output_text: str | None = None,
) -> PendingPlan:
    run_id = new_run_id()
    prompt = build_planning_prompt(goal)
    runtime_dir = config.runtime_directory
    run_dir = runtime_dir / "runs" / run_id
    atomic_write_text(run_dir / "goal.md", goal.strip() + "\n")
    if planner_output_text is None:
        output_text, evidence = _planner_text(config, prompt)
    else:
        output_text = planner_output_text
        evidence = {
            "provider": "fixture",
            "model": "fixture",
            "command": [],
            "returncode": 0,
            "stderr": "",
        }
    parsed = parse_planner_output(output_text)
    raw_evidence = {
        **evidence,
        "run_id": run_id,
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "created_at": time.time(),
        "raw_output": output_text,
        "parsed": parsed.planner_output,
    }
    pending = PendingPlan(
        run_id=run_id,
        goal=goal.strip(),
        plan_markdown=parsed.plan_markdown,
        work_packages=parsed.work_packages,
        planner_output=raw_evidence,
    )
    atomic_write_json(run_dir / "planner-output.json", raw_evidence)
    atomic_write_json(
        runtime_dir / "state" / "pending-plan.json",
        {
            "run_id": run_id,
            "goal": pending.goal,
            "plan_path": str(run_dir / "plan.md"),
            "work_packages": pending.work_packages,
            "approved": False,
            "created_at": time.time(),
        },
    )
    atomic_write_text(run_dir / "plan.md", pending.plan_markdown)
    append_event(runtime_dir, "plan_created", {"run_id": run_id})
    return pending


def approve_latest_plan(config: ProjectConfig) -> dict[str, Any]:
    path = config.runtime_directory / "state" / "pending-plan.json"
    if not path.exists():
        raise LfgError("No pending plan exists")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LfgError("Pending plan state is invalid")
    packages = payload.get("work_packages")
    if not isinstance(packages, list):
        raise LfgError("Pending plan has no work packages")
    config_dir = config.repository_root / ".lfg"
    atomic_write_text(
        config_dir / "plan.md",
        (config.runtime_directory / "runs" / str(payload["run_id"]) / "plan.md").read_text(
            encoding="utf-8"
        ),
    )
    atomic_write_text(
        config_dir / "work_packages.yaml",
        yaml.safe_dump(
            {"schema_version": "1.0.0", "work_packages": packages},
            sort_keys=False,
        ),
    )
    for package in packages:
        if isinstance(package, dict):
            ensure_task(
                config.runtime_directory,
                package_id=str(package["id"]),
                name=str(package.get("name", package["id"])),
                dependencies=tuple(str(item) for item in package.get("dependencies", [])),
            )
    payload["approved"] = True
    payload["approved_at"] = time.time()
    atomic_write_json(path, payload)
    append_event(config.runtime_directory, "plan_approved", {"run_id": payload["run_id"]})
    return payload


def plan_is_approved(config: ProjectConfig) -> bool:
    path = config.runtime_directory / "state" / "pending-plan.json"
    if not path.exists():
        return not config.execution_require_plan_approval
    payload = json.loads(path.read_text(encoding="utf-8"))
    return isinstance(payload, dict) and bool(payload.get("approved"))
