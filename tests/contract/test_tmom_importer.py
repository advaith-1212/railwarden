from __future__ import annotations

from pathlib import Path

from lfg.migration.tmom import dry_run_tmom_adoption


def test_tmom_importer_contract_if_fixture_available() -> None:
    target = Path("/Users/advaith/CODE/Tmom_Deviation")
    source = Path("/Users/advaith/CODE/tmom-worktrees/orchestrator")
    if not target.exists() or not source.exists():
        return
    report = dry_run_tmom_adoption(target, source)
    assert report.integration_branch == "integration/agentic-mcp"
    assert report.source_branch == "agent/ORCH-001"
    assert "WP-007" in report.merged_packages
    assert any(branch.endswith("WP-010") for branch in report.package_branches)
    assert report.worktree_root == "/Users/advaith/CODE/tmom-worktrees"
