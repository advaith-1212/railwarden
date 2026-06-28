from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from lfg.config.models import ProjectConfig
from lfg.hermes.profile import generate_hermes_profile, hermes_executable
from lfg.providers.adapters import ProviderAdapter
from lfg.runtime.session import AgentInstance, SessionProfile, load_session_profile
from lfg.runtime.typed_agents import pydanticai_available
from lfg.runtime.workflow import langgraph_available


def doctor_report(
    config: ProjectConfig,
    *,
    adapters: dict[str, ProviderAdapter],
) -> dict[str, Any]:
    profile = load_session_profile(config)
    return {
        "tools": {
            "hermes": _tool_status(hermes_executable()),
            "tmux": _tool_status(shutil.which("tmux")),
            "langgraph": {
                "status": "healthy" if langgraph_available() else "missing",
                "available": langgraph_available(),
            },
            "pydantic_ai": {
                "status": "healthy" if pydanticai_available() else "missing",
                "available": pydanticai_available(),
            },
        },
        "providers": {
            name: adapters[name].health_check()
            for name in config.worker_providers
            if name in adapters
        },
        "credentials": _credential_status(profile.agents),
        "endpoints": _endpoint_status(profile.agents),
        "coordination": {
            "mcp": _mcp_status(config.repository_root),
            "hermes_profile": _hermes_profile_status(config, profile),
            "runtime_ignored": _path_ignored(
                config.repository_root, config.runtime_directory
            ),
        },
    }


def _tool_status(path: str | None) -> dict[str, object]:
    if path is None:
        return {"status": "missing", "path": None}
    return {"status": "healthy", "path": path}


def _credential_status(agents: tuple[AgentInstance, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for agent in agents:
        auth_ref = agent.model_profile.auth_ref
        if auth_ref is None:
            rows.append(
                {
                    "agent_id": agent.agent_id,
                    "provider": agent.model_profile.provider,
                    "status": "external-or-not-required",
                    "auth_ref": None,
                }
            )
            continue
        if auth_ref.startswith("env:"):
            name = auth_ref.removeprefix("env:")
            rows.append(
                {
                    "agent_id": agent.agent_id,
                    "provider": agent.model_profile.provider,
                    "status": "available" if bool(os.environ.get(name)) else "missing",
                    "auth_ref": auth_ref,
                }
            )
            continue
        rows.append(
            {
                "agent_id": agent.agent_id,
                "provider": agent.model_profile.provider,
                "status": "external",
                "auth_ref": auth_ref,
            }
        )
    return rows


def _endpoint_status(agents: tuple[AgentInstance, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for agent in agents:
        provider = agent.model_profile.provider
        if provider == "ollama":
            base_url = agent.model_profile.base_url or "http://localhost:11434"
            rows.append(
                {
                    "agent_id": agent.agent_id,
                    "provider": provider,
                    "base_url": base_url,
                    **_http_status(f"{base_url.rstrip('/')}/api/tags"),
                }
            )
        elif provider == "azure-foundry":
            endpoint = (
                agent.model_profile.base_url
                or os.environ.get("AZURE_OPENAI_ENDPOINT")
                or os.environ.get("AZURE_AI_FOUNDRY_ENDPOINT")
            )
            if endpoint:
                rows.append(
                    {
                        "agent_id": agent.agent_id,
                        "provider": provider,
                        "base_url": endpoint,
                        **_http_status(endpoint),
                    }
                )
            else:
                rows.append(
                    {
                        "agent_id": agent.agent_id,
                        "provider": provider,
                        "base_url": None,
                        "status": "missing_config",
                        "reason": "AZURE_OPENAI_ENDPOINT or AZURE_AI_FOUNDRY_ENDPOINT is not set",
                    }
                )
    return rows


def _http_status(url: str) -> dict[str, object]:
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=1.0) as response:
            return {"status": "reachable", "http_status": response.status}
    except URLError as exc:
        return {"status": "unreachable", "reason": str(exc.reason)}
    except TimeoutError as exc:
        return {"status": "unreachable", "reason": str(exc)}
    except OSError as exc:
        return {"status": "unreachable", "reason": str(exc)}


def _mcp_status(repository: Path) -> dict[str, object]:
    try:
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as exc:
        return {"status": "failed", "reason": f"MCP SDK unavailable: {exc}"}

    async def probe() -> int:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "lfg.cli.main", "mcp", "serve"],
            cwd=repository,
        )
        async with (
            stdio_client(params, errlog=sys.stderr) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
            return len(result.tools)

    async def probe_with_timeout() -> int:
        with anyio.fail_after(5):
            return await probe()

    try:
        tool_count = anyio.run(probe_with_timeout)
    except TimeoutError as exc:
        return {"status": "failed", "reason": str(exc)}
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}
    return {
        "status": "healthy" if tool_count else "failed",
        "tool_count": tool_count,
        "transport": "mcp-stdio",
    }


def _hermes_profile_status(
    config: ProjectConfig, profile: SessionProfile
) -> dict[str, object]:
    executable = hermes_executable()
    if executable is None:
        return {
            "status": "skipped",
            "reason": "Hermes executable is not installed",
        }
    hermes_profile = generate_hermes_profile(config, profile)
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_profile.home)
    completed = subprocess.run(
        [executable, "mcp", "test", "lfg"],
        cwd=config.repository_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    return {
        "status": "healthy" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "home": str(hermes_profile.home),
        "command": " ".join(hermes_profile.command),
        "mcp_test": _tail(output),
    }


def _tail(text: str, *, limit: int = 2000) -> str:
    return text[-limit:]


def _path_ignored(repository: Path, path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repository), "check-ignore", "-q", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0
