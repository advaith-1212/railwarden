from __future__ import annotations

import pytest

from lfg.errors import ValidationError
from lfg.validation.paths import validate_owned_paths


def test_path_ownership_validation() -> None:
    validate_owned_paths(
        changed_files=["src/a.py"],
        reported_files=["src/a.py"],
        owned_paths=("src/",),
        forbidden_paths=("docs/",),
    )


def test_forbidden_path_rejected() -> None:
    with pytest.raises(ValidationError):
        validate_owned_paths(
            changed_files=["src/secret.py"],
            reported_files=["src/secret.py"],
            owned_paths=("src/",),
            forbidden_paths=("src/secret.py",),
        )
