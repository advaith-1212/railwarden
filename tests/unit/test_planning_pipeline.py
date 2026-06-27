from __future__ import annotations

from pathlib import Path

import yaml

from lfg.config.init import initialize_project
from lfg.config.loader import load_project_files
from lfg.planning.pipeline import approve_latest_plan, create_pending_plan
from lfg.runtime.tasks import load_tasks

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
      "forbidden_paths": [".git/"],
      "acceptance_tests": ["pytest"],
      "preferred_providers": ["codex"]
    }
  ]
}
"""


def test_create_and_approve_pending_plan(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    pending = create_pending_plan(
        files.project,
        "implement runtime",
        planner_output_text=PLANNER_OUTPUT,
    )
    assert pending.run_id
    assert (files.project.runtime_directory / "state" / "pending-plan.json").exists()

    approved = approve_latest_plan(files.project)

    assert approved["approved"] is True
    plan = (git_repo / ".lfg" / "plan.md").read_text(encoding="utf-8")
    assert plan.startswith("# Plan")
    packages = yaml.safe_load(
        (git_repo / ".lfg" / "work_packages.yaml").read_text(encoding="utf-8")
    )
    assert packages["work_packages"][0]["preferred_providers"] == ["codex"]
    tasks = load_tasks(files.project.runtime_directory)
    assert tasks[0]["status"] == "planned"
