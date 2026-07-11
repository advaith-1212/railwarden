from __future__ import annotations

import re
import shlex
from typing import Any

from lfg.errors import ConfigurationError

_SHELL_METACHAR = re.compile(r"[;|&`$()<>]")
_COMPOUND_SHELL_SEPARATORS = (" && ", " & ", " | ", ";")


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


def planner_validation_argv(raw: object) -> tuple[str, ...] | None:
    if isinstance(raw, dict):
        command = raw.get("command", raw.get("argv"))
        if isinstance(command, dict):
            argv = command.get("argv", [])
        else:
            argv = command
        try:
            return parse_validation_argv(argv, source="planner.validation_commands")
        except ConfigurationError:
            return None
    if isinstance(raw, list):
        try:
            return parse_validation_argv(raw, source="planner.validation_commands")
        except ConfigurationError:
            return None
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        candidates = [stripped]
        for sep in _COMPOUND_SHELL_SEPARATORS:
            if sep in stripped:
                candidates.append(stripped.split(sep, 1)[0].strip())
        for candidate in candidates:
            try:
                return parse_validation_argv(candidate, source="planner.validation_commands")
            except ConfigurationError:
                continue
    return None


def sanitize_planner_validation_commands(commands: Any) -> list[dict[str, Any]]:
    if not isinstance(commands, list):
        return []
    sanitized: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in commands:
        argv = planner_validation_argv(raw)
        if argv is None:
            continue
        key = tuple(argv)
        if key in seen:
            continue
        seen.add(key)
        sanitized.append(
            {
                "name": f"validation-{len(sanitized) + 1}",
                "command": {"cwd": ".", "argv": list(argv)},
            }
        )
    return sanitized