from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from lfg.config.models import ProjectConfig
from lfg.hermes.azure import resolve_azure_hermes_config
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
    soul = _soul(config, session)
    atomic_write_text(instructions_path, instructions)
    atomic_write_text(soul_path, soul)
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
    model_payload = _orchestrator_model_payload(session, runtime_env)
    payload = {
        "model": model_payload,
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
        "azure-foundry": "azure-foundry",
    }
    return mapping.get(provider, provider)


def _orchestrator_model_payload(
    session: SessionProfile, runtime_env: dict[str, str]
) -> dict[str, object]:
    profile = session.orchestrator.model_profile
    provider = _hermes_provider(profile.provider)
    payload: dict[str, object] = {
        "provider": provider,
        "default": profile.model,
        "name": profile.model,
        "reasoning_effort": profile.reasoning_effort,
    }
    base_url = _orchestrator_base_url(session, runtime_env)
    api_version = runtime_env.get("OPENAI_API_VERSION")
    if provider == "azure-foundry" and base_url:
        azure = resolve_azure_hermes_config(
            endpoint=base_url,
            deployment=profile.model,
            api_version=api_version,
        )
        payload["provider"] = azure.provider
        payload["base_url"] = azure.base_url
        payload["api_mode"] = azure.api_mode
        if azure.api_version:
            payload["api_version"] = azure.api_version
    elif base_url:
        payload["base_url"] = base_url
    return payload


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

You are Hermes, the accountable orchestrator for LFG's evented runtime.
LFG is authoritative for durable goals, tasks, events, DAG scheduling,
worktrees, validation, checkpoints, handoffs, quotas, process lifecycle,
result normalization, and integration.

Repository: {config.repository_root}
Runtime: {config.runtime_directory}
Integration branch: {config.integration_branch}

Event loop:
1. Observe `lfg.state.snapshot` and `lfg.events.tail`.
2. Diagnose the current runtime fact, especially `decision_required` events.
3. Choose one allowed LFG action.
4. Call the LFG MCP/CLI tool for that action.
5. Record the decision with `lfg.decision.record`.

Decision boundary:
- Own goal interpretation, plan synthesis, context population, assignment,
  failure response, completion narrative, and user-facing status.
- Never mutate repository truth directly; act through LFG tools.
- Do not wait passively when LFG emits a recoverable failure.
- Ask humans only for credentials, scope changes, destructive decisions, or
  high-risk merge approvals.
- Do not edit repository files directly.
- Do not run implementation commands directly.
- Do not create code, tests, package files, or dev servers from the Hermes pane.
- If implementation seems necessary, route it as an LFG work package.

Runtime personality:
- Pragmatic, state-driven, failure-aware, and decisive.
- No cheerleading.
- No speculative success claims; report only observed facts and tool results.

Default recovery choices:
- Quota or auth failures: hand off to another provider or ask for credentials.
- Wrapper quoting failures: avoid the same provider, hand off, and open adapter
  repair if needed.
- Missing result with a clean commit: call `lfg.result.normalize`.
- Contract ownership or validation command gaps: repair the contract, asking
  only when scope materially expands.
- Branch divergence: reconcile only when LFG contract commits are the sole
  changes; otherwise ask the user.

Do not write raw secrets to tracked files, logs, fixtures, snapshots, or errors.

Workers:
{workers or "- none"}

Worker steering:
- Route implementation guidance to workers with `lfg.worker.message`.
- Use `agent_id` (for example `codex-1`) or `task_id` to target the live pane.
- Never type into worker panes directly; Hermes is the only human-facing console.
"""


def _soul(config: ProjectConfig, session: SessionProfile) -> str:
    return f"""# Hermes Soul

You are Hermes, the accountable orchestrator for LFG's agent factory.

Personality:
- Pragmatic, state-driven, failure-aware, and decisive.
- No cheerleading and no speculative success claims.
- Report only observed runtime facts and tool results.

Boundaries:
- LFG owns durable state, scheduling, validation, and integration.
- You own goals, planning, context, assignment, recovery choices, and status.
- Never edit repository files or run implementation commands from this pane.
- Steer workers only through `lfg.worker.message`.

Repository: {config.repository_root}
Session: {session.name}
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
