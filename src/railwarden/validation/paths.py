from __future__ import annotations

from pathlib import PurePosixPath

from railwarden.errors import ValidationError


def normalize_repo_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/")


def path_is_within(path: str, root: str) -> bool:
    normalized_path = normalize_repo_path(path)
    normalized_root = normalize_repo_path(root)
    if (
        PurePosixPath(normalized_path).is_absolute()
        or ".." in PurePosixPath(normalized_path).parts
    ):
        return False
    return normalized_path == normalized_root or normalized_path.startswith(
        normalized_root + "/"
    )


def validate_owned_paths(
    *,
    changed_files: list[str],
    reported_files: list[str],
    owned_paths: tuple[str, ...],
    forbidden_paths: tuple[str, ...],
) -> None:
    if not owned_paths:
        raise ValidationError("Package does not define owned paths")
    unexpected = [
        path
        for path in changed_files
        if not any(path_is_within(path, root) for root in owned_paths)
    ]
    if unexpected:
        raise ValidationError(
            "Changed files outside owned paths: " + ", ".join(sorted(unexpected))
        )
    forbidden = [
        path
        for path in changed_files
        if any(path_is_within(path, root) for root in forbidden_paths)
    ]
    if forbidden:
        raise ValidationError(
            "Forbidden paths changed: " + ", ".join(sorted(forbidden))
        )
    changed_set = {normalize_repo_path(path) for path in changed_files}
    reported_set = {normalize_repo_path(path) for path in reported_files}
    if changed_set != reported_set:
        raise ValidationError(
            "changed_files mismatch: "
            f"missing={sorted(changed_set - reported_set)} "
            f"extra={sorted(reported_set - changed_set)}"
        )
