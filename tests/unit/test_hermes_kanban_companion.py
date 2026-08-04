from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

import railwarden.cli.main as cli_main
from railwarden.cli.main import main
from railwarden.config.init import initialize_project
from railwarden.config.loader import load_project_config, load_project_files
from railwarden.errors import RailWardenError
from railwarden.hermes.kanban import (
    HermesAdapter,
    apply_import_plan,
    build_import_plan,
)


def _write_packages(repo: Path) -> None:
    (repo / ".railwarden" / "work_packages.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0.0",
                "work_packages": [
                    {
                        "id": "WP-1",
                        "name": "Core",
                        "objective": "Build core behavior",
                        "owned_paths": ["src/core.py"],
                        "forbidden_paths": ["contracts/"],
                        "acceptance_criteria": ["deterministic output"],
                        "validation_commands": [
                            {
                                "name": "unit",
                                "command": {"cwd": ".", "argv": ["pytest", "tests"]},
                            }
                        ],
                        "preferred_providers": ["codex"],
                        "context_refs": ["skill:repo-rules"],
                    },
                    {
                        "id": "WP-2",
                        "name": "API",
                        "objective": "Expose API",
                        "dependencies": ["WP-1"],
                        "owned_paths": ["src/api.py"],
                        "preferred_providers": ["composer"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_config_loads_hermes_kanban_defaults(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)

    config = load_project_config(git_repo)

    assert config.hermes_board == "railwarden-repo"
    assert config.hermes_project_slug == "repo"
    assert config.hermes_default_assignee == "default"
    assert config.hermes_profile_map == {}
    assert config.hermes_workspace_mode == "worktree"


def test_work_package_conversion_includes_contract_and_dependencies(
    git_repo: Path,
) -> None:
    initialize_project(git_repo, yes=True)
    project_path = git_repo / ".railwarden" / "project.yaml"
    payload = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    payload["hermes"]["profile_map"] = {"codex": "builder", "composer": "uiworker"}
    project_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    _write_packages(git_repo)

    plan = build_import_plan(load_project_files(git_repo))

    assert plan.board == "railwarden-repo"
    assert [task.package_id for task in plan.tasks] == ["WP-1", "WP-2"]
    first = plan.tasks[0]
    assert first.assignee == "builder"
    assert first.idempotency_key == "railwarden:repo:WP-1"
    assert first.workspace == "worktree"
    assert first.branch == "railwarden/WP-1"
    assert first.skills == ("repo-rules",)
    assert "Owned paths:\n- src/core.py" in first.body
    assert "Forbidden paths:\n- contracts/" in first.body
    assert "Acceptance criteria:\n- deterministic output" in first.body
    assert "Validation commands:\n- unit: (cd .; pytest tests)" in first.body
    assert [link.to_dict() for link in plan.links] == [
        {"parent_package_id": "WP-1", "child_package_id": "WP-2"}
    ]


def test_hermes_adapter_json_reports_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="not-json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RailWardenError, match="did not return JSON"):
        HermesAdapter("hermes").json(["kanban", "create", "x", "--json"])


def test_apply_import_plan_uses_hermes_create_and_link(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    _write_packages(git_repo)
    plan = build_import_plan(load_project_files(git_repo))

    class FakeAdapter(HermesAdapter):
        def __init__(self) -> None:
            self.commands: list[list[str]] = []
            self.created = 0

        def json(
            self,
            args: list[str],
            *,
            check: bool = True,
            timeout: float = 30.0,
        ) -> dict[str, Any]:
            _ = (check, timeout)
            self.commands.append(args)
            self.created += 1
            return {"id": f"t{self.created}"}

        def run(
            self,
            args: list[str],
            *,
            check: bool = True,
            timeout: float = 30.0,
        ) -> Any:
            _ = (check, timeout)
            self.commands.append(args)
            return None

    adapter = FakeAdapter()

    result = apply_import_plan(plan, adapter)

    assert result == {
        "created": [
            {"package_id": "WP-1", "task_id": "t1"},
            {"package_id": "WP-2", "task_id": "t2"},
        ],
        "linked": [{"parent": "t1", "child": "t2"}],
    }
    assert adapter.commands[0][:4] == ["kanban", "--board", "railwarden-repo", "create"]
    assert "--idempotency-key" in adapter.commands[0]
    assert adapter.commands[-1] == [
        "kanban",
        "--board",
        "railwarden-repo",
        "link",
        "t1",
        "t2",
    ]


def test_cli_hermes_status_json_uses_status_payload(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initialize_project(git_repo, yes=True)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(
        cli_main,
        "hermes_status",
        lambda _config, _adapter: {
            "version": "Hermes Agent v1\nUpdate available: yes",
            "update_available": True,
            "current_board": "railwarden-repo",
            "project_slug": "repo",
        },
    )

    assert main(["hermes", "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["current_board"] == "railwarden-repo"
    assert payload["update_available"] is True


def test_cli_hermes_bootstrap_dry_run_does_not_call_adapter(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initialize_project(git_repo, yes=True)
    monkeypatch.chdir(git_repo)

    class FailingAdapter:
        def __init__(self) -> None:
            raise AssertionError("dry-run should not instantiate HermesAdapter")

    monkeypatch.setattr(cli_main, "HermesAdapter", FailingAdapter)

    assert main(["hermes", "bootstrap", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["board"] == "railwarden-repo"
    assert "ensure Hermes Kanban board `railwarden-repo`" in payload["actions"]


def test_cli_hermes_import_dry_run_json(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initialize_project(git_repo, yes=True)
    _write_packages(git_repo)
    monkeypatch.chdir(git_repo)

    assert main(["hermes", "import", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [task["package_id"] for task in payload["tasks"]] == ["WP-1", "WP-2"]
    assert payload["links"] == [
        {"parent_package_id": "WP-1", "child_package_id": "WP-2"}
    ]
