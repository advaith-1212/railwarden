"""Deterministic, credential-free demonstration of the RailWarden kernel.

This module is deliberately test infrastructure, not a provider adapter.  It
uses scripted file changes in disposable worktrees to exercise the same Git,
ledger, validation, retry, and integration mechanics used by the runtime.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

from railwarden.config.init import initialize_project
from railwarden.config.loader import load_project_files
from railwarden.config.models import ValidationCommand
from railwarden.git import current_branch, head, run_git
from railwarden.integration.manager import integrate_one
from railwarden.provisioning.worktrees import ensure_worktree
from railwarden.runtime.events import append_event
from railwarden.runtime.tasks import ensure_task, transition_task
from railwarden.scheduler.classifier import classify_packages
from railwarden.util.atomic import atomic_write_json
from railwarden.validation.runner import run_validation_suite

DEMO_MARKER = "demo.yaml"


def seed_demo(repository: Path) -> Path:
    """Add a small approved, scripted plan to an initialized repository."""
    config_dir = repository / ".railwarden"
    marker = config_dir / DEMO_MARKER
    if not config_dir.exists():
        initialize_project(repository, yes=True)
    project_path = config_dir / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    if not isinstance(project, dict):
        raise ValueError("Demo project configuration must be a mapping")
    project_data = project.setdefault("project", {})
    if not isinstance(project_data, dict):
        raise ValueError("Demo project configuration is invalid")
    project_data["integration_branch"] = current_branch(repository)
    project_path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    packages = {
        "schema_version": "1.0.0",
        "work_packages": [
            {
                "id": "DEMO-1",
                "name": "Create a worker artifact",
                "objective": "Demonstrate an isolated worker commit.",
                "owned_paths": ["demo/worker-one.txt"],
                "dependencies": [],
                "acceptance_criteria": ["A committed worker artifact exists."],
            },
            {
                "id": "DEMO-2",
                "name": "Create a dependent artifact",
                "objective": "Demonstrate dependency-aware integration.",
                "owned_paths": ["demo/worker-two.txt"],
                "dependencies": ["DEMO-1"],
                "acceptance_criteria": ["The dependent artifact is integrated."],
            },
        ],
    }
    (config_dir / "work_packages.yaml").write_text(
        yaml.safe_dump(packages, sort_keys=False), encoding="utf-8"
    )
    marker.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "approved": True,
                "provider": "scripted-fake",
                "notice": "Demo-only fake provider; never use as a production adapter.",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return marker


def _commit(repository: Path, message: str) -> None:
    run_git(repository, "add", "-A")
    run_git(
        repository,
        "-c",
        "user.name=RailWarden demo",
        "-c",
        "user.email=demo@railwarden.invalid",
        "commit",
        "-m",
        message,
    )


def _prepare_configuration_commit(repository: Path) -> None:
    dirty = run_git(repository, "diff", "--quiet", check=False).returncode != 0
    untracked = run_git(repository, "status", "--porcelain").stdout.strip()
    if dirty or untracked:
        _commit(repository, "chore: configure RailWarden demo")


def _worker_commit(worktree: Path, filename: str, text: str, message: str) -> str:
    target = worktree / "demo" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _commit(worktree, message)
    return head(worktree)


def run_demo(repository: Path) -> dict[str, Any]:
    """Run the documented deterministic acceptance scenario in ``repository``."""
    marker = repository / ".railwarden" / DEMO_MARKER
    if not marker.exists():
        raise ValueError("Run `warden init --demo` before `warden demo run`.")
    _prepare_configuration_commit(repository)
    files = load_project_files(repository)
    runtime = files.project.runtime_directory
    started = time.time()
    append_event(runtime, "demo_started", {"provider": "scripted-fake"})
    tasks = {
        package_id: ensure_task(
            runtime,
            package_id=package_id,
            name=package.name,
            dependencies=package.dependencies,
        )
        for package_id, package in files.packages.items()
    }
    worktrees: dict[str, Path] = {}
    commits: dict[str, str] = {}
    for package_id in ("DEMO-1", "DEMO-2"):
        result = ensure_worktree(
            repository=repository,
            integration_branch=files.project.integration_branch,
            workspace=files.project.worktree_root / package_id.lower(),
            branch=f"railwarden/{package_id}",
            action="execute",
        )
        workspace = Path(str(result["workspace"]))
        worktrees[package_id] = workspace
        transition_task(runtime, tasks[package_id], "running", {"attempt": 1})
        append_event(
            runtime,
            "scripted_worker_started",
            {"provider": "fake"},
            task_id=tasks[package_id]["id"],
        )
    commits["DEMO-1"] = _worker_commit(
        worktrees["DEMO-1"], "worker-one.txt", "first worker\n", "demo: worker one"
    )
    # The failure is intentional evidence: it verifies recovery mechanics without
    # requiring a live provider or a failing repository state.
    failed_status, failed_results, _ = run_validation_suite(
        repository,
        (
            ValidationCommand(
                "intentional-demo-failure",
                Path(),
                ("python", "-c", "raise SystemExit(1)"),
            ),
        ),
    )
    if failed_status != "failed":
        raise RuntimeError("The deterministic demo failure did not fail")
    append_event(
        runtime,
        "validation_failed",
        {"intentional": True, "results": [item.to_dict() for item in failed_results]},
        task_id=tasks["DEMO-1"]["id"],
    )
    transition_task(
        runtime, tasks["DEMO-1"], "handoff_needed", {"reason": "demo retry"}
    )
    append_event(
        runtime,
        "handoff_created",
        {"to": "scripted-fake-retry"},
        task_id=tasks["DEMO-1"]["id"],
    )
    transition_task(runtime, tasks["DEMO-1"], "validating", {"attempt": 2})
    success_command = ValidationCommand(
        "demo-validation", Path(), ("python", "-c", "print('demo ok')")
    )
    # Classifying the packages validates the frozen dependency DAG; active tasks
    # are deliberately integrated through their explicit, recorded candidates.
    classify_packages(files.project, files.packages)
    first = {
        "package_id": "DEMO-1",
        "name": files.packages["DEMO-1"].name,
        "branch": "railwarden/DEMO-1",
        "head": commits["DEMO-1"],
        "task_id": tasks["DEMO-1"]["id"],
    }
    first_merge = integrate_one(
        config=files.project,
        candidate=first,
        validation_commands=(success_command,),
        execute=True,
    )
    transition_task(runtime, tasks["DEMO-1"], "merged", {"commit": commits["DEMO-1"]})
    commits["DEMO-2"] = _worker_commit(
        worktrees["DEMO-2"], "worker-two.txt", "dependent worker\n", "demo: worker two"
    )
    transition_task(runtime, tasks["DEMO-2"], "validating", {"attempt": 1})
    classify_packages(files.project, files.packages)
    second = {
        "package_id": "DEMO-2",
        "name": files.packages["DEMO-2"].name,
        "branch": "railwarden/DEMO-2",
        "head": commits["DEMO-2"],
        "task_id": tasks["DEMO-2"]["id"],
    }
    second_merge = integrate_one(
        config=files.project,
        candidate=second,
        validation_commands=(success_command,),
        execute=True,
    )
    transition_task(runtime, tasks["DEMO-2"], "merged", {"commit": commits["DEMO-2"]})
    report = {
        "schema_version": "1.0.0",
        "status": "passed",
        "started_at": started,
        "completed_at": time.time(),
        "provider": "scripted-fake",
        "worktrees": {key: str(value) for key, value in worktrees.items()},
        "commits": commits,
        "intentional_failure": [item.to_dict() for item in failed_results],
        "integrations": [first_merge, second_merge],
        "event_count": len(
            (runtime / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ),
    }
    cleanup: list[str] = []
    for package_id, workspace in worktrees.items():
        run_git(repository, "worktree", "remove", "--force", str(workspace))
        cleanup.append(package_id)
    report["cleaned_worktrees"] = cleanup
    report_path = runtime / "reports" / "demo-acceptance.json"
    atomic_write_json(report_path, report)
    append_event(runtime, "demo_completed", {"report": str(report_path)})
    report["report"] = str(report_path)
    return report
