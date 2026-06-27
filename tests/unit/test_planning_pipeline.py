from __future__ import annotations

from pathlib import Path

import yaml

from lfg.config.init import initialize_project
from lfg.config.loader import load_project_files
from lfg.planning.pipeline import (
    approve_latest_plan,
    create_pending_plan,
    parse_planner_output,
)
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

HUMAN_PLAN_OUTPUT = """
# FFT Visualizer Plan

Build a browser-based FFT visualizer with a small audio input pipeline.

## Work Packages

1. Add FFT rendering surface and controls.
2. Wire microphone/audio file input.
3. Add validation and smoke checks.
"""

STRUCTURED_OUTPUT = """
{
  "plan_markdown": "# FFT Visualizer Plan\\n\\nBuild a browser-based FFT visualizer.",
  "work_packages": [
    {
      "id": "WP-1",
      "name": "Visualizer UI",
      "objective": "Add FFT rendering surface and controls",
      "dependencies": [],
      "owned_paths": ["src/ui/"],
      "forbidden_paths": ["infra/"],
      "acceptance_tests": ["npm test"],
      "preferred_providers": ["codex"]
    },
    {
      "id": "WP-2",
      "name": "Audio Input",
      "objective": "Wire microphone and audio file input",
      "dependencies": ["WP-1"],
      "owned_paths": ["src/audio/"],
      "forbidden_paths": ["infra/"],
      "acceptance_tests": ["npm test"],
      "preferred_providers": ["antigravity"]
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


def test_create_pending_plan_from_human_markdown_with_structuring_pass(
    git_repo: Path,
) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)

    pending = create_pending_plan(
        files.project,
        "build fft visualizer",
        planner_output_text=HUMAN_PLAN_OUTPUT,
        planner_structured_output_text=STRUCTURED_OUTPUT,
    )

    assert pending.plan_markdown.startswith("# FFT Visualizer Plan")
    assert [package["id"] for package in pending.work_packages] == ["WP-1", "WP-2"]
    assert pending.planner_output["used_structuring_pass"] is True

    approve_latest_plan(files.project)
    plan = (git_repo / ".lfg" / "plan.md").read_text(encoding="utf-8")
    assert plan.startswith("# FFT Visualizer Plan")


def test_parse_planner_output_from_yaml_code_block() -> None:
    payload = parse_planner_output(
        """
Planner summary for Hermes.

```yaml
plan_markdown: |
  # YAML Plan

  Do the work.
work_packages:
  - id: WP-1
    name: Runtime
    objective: Add runtime files
    dependencies: []
    owned_paths:
      - src/lfg/runtime/
    forbidden_paths:
      - .git/
    acceptance_tests:
      - pytest
    preferred_providers:
      - codex
```
"""
    )

    assert payload.plan_markdown.startswith("# YAML Plan")
    assert payload.work_packages[0]["id"] == "WP-1"
