from __future__ import annotations

from lfg.validation.commands import (
    planner_validation_argv,
    sanitize_planner_validation_commands,
)


def test_planner_validation_argv_splits_compound_shell_command() -> None:
    argv = planner_validation_argv(
        "npm start & sleep 2 && curl -s http://localhost:3000/health && kill %1"
    )
    assert argv == ("npm", "start")


def test_sanitize_planner_validation_commands_dedupes_and_structures() -> None:
    sanitized = sanitize_planner_validation_commands(
        [
            "npm install",
            "npm start & sleep 2 && curl -s http://localhost:3000/health",
            ["npm", "test"],
        ]
    )
    assert len(sanitized) == 3
    assert sanitized[0]["command"]["argv"] == ["npm", "install"]
    assert sanitized[1]["command"]["argv"] == ["npm", "start"]
    assert sanitized[2]["command"]["argv"] == ["npm", "test"]