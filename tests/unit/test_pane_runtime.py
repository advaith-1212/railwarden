from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from lfg.config.init import initialize_project
from lfg.config.loader import load_project_files
from lfg.processes.supervisor import ManagedCommand
from lfg.processes.tmux_runner import (
    _visible_runner_script,
    launch_tmux_managed,
    pane_for_worker,
)
from lfg.util.atomic import atomic_write_json
from lfg.providers.adapters import default_adapters
from lfg.runtime.session import load_session_profile
from lfg.workers.pane_runtime import idle_pane_command, task_pane_command


def test_idle_pane_is_shell_not_interactive_provider(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    profile = load_session_profile(files.project)
    adapters = default_adapters()
    for agent in profile.workers:
        adapter = adapters.get(agent.executor_adapter)
        if adapter is None:
            continue
        command = idle_pane_command(files.project, agent, adapter)
        assert "exec ${SHELL:-/bin/sh}" in command
        assert "idle shell" in command
        assert "&& codex" not in command
        assert "agy --dangerously-skip-permissions" not in command
        assert "&& grok" not in command


def test_task_pane_command_tees_and_supports_stdin(tmp_path: Path) -> None:
    log = tmp_path / "task.log"
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do the work\n", encoding="utf-8")
    command = task_pane_command(
        argv=("codex", "exec", "--cd", str(tmp_path), str(prompt)),
        cwd=tmp_path,
        log_path=log,
    )
    assert "codex" in command
    assert "tee -a" in command
    assert str(log) in command

    with_stdin = task_pane_command(
        argv=("agy", "--print"),
        cwd=tmp_path,
        log_path=log,
        stdin_path=prompt,
    )
    assert f"< {prompt}" in with_stdin or f"< '{prompt}'" in with_stdin or str(prompt) in with_stdin
    assert "tee -a" in with_stdin


def test_visible_runner_streams_to_stdout_and_log(tmp_path: Path) -> None:
    command = ManagedCommand(
        argv=("echo", "hello-from-provider"),
        cwd=tmp_path,
        log_path=tmp_path / "logs" / "task.log",
    )
    script = _visible_runner_script(command, tmp_path / "proc.json", "codex")
    assert "sys.stdout.write" in script
    assert "subprocess.PIPE" in script
    assert "start_new_session" not in script
    assert '"echo", "hello-from-provider"' in script


def test_cmd_start_uses_v2_layout_when_session_profile_exists(
    git_repo: Path, monkeypatch
) -> None:
    from lfg.cli import main as cli_main
    from lfg.config.init import initialize_project
    from lfg.config.loader import load_project_files
    from lfg.hermes.profile import generate_hermes_profile
    from lfg.runtime.session import load_session_profile, save_session_profile

    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    profile = load_session_profile(files.project)
    save_session_profile(files.project, profile)
    generate_hermes_profile(files.project, profile)

    captured: dict[str, object] = {}

    def fake_create_session(config, *, attach, profile=None, hermes_profile=None):
        captured["profile"] = profile
        captured["hermes_profile"] = hermes_profile
        captured["attach"] = attach
        return "test-session"

    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli_main, "create_session", fake_create_session)
    assert cli_main.main(["start", "--no-attach"]) == 0
    assert captured["profile"] is not None
    assert captured["hermes_profile"] is not None


def test_cmd_restart_stops_then_starts_with_v2(
    git_repo: Path, monkeypatch
) -> None:
    from lfg.cli import main as cli_main
    from lfg.config.init import initialize_project
    from lfg.config.loader import load_project_files
    from lfg.runtime.session import load_session_profile, save_session_profile

    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    save_session_profile(files.project, load_session_profile(files.project))

    order: list[str] = []
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(
        cli_main,
        "cmd_stop",
        lambda _args: order.append("stop") or 0,
    )
    monkeypatch.setattr(
        cli_main,
        "cmd_start",
        lambda _args: order.append("start") or 0,
    )
    assert cli_main.main(["restart", "--no-attach"]) == 0
    assert order == ["stop", "start"]


def test_pane_for_worker_never_selects_hermes_or_control_panes(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / ".lfg-runtime"
    state = runtime / "state"
    state.mkdir(parents=True)
    atomic_write_json(
        state / "tmux-session.json",
        {
            "session": "test",
            "panes": {
                "hermes": "%1",
                "controller": "%0",
                "observability": "%5",
                "codex-1": "%2",
                "codex": "%9",
            },
        },
    )
    monkeypatch.setattr(
        "lfg.processes.tmux_runner.pane_alive", lambda pane: pane in {"%1", "%0", "%2"}
    )
    assert pane_for_worker(runtime, agent_id="hermes", provider="codex") == "%2"
    assert pane_for_worker(runtime, agent_id=None, provider="codex") == "%2"
    assert pane_for_worker(runtime, agent_id="controller", provider="") is None


def test_launch_tmux_managed_falls_back_when_pane_dead(
    tmp_path: Path, monkeypatch
) -> None:
    import lfg.processes.tmux_runner as tmux_runner

    managed = ManagedCommand(
        argv=("python3", "-c", "print('ok')"),
        cwd=tmp_path,
        log_path=tmp_path / "task.log",
    )
    pid_path = tmp_path / "proc.json"
    script_path = tmp_path / "run.sh"
    monkeypatch.setattr(tmux_runner, "pane_alive", lambda _pane: False)

    def fake_launch_managed(command, *, pid_path, cwd=None, log_path=None):
        from lfg.processes.supervisor import ManagedProcess

        return ManagedProcess(
            pid=4242,
            pgid=4242,
            command=tuple(command.argv if hasattr(command, "argv") else command),
            log_path=managed.log_path,
        )

    monkeypatch.setattr(tmux_runner, "launch_managed", fake_launch_managed)
    result = launch_tmux_managed(
        managed,
        pid_path=pid_path,
        script_path=script_path,
        pane_id="%99",
        provider="codex",
    )
    assert result.pid == 4242


def test_launch_tmux_managed_injects_visible_script(
    tmp_path: Path, monkeypatch
) -> None:
    import lfg.processes.tmux_runner as tmux_runner

    managed = ManagedCommand(
        argv=("codex", "exec", "prompt.md"),
        cwd=tmp_path,
        log_path=tmp_path / "task.log",
    )
    pid_path = tmp_path / "proc.json"
    script_path = tmp_path / "run.sh"
    sent: list[list[str]] = []

    monkeypatch.setattr(tmux_runner, "pane_alive", lambda _pane: True)

    def fake_tmux(args, *, check=True):
        sent.append(list(args))
        return MagicMock(returncode=0)

    monkeypatch.setattr(tmux_runner, "tmux", fake_tmux)
    result = launch_tmux_managed(
        managed,
        pid_path=pid_path,
        script_path=script_path,
        pane_id="%1",
        provider="codex",
    )
    assert result.pid == 0
    assert script_path.exists()
    body = script_path.read_text(encoding="utf-8")
    assert "sys.stdout.write" in body
    assert "codex" in body
    # Final send-keys should run the script, not paste codex exec into a TUI.
    final = sent[-1]
    assert final[0] == "send-keys"
    assert any(str(script_path) in part for part in final)
    assert "C-m" in final
    assert pid_path.exists()
    record = json.loads(pid_path.read_text(encoding="utf-8"))
    assert record["mode"] == "tmux"
    assert record["status"] == "launching"
