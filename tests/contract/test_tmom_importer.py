from __future__ import annotations

from pathlib import Path

from railwarden.compat import environment_value
from railwarden.migration.tmom import dry_run_tmom_adoption


def test_tmom_importer_contract_if_fixture_available() -> None:
    target = Path(
        environment_value("RAILWARDEN_TMOM_TARGET")
        or Path.home() / "CODE/Tmom_Deviation"
    )
    source = Path(
        environment_value("RAILWARDEN_TMOM_SOURCE")
        or Path.home() / "CODE/tmom-worktrees/orchestrator"
    )
    if not target.exists() or not source.exists():
        return
    report = dry_run_tmom_adoption(target, source)
    assert report.integration_branch == "integration/agentic-mcp"
    assert report.source_branch == "agent/ORCH-001"
    assert "WP-007" in report.merged_packages
    assert any(branch.endswith("WP-010") for branch in report.package_branches)
    assert report.worktree_root is not None
    assert report.worktree_root.endswith("CODE/tmom-worktrees")
