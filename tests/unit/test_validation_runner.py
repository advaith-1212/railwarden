from __future__ import annotations

from pathlib import Path

import pytest

from railwarden.config.models import ValidationCommand
from railwarden.errors import ValidationError
from railwarden.validation.runner import resolve_cwd, run_validation_suite


def test_command_working_directory_behavior(git_repo: Path) -> None:
    sub = git_repo / "sub"
    sub.mkdir()
    command = ValidationCommand(
        "pwd",
        Path("sub"),
        ("python", "-c", "import os; assert os.path.basename(os.getcwd()) == 'sub'"),
    )
    status, results, _removed = run_validation_suite(git_repo, (command,))
    assert status == "passed"
    assert results[0].cwd == "sub"


def test_cwd_escape_rejected(git_repo: Path) -> None:
    with pytest.raises(ValidationError):
        resolve_cwd(git_repo, Path(".."))


def test_generated_artifact_cleanup(git_repo: Path) -> None:
    command = ValidationCommand(
        "gen",
        Path(),
        (
            "python",
            "-c",
            "from pathlib import Path; Path('build.out').write_text('x')",
        ),
        generated_outputs=(Path("build.out"),),
    )
    status, _results, removed = run_validation_suite(git_repo, (command,))
    assert status == "passed"
    assert removed == ["build.out"]
    assert not (git_repo / "build.out").exists()
