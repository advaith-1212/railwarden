from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from railwarden.config.loader import load_project_files
from railwarden.engine.controller import controller_tick
from railwarden.planning.jobs import planning_status, start_planning_job
from railwarden.planning.pipeline import approve_latest_plan
from railwarden.providers.adapters import ProviderAdapter
from railwarden.runtime.context import context_status
from tests.conftest import initialize_populated_project


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=True,
        capture_output=True,
    )


def test_reading_tracker_factory_context_gate_and_planning_job(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_populated_project(git_repo)
    goal = (
        "Build a production-quality personal reading tracker web application "
        "with CRUD, search, filters, and simple statistics."
    )
    fixture = git_repo / "fixtures" / "reading-tracker-plan.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(
        json.dumps(
            {
                "plan_markdown": "# Reading Tracker Plan\n\nScaffold and ship.",
                "work_packages": [
                    {
                        "id": "WP-001",
                        "name": "Scaffold",
                        "objective": "Create app scaffold and package metadata",
                        "owned_paths": ["package.json", "public/", "src/"],
                        "dependencies": [],
                        "context_refs": [
                            "context/ARCHITECTURE.md",
                            "context/TEST_STRATEGY.md",
                        ],
                        "preferred_providers": ["codex"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("RAILWARDEN_PLANNER_OUTPUT", str(fixture))
    files = load_project_files(git_repo)
    assert context_status(files.project, files.packages)["status"] == "ok"

    job = start_planning_job(files.project, goal=goal, replace=True)
    assert job["status"] == "planning"
    deadline = time.time() + 5
    status = {"status": "planning"}
    while time.time() < deadline:
        status = planning_status(files.project, run_id=str(job["run_id"]))
        if status.get("status") in {"ready", "failed"}:
            break
        time.sleep(0.1)
    assert status.get("status") == "ready"

    approve_latest_plan(files.project)
    _commit(git_repo, "freeze reading tracker contracts")

    packages = yaml.safe_load(
        (git_repo / ".railwarden" / "work_packages.yaml").read_text(encoding="utf-8")
    )
    assert packages["work_packages"][0]["id"] == "WP-001"
    assert packages["work_packages"][0]["context_refs"]

    result = controller_tick(
        load_project_files(git_repo),
        adapters={"codex": ProviderAdapter("codex", "python", "test-model")},
        launch=False,
        integrate=False,
    )
    assert result["status"] == "ok"
    assert result["launched"]
