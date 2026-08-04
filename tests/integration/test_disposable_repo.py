from __future__ import annotations

import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import yaml

from railwarden.config.init import initialize_project
from railwarden.config.loader import load_project_files
from railwarden.config.models import ValidationCommand, WorkPackage
from railwarden.integration.manager import integrate_one
from railwarden.provisioning.worktrees import ensure_worktree
from railwarden.scheduler.classifier import classify_packages, execution_plan


def run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


def test_end_to_end_disposable_repo(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    project_path = git_repo / ".railwarden" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["project"]["integration_branch"] = "main"
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    packages = {
        "schema_version": "1.0.0",
        "work_packages": [
            {
                "id": "WP-1",
                "name": "One",
                "objective": "Edit app",
                "owned_paths": ["app/"],
                "dependencies": [],
            }
        ],
    }
    (git_repo / ".railwarden" / "work_packages.yaml").write_text(
        yaml.safe_dump(packages), encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "configure integration test"],
        check=True,
        capture_output=True,
    )
    files = load_project_files(git_repo)
    pkg = files.packages["WP-1"]
    result = ensure_worktree(
        repository=git_repo,
        integration_branch="main",
        workspace=files.project.worktree_root / "wp1",
        branch="railwarden/WP-1",
        action="execute",
    )
    wt = Path(str(result["workspace"]))
    (wt / "app").mkdir()
    (wt / "app" / "x.txt").write_text("x\n", encoding="utf-8")
    run(wt, "add", ".")
    run(wt, "commit", "-m", "WP-1")
    states = classify_packages(files.project, {"WP-1": pkg})
    plan = execution_plan(files.project, states)
    queue = cast(list[dict[str, Any]], plan["integration_queue"])
    assert isinstance(queue, list) and queue
    command = ValidationCommand("ok", Path(), ("python", "-c", "print('ok')"))
    merged = integrate_one(
        config=files.project,
        candidate=queue[0],
        validation_commands=(command,),
        execute=True,
    )
    assert merged["status"] == "merged_and_validated"


def test_failed_validation_rolls_back_and_preserves_untracked(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    project_path = git_repo / ".railwarden" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["project"]["integration_branch"] = "main"
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")
    (git_repo / "keep.tmp").write_text("keep", encoding="utf-8")
    files = load_project_files(git_repo)
    pkg = WorkPackage("WP-2", "Two", "", owned_paths=("app/",))
    result = ensure_worktree(
        repository=git_repo,
        integration_branch="main",
        workspace=files.project.worktree_root / "wp2",
        branch="railwarden/WP-2",
        action="execute",
    )
    wt = Path(str(result["workspace"]))
    (wt / "app").mkdir()
    (wt / "app" / "y.txt").write_text("y\n", encoding="utf-8")
    run(wt, "add", ".")
    run(wt, "commit", "-m", "WP-2")
    states = classify_packages(files.project, {"WP-2": pkg})
    queue = cast(
        list[dict[str, Any]],
        execution_plan(files.project, states)["integration_queue"],
    )
    command = ValidationCommand(
        "fail",
        Path(),
        (
            "python",
            "-c",
            "from pathlib import Path; Path('new.tmp').write_text('x'); raise SystemExit(1)",
        ),
        generated_outputs=(Path("new.tmp"),),
    )
    with suppress(Exception):
        integrate_one(
            config=files.project,
            candidate=queue[0],
            validation_commands=(command,),
            execute=True,
        )
    assert (git_repo / "keep.tmp").exists()
    assert not (git_repo / "new.tmp").exists()
    assert run(git_repo, "rev-parse", "HEAD") == run(git_repo, "rev-parse", "main")
