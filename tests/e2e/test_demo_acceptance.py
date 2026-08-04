from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from railwarden.cli.main import main


@pytest.mark.acceptance
def test_demo_runs_a_credential_free_recover_and_integrate_lifecycle(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)

    assert main(["init", "--demo"]) == 0
    assert main(["demo", "run"]) == 0

    report_path = git_repo / ".railwarden-runtime" / "reports" / "demo-acceptance.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["provider"] == "scripted-fake"
    assert report["intentional_failure"][0]["status"] == "failed"
    assert report["cleaned_worktrees"] == ["DEMO-1", "DEMO-2"]
    assert (git_repo / "demo" / "worker-one.txt").exists()
    assert (git_repo / "demo" / "worker-two.txt").exists()
    assert not (git_repo / ".railwarden-worktrees" / "demo-1").exists()


@pytest.mark.acceptance
def test_demo_sets_a_local_identity_when_the_host_has_none(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(
        ["git", "-C", str(git_repo), "config", "--unset", "user.name"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "config", "--unset", "user.email"],
        check=True,
    )
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty-global-config"))
    monkeypatch.chdir(git_repo)

    assert main(["init", "--demo"]) == 0
    assert main(["demo", "run"]) == 0
    assert (
        subprocess.run(
            ["git", "-C", str(git_repo), "config", "--get", "user.name"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        == "RailWarden demo"
    )
