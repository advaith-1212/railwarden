from __future__ import annotations

from railwarden.planning.pipeline import _resolve_owned_path_overlaps


def test_resolve_owned_path_overlaps_keeps_later_package() -> None:
    packages = [
        {"id": "WP-001", "owned_paths": ["tests/", "package.json"]},
        {"id": "WP-004", "owned_paths": ["tests/", "vitest.config.js"]},
    ]
    resolved = _resolve_owned_path_overlaps(packages)
    by_id = {item["id"]: item["owned_paths"] for item in resolved}
    assert "tests/" not in by_id["WP-001"]
    assert "tests/" in by_id["WP-004"]
    assert "package.json" in by_id["WP-001"]
