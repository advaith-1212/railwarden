from __future__ import annotations

import os
import re
from pathlib import Path

from lfg.errors import ConfigurationError

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[^'\"\s]+"),
)


def runtime_secrets_path(runtime_dir: Path) -> Path:
    return runtime_dir / "secrets.env"


def redacted(text: str) -> str:
    value = text
    for pattern in SECRET_PATTERNS:
        value = pattern.sub(_replace_secret, value)
    for name, secret in os.environ.items():
        if not name.endswith(("API_KEY", "TOKEN", "SECRET")):
            continue
        if secret and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    return value


def _replace_secret(match: re.Match[str]) -> str:
    text = match.group(0)
    if "=" in text:
        key, _, _value = text.partition("=")
        return f"{key}=[REDACTED]"
    if ":" in text:
        key, _, _value = text.partition(":")
        return f"{key}: [REDACTED]"
    return "[REDACTED]"


def ensure_runtime_secrets_file(runtime_dir: Path) -> Path:
    path = runtime_secrets_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# LFG runtime-only secrets. This file must remain ignored by git.\n",
            encoding="utf-8",
        )
    path.chmod(0o600)
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        raise ConfigurationError(f"Runtime secrets file has unsafe permissions: {path}")
    return path


def contains_secret(text: str) -> bool:
    return redacted(text) != text
