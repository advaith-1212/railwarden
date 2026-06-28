from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from lfg.config.init import initialize_project
from lfg.config.loader import load_project_files
from lfg.engine.controller import controller_tick
from lfg.engine.dashboard import render_dashboard
from lfg.runtime.tasks import load_tasks, save_tasks


def _write_package(repo: Path, *, preferred: list[str] | None = None) -> None:
    (repo / ".lfg" / "work_packages.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "work_packages": [
                    {
                        "id": "WP-1",
                        "name": "One",
                        "objective": "Edit app",
                        "dependencies": [],
                        "owned_paths": ["app/"],
                        "preferred_providers": preferred or [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _approve(repo: Path) -> None:
    runtime = repo / ".lfg-runtime"
    (runtime / "state").mkdir(parents=True, exist_ok=True)
    (runtime / "state" / "pending-plan.json").write_text(
        json.dumps({"approved": True, "goal": "ship", "run_id": "test"}),
        encoding="utf-8",
    )


def test_controller_waits_for_plan_approval(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    _write_package(git_repo)
    files = load_project_files(git_repo)

    result = controller_tick(files, launch=False)

    assert result["status"] == "waiting_for_plan_approval"


def test_controller_assigns_ready_task_by_provider_priority(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    project_path = git_repo / ".lfg" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["project"]["integration_branch"] = "main"
    project["workers"]["providers"] = ["composer", "codex"]
    project["providers"]["composer"]["priority"] = 30
    project["providers"]["codex"]["priority"] = 10
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    _write_package(git_repo)
    _approve(git_repo)
    files = load_project_files(git_repo)

    result = controller_tick(files, launch=False)

    assert result["launched"] == [{"task_id": "task-WP-1", "provider": "codex"}]
    task = load_tasks(files.project.runtime_directory)[0]
    assert task["status"] == "assigned"
    assert task["provider"] == "codex"


def test_dashboard_renders_runtime_state(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    _write_package(git_repo)
    _approve(git_repo)
    files = load_project_files(git_repo)
    controller_tick(files, launch=False)

    dashboard = render_dashboard(load_project_files(git_repo))

    assert "LFG Dashboard" in dashboard
    assert "WP-1" in dashboard
    assert "Providers" in dashboard


def test_controller_runs_lfg_validation_and_review_before_merge(
    git_repo: Path,
) -> None:
    initialize_project(git_repo, yes=True)
    project_path = git_repo / ".lfg" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["project"]["integration_branch"] = "main"
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    (git_repo / ".lfg" / "work_packages.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "2.0.0",
                "work_packages": [
                    {
                        "id": "WP-1",
                        "name": "Validated package",
                        "objective": "Do work",
                        "owned_paths": ["app"],
                        "preferred_providers": ["codex"],
                        "validation_commands": [
                            {
                                "name": "package-check",
                                "command": {
                                    "cwd": ".",
                                    "argv": ["python3", "-c", "print('ok')"],
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _approve(git_repo)
    files = load_project_files(git_repo)
    controller_tick(files, launch=False, integrate=False)
    task = load_tasks(files.project.runtime_directory)[0]
    workspace = Path(str(task["worktree"]))
    (workspace / "app").mkdir()
    (workspace / "app" / "result.txt").write_text("done\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "app/result.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", "implement wp1"],
        check=True,
        capture_output=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result_path = Path(str(task["result_path"]))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "task_id": "task-WP-1",
                "worker": "codex",
                "model": "gpt-5.5",
                "status": "completed",
                "summary": "done",
                "workspace": str(workspace),
                "branch": task["branch"],
                "commit_hash": commit,
                "changed_files": ["app/result.txt"],
                "tests": [],
            }
        ),
        encoding="utf-8",
    )
    task["status"] = "running"
    save_tasks(files.project.runtime_directory, [task])
    process_path = files.project.runtime_directory / "processes" / "task-WP-1.json"
    process_path.parent.mkdir(parents=True, exist_ok=True)
    process_path.write_text(
        json.dumps({"pid": 0, "provider": "codex", "log_path": ""}),
        encoding="utf-8",
    )

    controller_tick(load_project_files(git_repo), launch=False, integrate=False)

    updated = load_tasks(files.project.runtime_directory)[0]
    assert updated["status"] == "review_passed"
    assert updated["package_validation"]["status"] == "passed"
    assert Path(updated["package_validation"]["evidence_path"]).exists()
    assert updated["review"]["status"] == "passed"
    assert Path(updated["review"]["evidence_path"]).exists()


def test_dead_quota_process_creates_handoff_and_reassigns(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    project_path = git_repo / ".lfg" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["project"]["integration_branch"] = "main"
    project["workers"]["providers"] = ["codex", "composer"]
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    _write_package(git_repo, preferred=["codex"])
    _approve(git_repo)
    files = load_project_files(git_repo)
    controller_tick(files, launch=False)
    task = load_tasks(files.project.runtime_directory)[0]
    task["status"] = "running"
    task["provider"] = "codex"
    save_tasks(files.project.runtime_directory, [task])
    log_path = Path(str(task["log_path"]))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("quota exceeded\n", encoding="utf-8")
    process_path = files.project.runtime_directory / "processes" / "task-WP-1.json"
    process_path.parent.mkdir(parents=True, exist_ok=True)
    process_path.write_text(
        json.dumps({"pid": 0, "provider": "codex", "log_path": str(log_path)}),
        encoding="utf-8",
    )

    controller_tick(load_project_files(git_repo), launch=False)

    updated = load_tasks(files.project.runtime_directory)[0]
    assert updated["status"] == "assigned"
    assert updated["provider"] == "composer"
    assert Path(str(updated["handoff_packet"])).exists()
