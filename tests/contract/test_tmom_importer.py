from __future__ import annotations

import os
from pathlib import Path

from lfg.migration.tmom import dry_run_tmom_adoption


def test_tmom_importer_contract_if_fixture_available() -> None:
    target = Path(
        os.environ.get("LFG_TMOM_TARGET", Path.home() / "CODE/Tmom_Deviation")
    )
    source = Path(
        os.environ.get(
            "LFG_TMOM_SOURCE",
            Path.home() / "CODE/tmom-worktrees/orchestrator",
        )
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
