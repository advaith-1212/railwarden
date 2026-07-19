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
    """Leave a plain shell ready for LFG to inject batch provider commands.

    Worker panes must not start interactive provider TUIs (``codex``, ``agy``,
    ``grok``). Those fight ``tmux send-keys`` task injection and hide real work
    behind a half-typed chat line. Idle is an inspectable shell; tasks run
    ``codex exec`` / equivalent visibly in that shell.
    """
    root = shlex.quote(str(config.repository_root))
    provider = adapter.name
    label = (
        f"{agent.agent_id} ({provider}) ready — idle shell; "
        "LFG runs tasks here as visible CLI commands"
    )
    return (
        f"cd {root} && echo {shlex.quote(label)} && "
        "exec ${SHELL:-/bin/sh}"
    )


def task_pane_command(
    *,
    argv: tuple[str, ...],
    cwd: Path,
    log_path: Path,
    stdin_path: Path | None = None,
) -> str:
    """Shell form of a visible in-pane task (stdout/stderr teed to the task log)."""
    quoted_argv = " ".join(shlex.quote(item) for item in argv)
    quoted_cwd = shlex.quote(str(cwd))
    quoted_log = shlex.quote(str(log_path))
    stdin_redirect = ""
    if stdin_path is not None:
        stdin_redirect = f" < {shlex.quote(str(stdin_path))}"
    return (
        f"cd {quoted_cwd} && "
        f"echo '[LFG] starting task in {quoted_cwd}' && "
        f"{quoted_argv}{stdin_redirect} 2>&1 | tee -a {quoted_log}; "
        f"echo '[LFG] task finished (exit='$?')'"
    )
