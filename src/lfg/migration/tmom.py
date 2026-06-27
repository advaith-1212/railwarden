from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lfg.git import branch_is_ancestor, output, run_git, worktree_entries


@dataclass(frozen=True)
class TmomAdoptionReport:
    target_repository: Path
    source_orchestration: Path
    integration_branch: str | None
    source_branch: str | None
    worktree_root: str | None
    package_count: int
    merged_packages: tuple[str, ...]
    package_branches: tuple[str, ...]
    dirty_worktrees: tuple[str, ...]
    legacy_validation_policy: bool
    legacy_provider_configuration: bool

    def markdown(self) -> str:
        lines = [
            "# TMOM Adoption Dry Run",
            "",
            f"Target repository: `{self.target_repository}`",
            f"Source orchestration: `{self.source_orchestration}`",
            f"Integration branch: `{self.integration_branch}`",
            f"Source orchestration branch: `{self.source_branch}`",
            f"Existing worktree root: `{self.worktree_root}`",
            f"Legacy package count: {self.package_count}",
            "",
            "## Detected Conditions",
            f"- merged packages: {', '.join(self.merged_packages) or 'none'}",
            f"- package branches: {', '.join(self.package_branches) or 'none'}",
            f"- dirty worktrees requiring repair: {', '.join(self.dirty_worktrees) or 'none'}",
            f"- legacy validation policy: {self.legacy_validation_policy}",
            f"- legacy provider configuration: {self.legacy_provider_configuration}",
            "",
            "## Safety",
            "- Dry run only; no TMOM files were modified.",
            "- Runtime logs and provider transcripts are intentionally excluded.",
        ]
        return "\n".join(lines) + "\n"


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def dry_run_tmom_adoption(
    target_repository: Path, source_repository: Path
) -> TmomAdoptionReport:
    orchestration = source_repository / "orchestration"
    policy = _load_yaml(orchestration / "config" / "factory_policy.yaml")
    work_packages = _load_yaml(orchestration / "work_packages.yaml")
    integration_branch = (
        str(policy.get("integration_branch"))
        if policy.get("integration_branch")
        else None
    )
    worktree_root = (
        str(policy.get("worktree_root")) if policy.get("worktree_root") else None
    )
    source_branch = output(source_repository, "branch", "--show-current")
    raw_packages = work_packages.get("work_packages", [])
    package_ids = [
        str(item.get("id"))
        for item in raw_packages
        if isinstance(item, dict) and item.get("id")
    ]
    branches: list[str] = []
    merged: list[str] = []
    dirty: list[str] = []
    entries = worktree_entries(target_repository)
    for entry in entries:
        branch_ref = entry.get("branch", "")
        if branch_ref.startswith("refs/heads/agent/WP-"):
            branch = branch_ref.removeprefix("refs/heads/")
            branches.append(branch)
            status = run_git(
                Path(entry["worktree"]), "status", "--porcelain", check=False
            )
            if status.stdout.strip():
                dirty.append(str(entry["worktree"]))
    for package_id in package_ids:
        branch = (
            "agent/WP-004-005"
            if package_id in {"WP-004", "WP-005"}
            else f"agent/{package_id}"
        )
        if integration_branch and branch_is_ancestor(
            target_repository, branch, integration_branch
        ):
            merged.append(package_id)
    return TmomAdoptionReport(
        target_repository=target_repository,
        source_orchestration=source_repository,
        integration_branch=integration_branch,
        source_branch=source_branch,
        worktree_root=worktree_root,
        package_count=len(package_ids),
        merged_packages=tuple(sorted(merged)),
        package_branches=tuple(sorted(set(branches))),
        dirty_worktrees=tuple(sorted(dirty)),
        legacy_validation_policy="validation" in policy,
        legacy_provider_configuration=(
            orchestration / "config" / "workers.json"
        ).exists(),
    )
