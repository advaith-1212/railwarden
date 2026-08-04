from __future__ import annotations

import os
from pathlib import Path

PREFERRED_CONFIG_DIR = ".railwarden"
LEGACY_CONFIG_DIR = ".lfg"
PREFERRED_RUNTIME_DIR = ".railwarden-runtime"
LEGACY_RUNTIME_DIR = ".lfg-runtime"
PREFERRED_WORKTREE_DIR = ".railwarden-worktrees"
LEGACY_WORKTREE_DIR = ".lfg-worktrees"
PREFERRED_RESULTS_DIR = ".railwarden-results"
LEGACY_RESULTS_DIR = ".lfg-results"


def environment_value(name: str) -> str | None:
    """Return a RailWarden setting, falling back to its deprecated LFG name."""
    prefix = "RAILWARDEN_"
    if not name.startswith(prefix):
        raise ValueError(f"RailWarden environment names must start with {prefix}")
    if name in os.environ:
        return os.environ[name]
    return os.environ.get(f"LFG_{name.removeprefix(prefix)}")


def preferred_or_legacy_path(preferred: Path, legacy: Path) -> Path:
    """Prefer current state, but reuse legacy state when it is the only state."""
    if preferred.exists():
        return preferred
    if legacy.exists():
        return legacy
    return preferred


def project_config_directory(repository_root: Path) -> Path:
    return preferred_or_legacy_path(
        repository_root / PREFERRED_CONFIG_DIR,
        repository_root / LEGACY_CONFIG_DIR,
    )


def compatible_project_path(
    repository_root: Path,
    value: str | None,
    *,
    preferred_name: str,
    legacy_name: str,
) -> Path:
    text = value or "auto"
    if text not in {"auto", preferred_name, legacy_name}:
        path = Path(text)
        return path if path.is_absolute() else repository_root / path
    return preferred_or_legacy_path(
        repository_root / preferred_name,
        repository_root / legacy_name,
    )
