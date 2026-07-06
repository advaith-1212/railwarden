from __future__ import annotations

import re
import shlex

from lfg.errors import ConfigurationError

_SHELL_METACHAR = re.compile(r"[;|&`$()<>]")


def parse_validation_argv(raw: object, *, source: str) -> tuple[str, ...]:
    if isinstance(raw, list):
        argv = tuple(str(item) for item in raw if str(item).strip())
    elif isinstance(raw, str):
        stripped = raw.strip()
        if "&;" in stripped or "& ;" in stripped:
            raise ConfigurationError(
                f"{source} uses shell background syntax; use structured argv"
            )
        if _SHELL_METACHAR.search(stripped):
            raise ConfigurationError(
                f"{source} contains shell metacharacters; use structured argv"
            )
        argv = tuple(shlex.split(stripped))
    else:
        raise ConfigurationError(f"{source} requires argv list or string")
    if not argv:
        raise ConfigurationError(f"{source} requires a non-empty argv")
    return argv