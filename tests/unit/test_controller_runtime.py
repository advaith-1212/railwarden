from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from railwarden.config.init import initialize_project
from railwarden.config.loader import load_project_files
from railwarden.engine.controller import controller_tick
from railwarden.engine.dashboard import render_dashboard
from railwarden.providers.adapters import ProviderAdapter
from railwarden.providers.health import classify_failure
from railwarden.runtime.tasks import load_tasks, save_tasks
from tests.conftest import initialize_populated_project


def _write_package(repo: Path, *, preferred: list[str] | None = None) -> None:
    (repo / ".railwarden" / "work_packages.yaml").write_text(
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
                        "context_refs": [
                            "context/ARCHITECTURE.md",
                            "context/TEST_STRATEGY.md",
                        ],
                        "preferred_providers": preferred or [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _approve(repo: Path) -> None:
    runtime = repo / ".railwarden-runtime"
    (runtime / "state").mkdir(parents=True, exist_ok=True)
    (runtime / "state" / "pending-plan.json").write_text(
        json.dumps({"approved": True, "goal": "ship", "run_id": "test"}),
        encoding="utf-8",
    )


def test_setup_initializes_fresh_repo_baseline(tmp_path: Path) -> None:
    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True)

    initialize_project(repo, yes=True)

    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    integration = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "integration/railwarden"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    assert head == integration
    assert (repo / ".railwarden" / "project.yaml").exists()
    assert (repo / "context" / "PROJECT_CONTEXT.md").exists()


def test_controller_waits_for_plan_approval(git_repo: Path) -> None:
    initialize_populated_project(git_repo)
    _write_package(git_repo)
    files = load_project_files(git_repo)

    result = controller_tick(files, launch=False)

    assert result["status"] == "waiting_for_plan_approval"


def test_controller_assigns_ready_task_by_provider_priority(git_repo: Path) -> None:
    initialize_populated_project(git_repo)
    project_path = git_repo / ".railwarden" / "project.yaml"
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
    assert task["status"] == "ready"
    assert task["provider"] == "codex"
    assert ".railwarden-results" in task["result_path"]
    runtime_result = Path(task["runtime_result_path"])
    assert ".railwarden-runtime" in runtime_result.parts
    assert "results" in runtime_result.parts


def test_dashboard_renders_runtime_state(git_repo: Path) -> None:
    initialize_populated_project(git_repo)
    _write_package(git_repo)
    _approve(git_repo)
    files = load_project_files(git_repo)
    controller_tick(files, launch=False)

    dashboard = render_dashboard(load_project_files(git_repo))

    assert "RailWarden Dashboard" in dashboard
    assert "WP-1" in dashboard
    assert "Providers" in dashboard


def test_controller_runs_railwarden_validation_and_review_before_merge(
    git_repo: Path,
) -> None:
    initialize_populated_project(git_repo)
    project_path = git_repo / ".railwarden" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["project"]["integration_branch"] = "main"
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    (git_repo / ".railwarden" / "work_packages.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "2.0.0",
                "work_packages": [
                    {
                        "id": "WP-1",
                        "name": "Validated package",
                        "objective": "Do work",
                        "owned_paths": ["app"],
                        "context_refs": [
                            "context/ARCHITECTURE.md",
                            "context/TEST_STRATEGY.md",
                        ],
                        "preferred_providers": ["codex"],
                        "validation_commands": [
                            {
                                "name": "package-check",
                                "command": {
                                    "cwd": ".",
                                    "argv": ["python", "-c", "print('ok')"],
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
                "schema_version": "1.0.0",
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


def test_result_path_sandbox_failure_is_not_auth() -> None:
    kind, transient, requires_human, pattern = classify_failure(
        "Codex could not write output-last-message to "
        "/repo/.railwarden-runtime/results/task.json because the sandbox blocked "
        "the result JSON path. invalid access token from unrelated MCP server"
    )

    assert kind == "result_path_unwritable"
    assert transient is False
    assert requires_human is False
    assert pattern == "result json sandbox"


def test_controller_recovers_from_stale_provider_auth_state(git_repo: Path) -> None:
    initialize_populated_project(git_repo)
    project_path = git_repo / ".railwarden" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["project"]["integration_branch"] = "main"
    project["workers"]["providers"] = ["codex", "composer"]
    project["providers"]["codex"]["priority"] = 10
    project["providers"]["composer"]["priority"] = 30
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    _write_package(git_repo)
    _approve(git_repo)
    files = load_project_files(git_repo)
    provider_path = files.project.runtime_directory / "provider-health" / "codex.json"
    provider_path.parent.mkdir(parents=True, exist_ok=True)
    provider_path.write_text(
        json.dumps(
            {
                "name": "codex",
                "status": "needs_auth",
                "failure_kind": "authentication",
                "failure_count": 1,
            }
        ),
        encoding="utf-8",
    )

    class HealthyAdapter(ProviderAdapter):
        def health_check(self) -> dict[str, object]:
            return {"name": self.name, "status": "healthy", "model": self.model}

    adapters: dict[str, ProviderAdapter] = {
        "codex": HealthyAdapter("codex", "codex", "gpt-5.5", "high"),
        "composer": HealthyAdapter("composer", "grok", "grok-composer-2.5-fast"),
    }

    result = controller_tick(files, adapters=adapters, launch=False)

    assert result["launched"] == [{"task_id": "task-WP-1", "provider": "codex"}]


def test_dead_quota_process_creates_handoff_and_reassigns(git_repo: Path) -> None:
    initialize_populated_project(git_repo)
    project_path = git_repo / ".railwarden" / "project.yaml"
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
    assert updated["status"] == "ready"
    assert updated["provider"] == "composer"
    assert Path(str(updated["handoff_packet"])).exists()
