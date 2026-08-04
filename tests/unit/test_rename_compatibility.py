from __future__ import annotations

from pathlib import Path

import pytest

import railwarden
from railwarden.cli.main import build_parser, legacy_main, main
from railwarden.compat import environment_value, project_config_directory
from railwarden.config.loader import load_project_config
from railwarden.runtime.results import worker_result_path


def _write_project_config(root: Path, directory: str, name: str) -> None:
    config_dir = root / directory
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "project.yaml").write_text(
        f"""project:
  name: {name}
  worktree_root: auto
runtime:
  directory: auto
""",
        encoding="utf-8",
    )


def test_import_railwarden_and_version_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert railwarden.__version__
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == railwarden.__version__


def test_warden_help_and_core_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert help_text.startswith("usage: warden")
    parser = build_parser()
    assert parser.parse_args(["setup"]).command == "setup"
    assert parser.parse_args(["launch", "--no-attach"]).command == "launch"


def test_legacy_lfg_cli_warns_and_reuses_warden_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        legacy_main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "usage: warden" in captured.out
    assert captured.err.strip() == (
        "The 'lfg' command is deprecated; use 'warden' instead."
    )


def test_legacy_config_and_derived_state_fallback(tmp_path: Path) -> None:
    _write_project_config(tmp_path, ".lfg", "legacy-project")
    (tmp_path / ".lfg-runtime").mkdir()
    (tmp_path / ".lfg-worktrees").mkdir()

    config = load_project_config(tmp_path)

    assert project_config_directory(tmp_path) == tmp_path / ".lfg"
    assert config.name == "legacy-project"
    assert config.runtime_directory == tmp_path / ".lfg-runtime"
    assert config.worktree_root == tmp_path / ".lfg-worktrees"


def test_current_config_and_state_take_precedence(tmp_path: Path) -> None:
    _write_project_config(tmp_path, ".lfg", "legacy-project")
    _write_project_config(tmp_path, ".railwarden", "current-project")
    for name in (
        ".lfg-runtime",
        ".railwarden-runtime",
        ".lfg-worktrees",
        ".railwarden-worktrees",
    ):
        (tmp_path / name).mkdir()

    config = load_project_config(tmp_path)

    assert project_config_directory(tmp_path) == tmp_path / ".railwarden"
    assert config.name == "current-project"
    assert config.runtime_directory == tmp_path / ".railwarden-runtime"
    assert config.worktree_root == tmp_path / ".railwarden-worktrees"


def test_result_state_uses_legacy_only_when_current_is_absent(tmp_path: Path) -> None:
    legacy = tmp_path / ".lfg-results"
    legacy.mkdir()
    assert worker_result_path(tmp_path, "task-1") == legacy / "task-1.json"

    current = tmp_path / ".railwarden-results"
    current.mkdir()
    assert worker_result_path(tmp_path, "task-1") == current / "task-1.json"


def test_environment_uses_legacy_fallback_and_current_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAILWARDEN_PLANNER_OUTPUT", raising=False)
    monkeypatch.setenv("LFG_PLANNER_OUTPUT", "legacy.json")
    assert environment_value("RAILWARDEN_PLANNER_OUTPUT") == "legacy.json"

    monkeypatch.setenv("RAILWARDEN_PLANNER_OUTPUT", "current.json")
    assert environment_value("RAILWARDEN_PLANNER_OUTPUT") == "current.json"
