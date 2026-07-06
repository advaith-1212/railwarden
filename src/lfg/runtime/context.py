from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from lfg.config.models import ProjectConfig, WorkPackage
from lfg.util.atomic import atomic_write_text

CONTEXT_FILES = (
    "PROJECT_CONTEXT.md",
    "ARCHITECTURE.md",
    "PRODUCT_INVARIANTS.md",
    "SECURITY_MODEL.md",
    "TEST_STRATEGY.md",
    "CONTRIBUTING_AGENTS.md",
)


TEMPLATE_MARKERS = (
    "Describe the repository",
    "Record stable architectural decisions",
    "List behavior that work packages must preserve",
    "Document trust boundaries",
    "Document required checks",
    "Describe how LFG workers should inspect",
)


def context_status(
    config: ProjectConfig, packages: dict[str, WorkPackage]
) -> dict[str, Any]:
    context_dir = config.repository_root / "context"
    files = []
    for name in CONTEXT_FILES:
        path = context_dir / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        stripped = text.strip()
        stale = not stripped or any(marker in text for marker in TEMPLATE_MARKERS)
        files.append(
            {
                "file": str(path),
                "exists": path.exists(),
                "empty": not stripped,
                "stale": stale,
                "updated_at": path.stat().st_mtime if path.exists() else None,
            }
        )
    missing_context_refs = [
        {
            "package_id": package.package_id,
            "name": package.name,
            "missing": len(package.context_refs) == 0,
        }
        for package in packages.values()
        if len(package.context_refs) == 0
    ]
    status = "ok"
    if any(row["empty"] or row["stale"] for row in files) or missing_context_refs:
        status = "needs_population"
    return {
        "status": status,
        "context_dir": str(context_dir),
        "files": files,
        "work_packages_missing_context_refs": missing_context_refs,
    }


def write_context_file(config: ProjectConfig, file: str, content: str) -> dict[str, Any]:
    relative = Path(file)
    if relative.is_absolute():
        path = relative
    else:
        path = config.repository_root / "context" / relative
    try:
        path.relative_to(config.repository_root / "context")
    except ValueError as exc:
        raise ValueError("Context writes must stay under context/") from exc
    atomic_write_text(path, content.rstrip() + "\n")
    return {"status": "written", "file": str(path), "updated_at": time.time()}


def resolve_context_refs(
    repository_root: Path,
    refs: tuple[str, ...] | list[str],
    *,
    max_chars_per_file: int = 8000,
) -> str:
    chunks: list[str] = []
    for ref in refs:
        path = repository_root / str(ref)
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        if len(text) > max_chars_per_file:
            text = text[:max_chars_per_file] + "\n...[truncated]..."
        chunks.append(f"### {ref}\n\n{text}")
    return "\n\n".join(chunks)
