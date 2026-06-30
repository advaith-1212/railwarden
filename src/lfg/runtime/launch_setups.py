from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from lfg.errors import ConfigurationError, LfgError
from lfg.util.atomic import atomic_write_json


@dataclass(frozen=True)
class LaunchSetup:
    name: str
    provider: str
    model: str
    reasoning_effort: str | None = None
    base_url: str | None = None
    auth_env_var: str | None = None
    env_vars: tuple[str, ...] = ()

    @property
    def model_ref(self) -> str:
        value = f"{self.provider}:{self.model}"
        if self.base_url:
            value = f"{value}@{self.base_url}"
        if self.reasoning_effort:
            value = f"{value}?reasoning={self.reasoning_effort}"
        return value


def setup_storage_root() -> Path:
    override = os.environ.get("LFG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".lfg").resolve()


def setup_registry_path() -> Path:
    return setup_storage_root() / "launch-setups.json"


def setup_secret_path(name: str) -> Path:
    return setup_storage_root() / "launch-setups.d" / f"{_slug(name)}.json"


def load_launch_setups() -> dict[str, LaunchSetup]:
    path = setup_registry_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigurationError(f"Invalid launch setup registry: {path}")
    setups: dict[str, LaunchSetup] = {}
    for name, raw in payload.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            raise ConfigurationError(f"Invalid launch setup entry in {path}")
        setups[name] = LaunchSetup(
            name=name,
            provider=str(raw["provider"]),
            model=str(raw["model"]),
            reasoning_effort=str(raw["reasoning_effort"])
            if raw.get("reasoning_effort")
            else None,
            base_url=str(raw["base_url"]) if raw.get("base_url") else None,
            auth_env_var=str(raw["auth_env_var"]) if raw.get("auth_env_var") else None,
            env_vars=tuple(str(item) for item in raw.get("env_vars", [])),
        )
    return setups


def save_launch_setup(setup: LaunchSetup, *, env: dict[str, str] | None = None) -> None:
    _validate_setup_name(setup.name)
    registry = load_launch_setups()
    registry[setup.name] = setup
    path = setup_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        {
            name: {
                key: value
                for key, value in asdict(item).items()
                if key != "name" and value not in (None, (), [])
            }
            for name, item in sorted(registry.items())
        },
    )
    if env is None:
        return
    secret_path = setup_secret_path(setup.name)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(secret_path, {"env": env})
    secret_path.chmod(0o600)


def load_setup_env(name: str) -> dict[str, str]:
    path = setup_secret_path(name)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("env"), dict):
        raise ConfigurationError(f"Invalid launch setup secret file: {path}")
    env: dict[str, str] = {}
    for key, value in payload["env"].items():
        if isinstance(key, str) and isinstance(value, str):
            env[key] = value
    return env


def merge_setup_env(setup_names: list[str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for name in setup_names:
        for key, value in load_setup_env(name).items():
            existing = merged.get(key)
            if existing is not None and existing != value:
                raise LfgError(
                    f"Launch setups conflict on environment variable {key}: {name}"
                )
            merged[key] = value
    return merged


def write_runtime_env(path: Path, setup_names: list[str]) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = merge_setup_env(setup_names)
    lines = ["# LFG runtime-only secrets. This file must remain ignored by git."]
    for key in sorted(merged):
        lines.append(f"export {key}={json.dumps(merged[key])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return merged


def setup_summary(setup: LaunchSetup) -> str:
    parts = [setup.provider, setup.model]
    if setup.reasoning_effort:
        parts.append(f"reasoning={setup.reasoning_effort}")
    if setup.base_url:
        parts.append(setup.base_url)
    return " | ".join(parts)


def default_auth_env_var(provider: str, setup_name: str) -> str:
    prefix = re.sub(r"[^A-Z0-9]+", "_", provider.upper()).strip("_")
    suffix = re.sub(r"[^A-Z0-9]+", "_", setup_name.upper()).strip("_")
    return f"LFG_{prefix}_{suffix}_API_KEY"


def _validate_setup_name(value: str) -> None:
    text = value.strip()
    if not text:
        raise ConfigurationError("Launch setup name cannot be empty")
    if any(char in text for char in "/\\"):
        raise ConfigurationError("Launch setup name cannot contain path separators")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "setup"
