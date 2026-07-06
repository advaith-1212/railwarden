from __future__ import annotations

import shlex
from pathlib import Path

from lfg.config.models import ProjectConfig
from lfg.providers.adapters import ProviderAdapter
from lfg.runtime.session import AgentInstance


def idle_pane_command(
    config: ProjectConfig,
    agent: AgentInstance,
    adapter: ProviderAdapter,
) -> str:
    root = shlex.quote(str(config.repository_root))
    provider = adapter.name
    label = f"{agent.agent_id} ({provider}) ready — tasks assigned by Hermes/LFG"
    if provider == "codex":
        return f"cd {root} && echo {shlex.quote(label)} && exec codex"
    if provider == "antigravity":
        return f"cd {root} && echo {shlex.quote(label)} && exec agy"
    if provider == "composer":
        return f"cd {root} && echo {shlex.quote(label)} && exec grok"
    return f"cd {root} && echo {shlex.quote(label)} && exec ${{SHELL:-/bin/sh}}"


def task_pane_command(
    *,
    argv: tuple[str, ...],
    cwd: Path,
    log_path: Path,
) -> str:
    quoted_argv = " ".join(shlex.quote(item) for item in argv)
    quoted_cwd = shlex.quote(str(cwd))
    quoted_log = shlex.quote(str(log_path))
    return (
        f"cd {quoted_cwd} && "
        f"echo '[LFG] starting task in {quoted_cwd}' && "
        f"{quoted_argv} 2>&1 | tee -a {quoted_log}; "
        f"echo '[LFG] task finished (exit='$?')'"
    )