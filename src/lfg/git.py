from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from lfg.errors import GitError


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def run_git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> CommandResult:
    command = ("git", "-C", str(repository), *arguments)
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    result = CommandResult(
        args=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and result.returncode != 0:
        raise GitError(
            f"Git command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def output(repository: Path, *arguments: str) -> str:
    return run_git(repository, *arguments).stdout.strip()


def discover_repo(start: Path) -> Path:
    result = run_git(start, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise GitError(f"Not inside a Git repository: {start}")
    return Path(result.stdout.strip()).resolve()


def current_branch(repository: Path) -> str:
    return output(repository, "branch", "--show-current")


def head(repository: Path, revision: str = "HEAD") -> str:
    return output(repository, "rev-parse", revision)


def short_head(repository: Path, revision: str = "HEAD") -> str:
    return output(repository, "rev-parse", "--short", revision)


def is_clean(repository: Path) -> bool:
    return output(repository, "status", "--porcelain") == ""


def tracked_is_clean(repository: Path) -> bool:
    return output(repository, "status", "--porcelain", "--untracked-files=no") == ""


def status_porcelain(repository: Path) -> str:
    return output(repository, "status", "--porcelain")


def branch_exists(repository: Path, branch: str) -> bool:
    return (
        run_git(
            repository,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            check=False,
        ).returncode
        == 0
    )


def branch_is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    return (
        run_git(
            repository,
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
            check=False,
        ).returncode
        == 0
    )


def changed_files_in_commit(repository: Path, commit: str) -> list[str]:
    text = output(
        repository,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        commit,
    )
    return [line for line in text.splitlines() if line]


def untracked_files(repository: Path) -> set[str]:
    text = output(repository, "ls-files", "--others", "--exclude-standard")
    return {line for line in text.splitlines() if line}


def worktree_entries(repository: Path) -> list[dict[str, str]]:
    text = output(repository, "worktree", "list", "--porcelain")
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries
