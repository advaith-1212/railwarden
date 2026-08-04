from __future__ import annotations

import json
from pathlib import Path

import yaml

from railwarden.config.init import initialize_project
from railwarden.config.loader import load_project_files
from railwarden.mcp.server import _contract_repair, _contract_repair_apply
from railwarden.planning.pipeline import (
    approve_latest_plan,
    create_pending_plan,
    parse_planner_output,
)
from railwarden.runtime.tasks import load_tasks
from railwarden.validation.package import commands_for_package

PLANNER_OUTPUT = """
{
  "plan_markdown": "# Plan\\n\\nDo the work.",
  "work_packages": [
    {
      "id": "WP-1",
      "name": "Runtime",
      "objective": "Add runtime files",
      "dependencies": [],
      "owned_paths": ["src/railwarden/runtime/"],
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
    plan = (git_repo / ".railwarden" / "plan.md").read_text(encoding="utf-8")
    assert plan.startswith("# Plan")
    packages = yaml.safe_load(
        (git_repo / ".railwarden" / "work_packages.yaml").read_text(encoding="utf-8")
    )
    assert packages["work_packages"][0]["preferred_providers"] == ["codex"]
    tasks = load_tasks(files.project.runtime_directory)
    assert tasks[0]["status"] == "planned"


def test_approve_pending_plan_writes_v2_orchestration_artifacts(
    git_repo: Path,
) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    output = """
{
  "plan_markdown": "# Plan\\n\\nDo the work.",
  "work_packages": [
    {
      "id": "WP-9",
      "name": "Risky runtime",
      "objective": "Add runtime files",
      "dependencies": [],
      "owned_paths": ["src/railwarden/runtime/"],
      "forbidden_paths": [".git/"],
      "acceptance_criteria": ["all runtime tests pass"],
      "validation_commands": [
        {"name": "runtime-tests", "command": {"cwd": ".", "argv": ["python", "-c", "print('ok')"]}}
      ],
      "preferred_providers": ["codex"],
      "model_profile": "codex:gpt-5.5?reasoning=high",
      "reviewer_profile": "antigravity:claude-opus-4.6-thinking",
      "risk_level": "high",
      "context_refs": ["context/ARCHITECTURE.md"],
      "merge_policy": "manual",
      "approval_required": true
    }
  ]
}
"""
    create_pending_plan(files.project, "implement runtime", planner_output_text=output)

    approve_latest_plan(files.project)

    packages = yaml.safe_load(
        (git_repo / ".railwarden" / "work_packages.yaml").read_text(encoding="utf-8")
    )
    assert packages["schema_version"] == "2.0.0"
    assert packages["work_packages"][0]["risk_level"] == "high"
    assert (git_repo / ".railwarden" / "contract_freeze_manifest.yaml").exists()
    assert (git_repo / ".railwarden" / "model_assignment.yaml").exists()
    assert "WP-9" in (git_repo / ".railwarden" / "dependency_graph.mmd").read_text(
        encoding="utf-8"
    )
    assert "src/railwarden/runtime" in (
        git_repo / ".railwarden" / "ownership_matrix.csv"
    ).read_text(encoding="utf-8")
    assert (git_repo / ".railwarden" / "agent_prompts" / "wp-9.md").exists()


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
    plan = (git_repo / ".railwarden" / "plan.md").read_text(encoding="utf-8")
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
      - src/railwarden/runtime/
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


def test_approve_pending_plan_normalizes_repo_paths(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    (git_repo / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    (git_repo / "styles.css").write_text("body {}\n", encoding="utf-8")
    (git_repo / "src").mkdir(exist_ok=True)
    (git_repo / "src" / "app.js").write_text("console.log('ok')\n", encoding="utf-8")
    files = load_project_files(git_repo)
    output = """
{
  "plan_markdown": "# Plan\\n\\nDo the work.",
  "work_packages": [
    {
      "id": "WP-2",
      "name": "UI and state",
      "objective": "Update clock and reminders",
      "owned_paths": ["index.html", "style.css", "app.js"],
      "forbidden_paths": ["./style.css"],
      "acceptance_tests": []
    }
  ]
}
"""
    create_pending_plan(files.project, "extend calendar", planner_output_text=output)

    approved = approve_latest_plan(files.project)
    package = approved["work_packages"][0]

    assert package["owned_paths"] == ["index.html", "styles.css", "src/app.js"]
    assert package["forbidden_paths"] == ["styles.css"]


def test_contract_repair_works_on_pending_plan(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    create_pending_plan(
        files.project,
        "build expense tracker",
        planner_output_text=PLANNER_OUTPUT,
    )

    recorded = _contract_repair(
        git_repo,
        {
            "package_id": "WP-1",
            "patch": {"objective": "Repaired objective"},
        },
    )
    assert recorded["status"] == "recorded"

    applied = _contract_repair_apply(git_repo, {"package_id": "WP-1"})
    assert applied["status"] == "applied"
    assert applied["pending"] is True

    pending = json.loads(
        (files.project.runtime_directory / "state" / "pending-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert pending["work_packages"][0]["objective"] == "Repaired objective"


def test_approve_pending_plan_sanitizes_future_context_refs(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    output = """
{
  "plan_markdown": "# Plan\\n\\nBuild expense tracker.",
  "work_packages": [
    {
      "id": "WP-001",
      "name": "Scaffold",
      "objective": "Initialize project",
      "dependencies": [],
      "owned_paths": ["src/constants.js"],
      "context_refs": ["context/ARCHITECTURE.md"]
    },
    {
      "id": "WP-004",
      "name": "Features",
      "objective": "Add expense features",
      "dependencies": ["WP-001"],
      "owned_paths": ["src/app.js"],
      "context_refs": ["src/constants.js", "context/TEST_STRATEGY.md"]
    }
  ]
}
"""
    create_pending_plan(
        files.project, "build expense tracker", planner_output_text=output
    )

    approved = approve_latest_plan(files.project)
    packages = {item["id"]: item for item in approved["work_packages"]}

    assert packages["WP-001"]["context_refs"] == ["context/ARCHITECTURE.md"]
    assert packages["WP-004"]["context_refs"] == ["context/TEST_STRATEGY.md"]
    assert "src/constants.js" not in packages["WP-004"]["context_refs"]


def test_commands_for_package_ignores_prose_acceptance_tests() -> None:
    from railwarden.config.models import WorkPackage

    prose = WorkPackage(
        package_id="WP-2",
        name="Clock",
        objective="",
        owned_paths=("index.html",),
        acceptance_tests=(
            "Clock element is visible in the topbar on page load",
            "node --check src/app.js",
        ),
    )

    commands = commands_for_package(prose)

    assert [command.name for command in commands] == ["WP-2-acceptance-2"]
    assert commands[0].argv == ("node", "--check", "src/app.js")
