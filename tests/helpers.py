from __future__ import annotations

from pathlib import Path

from railwarden.config.init import initialize_project


def populate_test_context(repo: Path) -> None:
    context_dir = repo / "context"
    for name in (
        "PROJECT_CONTEXT.md",
        "ARCHITECTURE.md",
        "PRODUCT_INVARIANTS.md",
        "SECURITY_MODEL.md",
        "TEST_STRATEGY.md",
        "CONTRIBUTING_AGENTS.md",
    ):
        path = context_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\n\nPopulated for tests.\n", encoding="utf-8")


def initialize_populated_project(repo: Path) -> None:
    initialize_project(repo, yes=True)
    populate_test_context(repo)
