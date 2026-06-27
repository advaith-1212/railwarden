from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from lfg.config.models import ValidationCommand
from lfg.errors import ValidationError
from lfg.git import tracked_is_clean, untracked_files


@dataclass(frozen=True)
class ValidationResult:
    name: str
    command: tuple[str, ...]
    cwd: str
    required: bool
    status: str
    returncode: int | None
    duration_seconds: float
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve_cwd(repository: Path, cwd: Path) -> Path:
    target = (repository / cwd).resolve()
    try:
        target.relative_to(repository.resolve())
    except ValueError:
        raise ValidationError(f"Validation cwd escapes repository: {cwd}")
    if not target.is_dir():
        raise ValidationError(f"Validation cwd does not exist: {target}")
    return target


def cleanup_new_untracked(
    repository: Path, before: set[str], generated_outputs: tuple[Path, ...]
) -> list[str]:
    after = untracked_files(repository)
    new_files = sorted(after - before)
    removable: set[str] = set()
    declared = tuple(
        str(path).replace("\\", "/").rstrip("/") for path in generated_outputs
    )
    for item in new_files:
        if not declared or any(
            item == root or item.startswith(root + "/") for root in declared
        ):
            removable.add(item)
    removed: list[str] = []
    for item in sorted(removable, key=len, reverse=True):
        path = repository / item
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed.append(item)
        elif path.is_dir():
            try:
                path.rmdir()
                removed.append(item)
            except OSError:
                pass
    return removed


def run_validation_command(
    repository: Path, command: ValidationCommand
) -> ValidationResult:
    cwd = resolve_cwd(repository, command.cwd)
    started = time.monotonic()
    completed = subprocess.run(
        list(command.argv),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    duration = time.monotonic() - started
    return ValidationResult(
        name=command.name,
        command=command.argv,
        cwd=str(command.cwd),
        required=command.required,
        status="passed" if completed.returncode == 0 else "failed",
        returncode=completed.returncode,
        duration_seconds=round(duration, 3),
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_validation_suite(
    repository: Path, commands: tuple[ValidationCommand, ...]
) -> tuple[str, list[ValidationResult], list[str]]:
    if not tracked_is_clean(repository):
        raise ValidationError("Repository has tracked changes before validation")
    before_untracked = untracked_files(repository)
    generated_outputs = tuple(
        path for command in commands for path in command.generated_outputs
    )
    results: list[ValidationResult] = []
    with tempfile.TemporaryDirectory(prefix="lfg-validation-") as tmp:
        _ = tmp
        for command in commands:
            result = run_validation_command(repository, command)
            results.append(result)
            if result.required and result.status != "passed":
                break
    removed = cleanup_new_untracked(repository, before_untracked, generated_outputs)
    failed = [
        result for result in results if result.required and result.status != "passed"
    ]
    status = "passed" if not failed else "failed"
    if status == "passed" and not tracked_is_clean(repository):
        raise ValidationError("Validation left tracked repository changes")
    return status, results, removed
