from __future__ import annotations

import shutil
from pathlib import Path

from lfg.config.init import initialize_project
from lfg.config.loader import load_project_config
from lfg.planning.antigravity import DEFAULT_PLANNER_MODEL, AntigravityClaudePlanner
from lfg.providers.adapters import default_adapters


def test_default_project_uses_antigravity_claude_planner(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    config = load_project_config(git_repo)
    assert config.planner_provider == "antigravity"
    assert config.planner_model == DEFAULT_PLANNER_MODEL


def test_provider_model_assignments() -> None:
    adapters = default_adapters()
    assert adapters["codex"].model == "gpt-5.5"
    assert adapters["codex"].reasoning_effort == "high"
    assert adapters["antigravity"].model == "Gemini 3.1 Pro (High)"
    assert adapters["composer"].model == "grok-composer-2.5-fast"


def test_antigravity_planner_command_shape() -> None:
    planner = AntigravityClaudePlanner()
    assert planner.model == "Claude Opus 4.6 (Thinking)"


def test_antigravity_command_does_not_probe_doctor(monkeypatch) -> None:
    planner = AntigravityClaudePlanner()

    def fail_doctor() -> object:
        raise AssertionError("doctor() should not be called during command creation")

    monkeypatch.setattr(planner, "doctor", fail_doctor)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/agy")

    command = planner.command(
        repository=Path("/tmp/repo"),
        prompt="plan the work",
    )

    assert command[:2] == ["/usr/local/bin/agy", "--model"]
    assert "--print" in command
