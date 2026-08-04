from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from railwarden.provisioning.worktrees import ensure_worktree


def run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "-C", str(tmp_path), "init"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "railwarden@test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "RailWarden"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "README.md").write_text("repo\n", encoding="utf-8")
    run(tmp_path, "add", ".")
    run(tmp_path, "commit", "-m", "init")
    branch = run(tmp_path, "branch", "--show-current")
    if branch != "main":
        run(tmp_path, "branch", "-M", "main")
    return tmp_path


def test_ensure_worktree_removes_broken_orphan(repo: Path) -> None:
    workspace = repo / ".railwarden-worktrees" / "wp001"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text("orphan\n", encoding="utf-8")
    (workspace / ".git").write_text(
        f"gitdir: {repo / '.git' / 'worktrees' / 'wp001'}\n",
        encoding="utf-8",
    )

    result = ensure_worktree(
        repository=repo,
        integration_branch="main",
        workspace=workspace,
        branch="railwarden/WP-001",
        action="execute",
    )

    assert result["operation"] == "create"
    assert workspace.is_dir()
    assert run(workspace, "rev-parse", "--is-inside-work-tree") == "true"
    assert run(workspace, "branch", "--show-current") == "railwarden/WP-001"
