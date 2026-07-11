from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lfg.config.models import ProjectConfig
from lfg.errors import LfgError
from lfg.planning.antigravity import AntigravityClaudePlanner
from lfg.runtime.events import append_event
from lfg.runtime.tasks import ensure_task
from lfg.runtime.workflow import advance_workflow
from lfg.util.atomic import atomic_write_json, atomic_write_text
from lfg.validation.commands import sanitize_planner_validation_commands
from lfg.validation.contract import infer_scaffold_owned_paths


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

Return JSON only. You may wrap it in a ```json fence.

Required shape:
{{
  "plan_markdown": "<human-readable implementation plan for Hermes to review>",
  "work_packages": [
    {{
      "id": "WP-001",
      "name": "Short name",
      "objective": "Concrete implementation objective",
      "dependencies": [],
      "owned_paths": [],
      "forbidden_paths": [],
      "acceptance_tests": [],
      "acceptance_criteria": [],
      "validation_commands": [["npm", "test"]],
      "preferred_providers": [],
      "model_profile": null,
      "reviewer_profile": null,
      "risk_level": "medium",
      "context_refs": [],
      "merge_policy": "auto_after_review",
      "approval_required": false,
      "review_required": true,
      "branch": null,
      "worktree": null,
      "status_notes": null
    }}
  ]
}}

Focus on:
- concrete work packages
- dependencies between packages
- owned paths and forbidden paths
- acceptance checks
- provider hints when they are useful

context_refs must reference existing files under context/ only.
Do not put owned_paths or other future artifacts in context_refs.
Leave context_refs empty to use project defaults.

validation_commands must be structured argv arrays only, for example:
[["npm", "install"], ["npm", "test"]]
Do not use shell operators such as &&, |, &, or backgrounding.
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


def build_structuring_prompt(goal: str, plan_markdown: str) -> str:
    return f"""You are converting an LFG planning response into machine-readable data.

Goal:
{goal}

Human-readable plan:
```markdown
{plan_markdown.strip()}
```

Return JSON only. You may wrap it in a ```json fence.

Required shape:
{{
  "plan_markdown": "<the human-readable plan, preserved or lightly cleaned>",
  "work_packages": [
    {{
      "id": "WP-1",
      "name": "Short name",
      "objective": "Concrete objective",
      "dependencies": [],
      "owned_paths": [],
      "forbidden_paths": [],
      "acceptance_tests": [],
      "acceptance_criteria": [],
      "validation_commands": [["npm", "test"]],
      "preferred_providers": [],
      "model_profile": null,
      "reviewer_profile": null,
      "risk_level": "medium",
      "context_refs": [],
      "merge_policy": "auto_after_review",
      "approval_required": false,
      "review_required": true,
      "branch": null,
      "worktree": null,
      "status_notes": null
    }}
  ]
}}

Rules:
- Every work package must have a stable `id`.
- `dependencies` must reference other package ids.
- Use empty arrays when information is unknown, not prose.
- Preserve the markdown plan in `plan_markdown`.
- `context_refs` must reference existing files under context/ only, never owned_paths.
"""


def _extract_code_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            if inside:
                blocks.append("\n".join(current).strip())
                current = []
                inside = False
            else:
                inside = True
            continue
        if inside:
            current.append(line)
    return [block for block in blocks if block]


def _extract_mapping(text: str) -> dict[str, Any]:
    candidates = [text.strip(), *_extract_code_blocks(text)]
    last_error: Exception | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
        else:
            if isinstance(payload, dict):
                return payload
        try:
            payload = yaml.safe_load(candidate)
        except yaml.YAMLError as exc:
            last_error = exc
            continue
        if isinstance(payload, dict):
            return payload
    raise LfgError(
        "Planner output did not contain structured plan data"
    ) from last_error


def _normalize_work_packages(packages: Any) -> list[dict[str, Any]]:
    if not isinstance(packages, list):
        raise LfgError("Planner output requires work_packages list")
    normalized: list[dict[str, Any]] = []
    for raw in packages:
        if not isinstance(raw, dict):
            raise LfgError("Each work package must be an object")
        package_id = str(raw.get("id", "")).strip()
        if not package_id:
            raise LfgError("Each work package requires id")
        raw_validation = raw.get("validation_commands", raw.get("validation", [])) or []
        validation_commands = sanitize_planner_validation_commands(raw_validation)
        normalized.append(
            {
                "id": package_id,
                "name": str(raw.get("name", package_id)),
                "objective": str(raw.get("objective", "")),
                "dependencies": list(raw.get("dependencies", [])),
                "owned_paths": list(raw.get("owned_paths", [])),
                "forbidden_paths": list(raw.get("forbidden_paths", [])),
                "acceptance_tests": list(raw.get("acceptance_tests", [])),
                "acceptance_criteria": list(raw.get("acceptance_criteria", [])),
                "validation_commands": validation_commands,
                "preferred_providers": list(raw.get("preferred_providers", [])),
                "model_profile": raw.get("model_profile"),
                "reviewer_profile": raw.get("reviewer_profile"),
                "risk_level": str(raw.get("risk_level", "medium")),
                "context_refs": list(raw.get("context_refs", [])),
                "merge_policy": str(raw.get("merge_policy", "auto_after_review")),
                "approval_required": bool(raw.get("approval_required", False)),
                "review_required": bool(raw.get("review_required", True)),
                **({"branch": str(raw["branch"])} if raw.get("branch") else {}),
                **({"worktree": str(raw["worktree"])} if raw.get("worktree") else {}),
                **(
                    {"status_notes": str(raw["status_notes"])}
                    if raw.get("status_notes") is not None
                    else {}
                ),
            }
        )
    return normalized


def parse_planner_output(
    text: str,
    *,
    default_plan_markdown: str | None = None,
) -> PendingPlan:
    try:
        payload = _extract_json(text)
    except LfgError:
        payload = _extract_mapping(text)
    plan_markdown = (
        payload.get("plan_markdown") or payload.get("plan") or default_plan_markdown
    )
    if not isinstance(plan_markdown, str) or not plan_markdown.strip():
        raise LfgError("Planner output requires plan_markdown")
    normalized = _normalize_work_packages(payload.get("work_packages"))
    return PendingPlan(
        run_id="",
        goal="",
        plan_markdown=plan_markdown.strip() + "\n",
        work_packages=normalized,
        planner_output=payload,
    )


def _planner_text(
    config: ProjectConfig,
    prompt: str,
    *,
    purpose: str,
) -> tuple[str, dict[str, Any]]:
    planner = AntigravityClaudePlanner(config.planner_model)
    command = planner.command(repository=config.repository_root, prompt=prompt)
    timeout = float(os.environ.get("LFG_PLANNER_TIMEOUT_SECONDS", "300"))
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LfgError(
            f"Planner timed out after {timeout:g}s during {purpose}"
        ) from exc
    evidence = {
        "provider": config.planner_provider,
        "model": config.planner_model,
        "purpose": purpose,
        "command": [*command[:6], "<prompt>"],
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "timeout_seconds": timeout,
    }
    if completed.returncode != 0:
        raise LfgError(f"Planner failed: {completed.stderr.strip()}")
    return completed.stdout, evidence


def create_pending_plan(
    config: ProjectConfig,
    goal: str,
    *,
    planner_output_text: str | None = None,
    planner_structured_output_text: str | None = None,
    run_id: str | None = None,
) -> PendingPlan:
    run_id = run_id or new_run_id()
    prompt = build_planning_prompt(goal)
    runtime_dir = config.runtime_directory
    run_dir = runtime_dir / "runs" / run_id
    atomic_write_text(run_dir / "goal.md", goal.strip() + "\n")
    if planner_output_text is None:
        output_text, evidence = _planner_text(config, prompt, purpose="planning")
    else:
        output_text = planner_output_text
        evidence = {
            "provider": "fixture",
            "model": "fixture",
            "purpose": "planning",
            "command": [],
            "returncode": 0,
            "stderr": "",
        }
    used_structuring_pass = False
    structured_evidence: dict[str, Any] | None = None
    try:
        parsed = parse_planner_output(output_text)
        plan_markdown = parsed.plan_markdown
        structured_output_text = output_text
    except LfgError:
        used_structuring_pass = True
        structuring_prompt = build_structuring_prompt(goal, output_text)
        if planner_structured_output_text is not None:
            structured_output_text = planner_structured_output_text
            structured_evidence = {
                "provider": "fixture",
                "model": "fixture",
                "purpose": "structuring",
                "command": [],
                "returncode": 0,
                "stderr": "",
            }
        elif planner_output_text is None:
            structured_output_text, structured_evidence = _planner_text(
                config,
                structuring_prompt,
                purpose="structuring",
            )
        else:
            raise
        parsed = parse_planner_output(
            structured_output_text,
            default_plan_markdown=output_text,
        )
        plan_markdown = output_text.strip() + "\n"
    raw_evidence = {
        **evidence,
        "run_id": run_id,
        "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "created_at": time.time(),
        "planning_output": output_text,
        "used_structuring_pass": used_structuring_pass,
        "structuring_output": structured_output_text if used_structuring_pass else None,
        "structuring_prompt_hash": (
            hashlib.sha256(
                build_structuring_prompt(goal, output_text).encode("utf-8")
            ).hexdigest()
            if used_structuring_pass
            else None
        ),
        "structuring_evidence": structured_evidence,
        "parsed": parsed.planner_output,
    }
    pending = PendingPlan(
        run_id=run_id,
        goal=goal.strip(),
        plan_markdown=plan_markdown,
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
    advance_workflow(runtime_dir, "PLAN_CREATED", payload={"run_id": run_id})
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
    normalized_packages = _normalize_packages_for_repo(config.repository_root, packages)
    normalized_packages = _resolve_owned_path_overlaps(normalized_packages)
    all_owned_paths = _collect_owned_paths(normalized_packages)
    for index, package in enumerate(normalized_packages):
        if isinstance(package, dict):
            package["owned_paths"] = infer_scaffold_owned_paths(package)
            package["context_refs"] = _sanitize_context_refs(
                config.repository_root,
                package,
                all_owned_paths=all_owned_paths,
            )
            package["validation_commands"] = sanitize_planner_validation_commands(
                package.get("validation_commands", [])
            )
            normalized_packages[index] = package
    from lfg.validation.contract import validate_packages_for_freeze

    validate_packages_for_freeze(
        config.repository_root,
        normalized_packages,
        require_context_refs=True,
    )
    config_dir = config.repository_root / ".lfg"
    atomic_write_text(
        config_dir / "plan.md",
        (
            config.runtime_directory / "runs" / str(payload["run_id"]) / "plan.md"
        ).read_text(encoding="utf-8"),
    )
    atomic_write_text(
        config_dir / "work_packages.yaml",
        yaml.safe_dump(
            {"schema_version": "2.0.0", "work_packages": normalized_packages},
            sort_keys=False,
        ),
    )
    payload["work_packages"] = normalized_packages
    _write_orchestration_artifacts(config, payload, normalized_packages)
    for package in normalized_packages:
        if isinstance(package, dict):
            ensure_task(
                config.runtime_directory,
                package_id=str(package["id"]),
                name=str(package.get("name", package["id"])),
                dependencies=tuple(
                    str(item) for item in package.get("dependencies", [])
                ),
            )
    payload["approved"] = True
    payload["approved_at"] = time.time()
    atomic_write_json(path, payload)
    append_event(
        config.runtime_directory, "plan_approved", {"run_id": payload["run_id"]}
    )
    advance_workflow(
        config.runtime_directory,
        "CONTRACTS_FROZEN",
        payload={"run_id": payload["run_id"]},
    )
    return payload


def _resolve_owned_path_overlaps(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: dict[str, list[str]] = {}
    for package in packages:
        package_id = str(package.get("id", ""))
        for path in package.get("owned_paths", []):
            claims.setdefault(str(path), []).append(package_id)

    winners: dict[str, str] = {}
    for path, owners in claims.items():
        if len(owners) == 1:
            winners[path] = owners[0]
            continue
        winners[path] = max(owners, key=_package_sort_key)

    resolved: list[dict[str, Any]] = []
    for package in packages:
        updated = dict(package)
        owned = [
            path
            for path in updated.get("owned_paths", [])
            if winners.get(str(path)) == str(package.get("id"))
        ]
        updated["owned_paths"] = owned
        resolved.append(updated)
    return resolved


def _package_sort_key(package_id: str) -> tuple[int, str]:
    suffix = package_id.rsplit("-", 1)[-1]
    if suffix.isdigit():
        return (int(suffix), package_id)
    return (0, package_id)


def _collect_owned_paths(packages: list[dict[str, Any]]) -> set[str]:
    owned: set[str] = set()
    for package in packages:
        if not isinstance(package, dict):
            continue
        for path in package.get("owned_paths", []):
            owned.add(str(path).rstrip("/"))
    return owned


def _default_context_refs(repository_root: Path) -> list[str]:
    defaults = [
        "context/ARCHITECTURE.md",
        "context/TEST_STRATEGY.md",
        "context/CONTRIBUTING_AGENTS.md",
    ]
    return [ref for ref in defaults if (repository_root / ref).is_file()]


def _sanitize_context_refs(
    repository_root: Path,
    package: dict[str, Any],
    *,
    all_owned_paths: set[str],
) -> list[str]:
    refs = [
        str(item).strip()
        for item in package.get("context_refs", [])
        if str(item).strip()
    ]
    sanitized: list[str] = []
    for ref in refs:
        if ref.startswith("skill:"):
            sanitized.append(ref)
            continue
        if not ref.startswith("context/"):
            continue
        if ref.rstrip("/") in all_owned_paths:
            continue
        path = repository_root / ref
        if path.is_file():
            sanitized.append(ref)
    if sanitized:
        return sanitized
    return _default_context_refs(repository_root)


def _normalize_packages_for_repo(
    repository_root: Path, packages: list[Any]
) -> list[dict[str, Any]]:
    candidates = _repo_path_candidates(repository_root)
    normalized: list[dict[str, Any]] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        updated = dict(package)
        updated["owned_paths"] = _normalize_path_list(
            package.get("owned_paths", []), candidates
        )
        updated["forbidden_paths"] = _normalize_path_list(
            package.get("forbidden_paths", []), candidates
        )
        normalized.append(updated)
    return normalized


def _repo_path_candidates(repository_root: Path) -> list[str]:
    paths: set[str] = set()
    for path in repository_root.rglob("*"):
        if (
            ".git" in path.parts
            or ".lfg-runtime" in path.parts
            or ".lfg-worktrees" in path.parts
        ):
            continue
        relative = path.relative_to(repository_root).as_posix().rstrip("/")
        if relative:
            paths.add(relative)
    return sorted(paths)


def _normalize_path_list(values: Any, candidates: list[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_normalize_repo_path(str(value), candidates) for value in values]


def _normalize_repo_path(value: str, candidates: list[str]) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.rstrip("/")
    if not normalized:
        return normalized
    if normalized.startswith(".lfg-worktrees/"):
        leaf = normalized.split("/", 3)[-1]
        return leaf if leaf else normalized
    if normalized in candidates:
        return normalized

    basename = Path(normalized).name
    basename_matches = [item for item in candidates if Path(item).name == basename]
    if len(basename_matches) == 1:
        return basename_matches[0]

    close = difflib.get_close_matches(
        basename,
        [Path(item).name for item in candidates],
        n=2,
        cutoff=0.75,
    )
    if len(close) == 1:
        matched = [item for item in candidates if Path(item).name == close[0]]
        if len(matched) == 1:
            return matched[0]
    return normalized


def _write_orchestration_artifacts(
    config: ProjectConfig, payload: dict[str, Any], packages: list[Any]
) -> None:
    config_dir = config.repository_root / ".lfg"
    normalized = [package for package in packages if isinstance(package, dict)]
    frozen_at = time.time()
    atomic_write_text(
        config_dir / "contract_freeze_manifest.yaml",
        yaml.safe_dump(
            {
                "schema_version": "2.0.0",
                "run_id": payload["run_id"],
                "goal": payload.get("goal", ""),
                "frozen_at": frozen_at,
                "approved": True,
                "package_ids": [str(package["id"]) for package in normalized],
                "contract_hash": hashlib.sha256(
                    json.dumps(normalized, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            },
            sort_keys=False,
        ),
    )
    atomic_write_text(
        config_dir / "model_assignment.yaml",
        yaml.safe_dump(
            {
                "schema_version": "2.0.0",
                "assignments": [
                    {
                        "package_id": str(package["id"]),
                        "model_profile": package.get("model_profile")
                        or (package.get("preferred_providers") or [None])[0],
                        "reviewer_profile": package.get("reviewer_profile"),
                        "preferred_providers": package.get("preferred_providers", []),
                    }
                    for package in normalized
                ],
            },
            sort_keys=False,
        ),
    )
    atomic_write_text(
        config_dir / "dependency_graph.mmd", _dependency_graph(normalized)
    )
    atomic_write_text(
        config_dir / "ownership_matrix.csv", _ownership_matrix(normalized)
    )
    prompt_dir = config_dir / "agent_prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for package in normalized:
        atomic_write_text(
            prompt_dir / f"{str(package['id']).lower()}.md",
            _contract_prompt(str(payload.get("goal", "")), package),
        )


def _dependency_graph(packages: list[dict[str, Any]]) -> str:
    lines = ["flowchart TD"]
    for package in packages:
        package_id = str(package["id"])
        label = str(package.get("name") or package_id).replace('"', "'")
        lines.append(f'  {package_id}["{package_id}: {label}"]')
        for dependency in package.get("dependencies", []):
            lines.append(f"  {dependency} --> {package_id}")
    return "\n".join(lines) + "\n"


def _ownership_matrix(packages: list[dict[str, Any]]) -> str:
    rows = ["package_id,path,access"]
    for package in packages:
        package_id = str(package["id"])
        for path in package.get("owned_paths", []):
            rows.append(f"{package_id},{path},owned")
        for path in package.get("forbidden_paths", []):
            rows.append(f"{package_id},{path},forbidden")
    return "\n".join(rows) + "\n"


def _contract_prompt(goal: str, package: dict[str, Any]) -> str:
    package_id = str(package["id"])
    lines = [
        f"# {package_id} {package.get('name', package_id)}",
        "",
        "## Goal",
        goal.strip() or "(not recorded)",
        "",
        "## Objective",
        str(package.get("objective", "")).strip(),
        "",
        "## Acceptance Criteria",
        *[f"- {item}" for item in package.get("acceptance_criteria", [])],
        "",
        "## Validation Commands",
        *[f"- {item}" for item in package.get("validation_commands", [])],
        "",
        "## Owned Paths",
        *[f"- {item}" for item in package.get("owned_paths", [])],
        "",
        "## Forbidden Paths",
        *[f"- {item}" for item in package.get("forbidden_paths", [])],
    ]
    return "\n".join(lines).rstrip() + "\n"


def plan_is_approved(config: ProjectConfig) -> bool:
    path = config.runtime_directory / "state" / "pending-plan.json"
    if not path.exists():
        return not config.execution_require_plan_approval
    payload = json.loads(path.read_text(encoding="utf-8"))
    return isinstance(payload, dict) and bool(payload.get("approved"))
