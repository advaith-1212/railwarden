from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from lfg.config.models import ProjectConfig
from lfg.runtime.launch_setups import load_setup_env, write_runtime_env
from lfg.runtime.secrets import ensure_runtime_secrets_file
from lfg.runtime.session import SessionProfile
from lfg.util.atomic import atomic_write_text


@dataclass(frozen=True)
class HermesRuntimeProfile:
    home: Path
    config_path: Path
    instructions_path: Path
    soul_path: Path
    skill_path: Path
    env_path: Path
    mcp_config_path: Path
    command: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "home": str(self.home),
            "config_path": str(self.config_path),
            "instructions_path": str(self.instructions_path),
            "soul_path": str(self.soul_path),
            "skill_path": str(self.skill_path),
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
    soul_path = home / "SOUL.md"
    skill_path = home / "skills" / "lfg-factory" / "SKILL.md"
    config_path = home / "config.yaml"
    mcp_config_path = home / "mcp.json"
    env_path = ensure_runtime_secrets_file(home)
    runtime_env = write_runtime_env(env_path, _session_setup_names(session))
    _inherit_existing_auth(home)
    skill_dirs = [
        str(config.repository_root / ".lfg" / "skills"),
        str(config.runtime_directory / "skills"),
    ]
    for directory in skill_dirs:
        Path(directory).mkdir(parents=True, exist_ok=True)
    instructions = _instructions(config, session)
    atomic_write_text(instructions_path, instructions)
    atomic_write_text(soul_path, instructions)
    atomic_write_text(skill_path, _skill(instructions))
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
    command = _lfg_mcp_command()
    payload = {
        "model": {
            "provider": _hermes_provider(session.orchestrator.model_profile.provider),
            "default": session.orchestrator.model_profile.model,
            "name": session.orchestrator.model_profile.model,
            "reasoning_effort": session.orchestrator.model_profile.reasoning_effort,
            "base_url": _orchestrator_base_url(session, runtime_env),
        },
        "providers": {},
        "toolsets": ["hermes-cli"],
        "terminal": {"backend": "local"},
        "mcp_servers": {
            "lfg": {
                "command": command[0],
                "args": command[1:],
                "enabled": True,
            }
        },
    }
    atomic_write_text(config_path, yaml.safe_dump(payload, sort_keys=False))
    executable = hermes_executable() or "hermes"
    hermes_command = (
        "env",
        f"HERMES_HOME={home}",
        "HERMES_ACCEPT_HOOKS=1",
        executable,
        "chat",
        "--cli",
        "--source",
        "lfg",
    )
    return HermesRuntimeProfile(
        home=home,
        config_path=config_path,
        instructions_path=instructions_path,
        soul_path=soul_path,
        skill_path=skill_path,
        env_path=env_path,
        mcp_config_path=mcp_config_path,
        command=hermes_command,
    )


def _lfg_mcp_command() -> list[str]:
    return ["lfg", "mcp", "serve"]


def _hermes_provider(provider: str) -> str:
    mapping = {
        "codex": "openai-codex",
        "openai": "openai",
        "anthropic": "anthropic",
        "gemini": "google",
        "ollama": "ollama-launch",
        "openai-compatible": "openai",
        "azure-foundry": "azure",
    }
    return mapping.get(provider, provider)


def _inherit_existing_auth(home: Path) -> None:
    target = home / "auth.json"
    source = Path.home() / ".hermes" / "auth.json"
    if not source.exists():
        return
    if target.exists() and _has_provider_auth(target):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _has_provider_auth(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    providers = payload.get("providers")
    credential_pool = payload.get("credential_pool")
    if isinstance(providers, dict) and providers:
        return True
    return isinstance(credential_pool, dict) and "openai-codex" in credential_pool


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

Use the LFG MCP tools for factory state changes. For repository goals, your job
is orchestration only:
- On `goal ...`, call LFG goal/plan tools and present the plan for approval.
- On `approved`, call LFG approval/freeze tools and let the controller dispatch
  workers.
- Do not edit repository files directly.
- Do not run implementation commands directly.
- Do not create code, tests, package files, or dev servers from the Hermes pane.
- If direct implementation seems necessary, route it as an LFG work package.

Do not write raw secrets to tracked files, logs, fixtures, snapshots, or errors.

Workers:
{workers or "- none"}
"""


def _skill(instructions: str) -> str:
    return f"""---
name: lfg-factory
description: Use LFG MCP tools to run this repository as a durable agentic development factory.
---

{instructions}
"""


def _session_setup_names(session: SessionProfile) -> list[str]:
    names = [agent.setup_name for agent in session.agents if agent.setup_name]
    return [name for name in names if name]


def _orchestrator_base_url(
    session: SessionProfile, runtime_env: dict[str, str]
) -> str | None:
    base_url = session.orchestrator.model_profile.base_url
    if base_url:
        return base_url
    setup_name = session.orchestrator.setup_name
    if setup_name:
        setup_env = load_setup_env(setup_name)
        for key in (
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_AI_FOUNDRY_ENDPOINT",
            "OPENAI_BASE_URL",
        ):
            value = setup_env.get(key)
            if value:
                return value
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_AI_FOUNDRY_ENDPOINT", "OPENAI_BASE_URL"):
        value = runtime_env.get(key)
        if value:
            return value
    return None
