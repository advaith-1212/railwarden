from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from lfg.config.models import ProjectConfig
from lfg.runtime.secrets import ensure_runtime_secrets_file
from lfg.runtime.session import SessionProfile
from lfg.util.atomic import atomic_write_text


@dataclass(frozen=True)
class HermesRuntimeProfile:
    home: Path
    config_path: Path
    instructions_path: Path
    env_path: Path
    mcp_config_path: Path
    command: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "home": str(self.home),
            "config_path": str(self.config_path),
            "instructions_path": str(self.instructions_path),
            "env_path": str(self.env_path),
            "mcp_config_path": str(self.mcp_config_path),
            "command": list(self.command),
        }


def hermes_executable() -> str | None:
    for candidate in ("hermes", "hermes-agent"):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def generate_hermes_profile(
    config: ProjectConfig,
    session: SessionProfile,
) -> HermesRuntimeProfile:
    home = config.runtime_directory / "hermes" / session.name
    instructions_path = home / "lfg-factory-instructions.md"
    config_path = home / "config.yaml"
    mcp_config_path = home / "mcp.json"
    env_path = ensure_runtime_secrets_file(home)
    skill_dirs = [
        str(config.repository_root / ".lfg" / "skills"),
        str(config.runtime_directory / "skills"),
    ]
    for directory in skill_dirs:
        Path(directory).mkdir(parents=True, exist_ok=True)
    atomic_write_text(instructions_path, _instructions(config, session))
    atomic_write_text(
        mcp_config_path,
        json.dumps(
            {
                "mcpServers": {
                    "lfg": {
                        "command": "lfg",
                        "args": ["mcp", "serve"],
                        "cwd": str(config.repository_root),
                    }
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    payload = {
        "model": {
            "provider": session.orchestrator.model_profile.provider,
            "name": session.orchestrator.model_profile.model,
            "reasoning_effort": session.orchestrator.model_profile.reasoning_effort,
            "base_url": session.orchestrator.model_profile.base_url,
        },
        "terminal": {"backend": "local"},
        "instructions": {"file": str(instructions_path)},
        "skills": {"external_dirs": skill_dirs},
        "mcp": {"config_file": str(mcp_config_path)},
        "env_file": str(env_path),
    }
    atomic_write_text(config_path, yaml.safe_dump(payload, sort_keys=False))
    executable = hermes_executable() or "hermes"
    command = (
        "env",
        f"HERMES_HOME={home}",
        executable,
        "--config",
        str(config_path),
    )
    return HermesRuntimeProfile(
        home=home,
        config_path=config_path,
        instructions_path=instructions_path,
        env_path=env_path,
        mcp_config_path=mcp_config_path,
        command=command,
    )


def _instructions(config: ProjectConfig, session: SessionProfile) -> str:
    workers = "\n".join(
        f"- {agent.agent_id}: {agent.model_profile.model_ref} via {agent.executor_adapter}"
        for agent in session.workers
    )
    return f"""# LFG Factory Runtime

You are running Hermes Agent as the external orchestrator console for LFG.
LFG is authoritative for durable goals, tasks, DAG scheduling, worktrees,
validation, checkpoints, handoffs, quotas, and integration.

Repository: {config.repository_root}
Runtime: {config.runtime_directory}
Integration branch: {config.integration_branch}

Use the LFG MCP tools for factory state changes. Do not write raw secrets to
tracked files, logs, fixtures, snapshots, or errors.

Workers:
{workers or "- none"}
"""
