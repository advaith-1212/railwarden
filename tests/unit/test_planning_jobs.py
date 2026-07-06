from __future__ import annotations

import time
from pathlib import Path

import pytest

from lfg.config.init import initialize_project
from lfg.config.loader import load_project_files
from lfg.planning.jobs import planning_status, start_planning_job

PLANNER_OUTPUT = """
{
  "plan_markdown": "# Plan\\n\\nDo the work.",
  "work_packages": [
    {
      "id": "WP-1",
      "name": "Runtime",
      "objective": "Add runtime files",
      "dependencies": [],
      "owned_paths": ["src/lfg/runtime/"],
      "context_refs": ["context/ARCHITECTURE.md"]
    }
  ]
}
"""


def test_goal_submit_returns_planning_status_immediately(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    fixture = git_repo / "planner-fixture.json"
    fixture.write_text(PLANNER_OUTPUT, encoding="utf-8")
    monkeypatch.setenv("LFG_PLANNER_OUTPUT", str(fixture))
    started = start_planning_job(files.project, "build reading tracker")
    assert started["status"] == "planning"
    assert started["run_id"]

    deadline = time.time() + 5
    final = {"status": "planning"}
    while time.time() < deadline:
        final = planning_status(files.project, run_id=str(started["run_id"]))
        if final.get("status") in {"ready", "failed"}:
            break
        time.sleep(0.1)
    assert final.get("status") == "ready"