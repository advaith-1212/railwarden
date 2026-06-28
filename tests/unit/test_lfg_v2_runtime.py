from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

import anyio
import pytest

import lfg.cli.main as cli_main
from lfg.cli.main import main
from lfg.config.init import initialize_project
from lfg.config.loader import load_project_files
from lfg.engine.controller import controller_tick
from lfg.hermes.profile import generate_hermes_profile
from lfg.mcp.server import tool_schemas
from lfg.models.registry import list_models, validate_model_refs
from lfg.runtime.checkpoints import create_checkpoint_commit
from lfg.runtime.doctor import doctor_report
from lfg.runtime.model_refs import parse_model_ref
from lfg.runtime.quota import update_usage
from lfg.runtime.secrets import contains_secret, redacted
from lfg.runtime.session import (
    AgentInstance,
    load_session_profile,
    model_profile_from_ref,
    save_session_profile,
    update_agent,
)
from lfg.runtime.skills import create_runtime_skill
from lfg.runtime.tasks import ensure_task, load_tasks, save_tasks
from lfg.runtime.typed_agents import create_pydanticai_agent
from lfg.tmux.session import launch_layout


def test_model_ref_parsing_normalizes_supported_shapes() -> None:
    codex = parse_model_ref("codex:gpt-5.5?reasoning=high")
    assert codex.provider == "codex"
    assert codex.reasoning_effort == "high"
    assert codex.normalized() == "codex:gpt-5.5?reasoning=high"

    ollama = parse_model_ref("ollama:qwen3-coder@http://localhost:11434")
    assert ollama.provider == "ollama"
    assert ollama.base_url == "http://localhost:11434"

    openai_compatible = parse_model_ref(
        "openai-compatible:custom@https://api.example.com/v1"
    )
    assert openai_compatible.base_url == "https://api.example.com/v1"

    composer = parse_model_ref("composer:grok-composer-2.5-fast")
    assert composer.provider == "composer"


def test_pydanticai_agent_translates_lfg_model_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-azure")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-10-21")

    for ref in (
        "openai:gpt-5.2",
        "anthropic:claude-opus-4.6",
        "gemini:gemini-3-pro",
        "azure-foundry:deployment-a",
        "ollama:qwen3-coder@http://localhost:11434",
        "openai-compatible:custom-model@https://api.example.com/v1",
    ):
        agent = create_pydanticai_agent(
            system_prompt="Return short answers.",
            model_profile=model_profile_from_ref(ref),
            output_type=str,
        )
        assert agent is not None

    with pytest.raises(Exception, match="direct PydanticAI calls"):
        create_pydanticai_agent(
            system_prompt="Return short answers.",
            model_profile=model_profile_from_ref("codex:gpt-5.5"),
            output_type=str,
        )


def test_provider_registry_lists_valid_default_model_refs() -> None:
    models = list_models()
    refs = [model["ref"] for model in models]

    assert "openai:gpt-5.2" in refs
    assert "composer:grok-composer-2.5-fast" in refs
    assert "ollama:qwen3-coder@http://localhost:11434" in refs
    assert all(result["status"] == "ok" for result in validate_model_refs(refs))


def test_session_profile_serializes_without_raw_secret(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-testsecretvalue123456789")
    profile = load_session_profile(files.project)
    assert profile.reviewer is not None
    profile = update_agent(
        profile,
        AgentInstance(
            agent_id=profile.reviewer.agent_id,
            role=profile.reviewer.role,
            model_profile=model_profile_from_ref("openai:gpt-5.2"),
            executor_adapter=profile.reviewer.executor_adapter,
            state=profile.reviewer.state,
            quota_policy=profile.reviewer.quota_policy,
            active_task=profile.reviewer.active_task,
        ),
    )
    save_session_profile(files.project, profile)

    text = (
        files.project.runtime_directory / "state" / "session-profile.json"
    ).read_text(encoding="utf-8")
    assert "sk-testsecretvalue" not in text
    assert "env:" in text


def test_default_session_profile_uses_valid_cli_model_refs(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    profile = load_session_profile(load_project_files(git_repo).project)

    assert (
        profile.orchestrator.model_profile.model_ref == "codex:gpt-5.5?reasoning=high"
    )
    assert profile.reviewer is not None
    assert profile.reviewer.model_profile.model_ref == "codex:gpt-5.5?reasoning=high"
    antigravity = next(
        agent for agent in profile.workers if agent.executor_adapter == "antigravity"
    )
    composer = next(
        agent for agent in profile.workers if agent.executor_adapter == "composer"
    )
    assert antigravity.model_profile.model_ref == "antigravity:gemini-3.5-flash-low"
    assert composer.model_profile.model_ref == "composer:grok-composer-2.5-fast"


def test_secret_redaction_covers_keys_tokens_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-envsecretvalue123456")
    text = (
        "api_key=sk-inlinevalue123456 token: abcdefghijklmnop sk-envsecretvalue123456"
    )
    assert contains_secret(text)
    assert "sk-envsecret" not in redacted(text)
    assert "[REDACTED]" in redacted(text)


def test_hermes_profile_generation_is_runtime_only(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    profile = load_session_profile(files.project)
    hermes = generate_hermes_profile(files.project, profile)

    assert files.project.runtime_directory in hermes.config_path.parents
    assert hermes.env_path.stat().st_mode & 0o777 == 0o600
    assert "LFG is authoritative" in hermes.soul_path.read_text(encoding="utf-8")
    assert "name: lfg-factory" in hermes.skill_path.read_text(encoding="utf-8")
    config = hermes.config_path.read_text(encoding="utf-8")
    assert "mcp_servers:" in config
    assert "command: lfg" in config
    assert Path(hermes.command[3]).name in {"hermes", "hermes-agent"}
    assert hermes.command[4] == "chat"
    assert "--cli" in hermes.command
    mcp = json.loads(hermes.mcp_config_path.read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["lfg"]["command"] == "lfg"
    assert mcp["mcpServers"]["lfg"]["args"] == ["mcp", "serve"]


def test_mcp_tool_schemas_cover_required_tools() -> None:
    names = {schema["name"] for schema in tool_schemas()}
    assert {
        "lfg.goal.submit",
        "lfg.plan.create",
        "lfg.plan.approve",
        "lfg.task.list",
        "lfg.task.route",
        "lfg.agent.swap",
        "lfg.agent.pause",
        "lfg.agent.resume",
        "lfg.quota.status",
        "lfg.checkpoint.create",
        "lfg.integration.status",
        "lfg.skill.create",
        "lfg.skill.promote",
    } <= names


def test_mcp_stdio_server_lists_and_calls_lfg_tools(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)

    async def probe() -> None:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "lfg.cli.main", "mcp", "serve"],
            cwd=git_repo,
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
            assert "lfg.task.list" in {tool.name for tool in result.tools}
            response = await session.call_tool("lfg.task.list", {})
            assert response.structuredContent == {"tasks": []}

    anyio.run(probe)


def test_tmux_launch_layout_has_factory_and_observability(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    profile = load_session_profile(files.project)
    hermes = generate_hermes_profile(files.project, profile)

    specs = launch_layout(files.project, profile, hermes)
    assert {spec.window for spec in specs} == {"factory", "observability"}
    assert any(
        "Hermes | role=orchestrator exec=hermes provider=" in spec.title
        for spec in specs
    )
    assert any("role=coder exec=codex provider=codex" in spec.title for spec in specs)
    assert any("lfg observability" in spec.command for spec in specs)
    assert not any("watch -n 5 lfg observability" in spec.command for spec in specs)


def test_launch_wizard_persists_budget_quota_fallback_and_review_models(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_project(git_repo, yes=True)
    monkeypatch.chdir(git_repo)

    def fake_create_session(*args: object, **kwargs: object) -> str:
        assert args
        assert kwargs["profile"] is not None
        assert kwargs["hermes_profile"] is not None
        return "test-session"

    monkeypatch.setattr(cli_main, "create_session", fake_create_session)
    monkeypatch.setattr(
        "sys.stdin",
        _TtyStringIO(
            "\n".join(
                [
                    "interactive",
                    "advanced",
                    "openai:gpt-5.2",
                    "anthropic:claude-opus-4.6",
                    "codex:gpt-5.5?reasoning=medium",
                    "antigravity:claude-opus-4.6-thinking",
                    "composer:grok-composer-2.5-fast",
                    "gemini:gemini-3-pro",
                    "acceptance-budget",
                    "auto-swap",
                    "20",
                    "7",
                    "yes",
                    "12345",
                ]
            )
            + "\n"
        ),
    )

    assert main(["launch", "--no-attach"]) == 0

    profile = load_session_profile(load_project_files(git_repo).project)
    assert profile.name == "interactive"
    assert profile.budget_label == "acceptance-budget"
    assert profile.fallback_policy == "auto-swap"
    assert profile.orchestrator.model_profile.model_ref == "openai:gpt-5.2"
    assert profile.architect.model_profile.model_ref == "anthropic:claude-opus-4.6"
    assert profile.workers[0].model_profile.reasoning_effort == "medium"
    assert (
        profile.workers[2].model_profile.model_ref == "composer:grok-composer-2.5-fast"
    )
    assert profile.reviewer is not None
    assert profile.reviewer.model_profile.model_ref == "gemini:gemini-3-pro"
    assert profile.orchestrator.quota_policy.warning_threshold_percent == 20
    assert profile.orchestrator.quota_policy.pause_threshold_percent == 7
    assert profile.orchestrator.quota_policy.manual_token_limit == 12345
    assert all(
        worker.quota_policy.manual_token_limit == 12345 for worker in profile.workers
    )


def test_doctor_report_checks_credentials_endpoints_mcp_and_ignore(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    profile = load_session_profile(files.project)
    openai_worker = AgentInstance(
        agent_id=profile.workers[0].agent_id,
        role=profile.workers[0].role,
        model_profile=model_profile_from_ref("openai:gpt-5.2"),
        executor_adapter=profile.workers[0].executor_adapter,
        state=profile.workers[0].state,
        quota_policy=profile.workers[0].quota_policy,
        active_task=profile.workers[0].active_task,
    )
    ollama_reviewer = AgentInstance(
        agent_id="local-reviewer",
        role="reviewer",
        model_profile=model_profile_from_ref("ollama:qwen3-coder@http://127.0.0.1:9"),
        executor_adapter="pydanticai",
    )
    profile = update_agent(profile, openai_worker)
    profile = type(profile)(
        name=profile.name,
        project=profile.project,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        orchestrator=profile.orchestrator,
        architect=profile.architect,
        workers=profile.workers,
        reviewer=ollama_reviewer,
        validator=profile.validator,
        fallback_policy=profile.fallback_policy,
        budget_label=profile.budget_label,
    )
    save_session_profile(files.project, profile)

    report = doctor_report(files.project, adapters={})

    credential = next(
        row
        for row in report["credentials"]
        if row["agent_id"] == openai_worker.agent_id
    )
    endpoint = next(
        row for row in report["endpoints"] if row["agent_id"] == "local-reviewer"
    )
    assert credential["status"] == "missing"
    assert endpoint["status"] == "unreachable"
    assert report["coordination"]["mcp"]["status"] == "healthy"
    assert report["coordination"]["runtime_ignored"] is True


def test_low_quota_prevents_new_task_launch(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    _write_package(git_repo)
    subprocess.run(
        ["git", "-C", str(git_repo), "checkout", "-b", "integration/lfg"],
        check=True,
        capture_output=True,
    )
    files = load_project_files(git_repo)
    profile = load_session_profile(files.project)
    codex = next(agent for agent in profile.agents if agent.executor_adapter == "codex")
    update_usage(
        files.project.runtime_directory,
        codex,
        remaining_percent=4.0,
        confidence="manual",
    )
    (files.project.runtime_directory / "state").mkdir(parents=True, exist_ok=True)
    (files.project.runtime_directory / "state" / "pending-plan.json").write_text(
        json.dumps({"approved": True}) + "\n", encoding="utf-8"
    )

    result = controller_tick(files, launch=False, integrate=False)
    assert result["launched"] == []
    tasks_payload = json.loads(
        (files.project.runtime_directory / "state" / "tasks.json").read_text(
            encoding="utf-8"
        )
    )
    tasks = tasks_payload["tasks"]
    assert tasks[0]["status"] == "handoff_needed"
    assert tasks[0]["reason"] == "pause-threshold"
    assert Path(str(tasks[0]["handoff_packet"])).exists()
    updated_profile = load_session_profile(files.project)
    updated_codex = next(
        agent for agent in updated_profile.agents if agent.agent_id == codex.agent_id
    )
    assert updated_codex.state == "paused"
    assert updated_codex.active_task == "task-WP-1"


def test_checkpoint_filters_disallowed_files(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    _write_package(git_repo)
    subprocess.run(
        ["git", "-C", str(git_repo), "checkout", "-b", "integration/lfg"],
        check=True,
        capture_output=True,
    )
    files = load_project_files(git_repo)
    subprocess.run(
        ["git", "-C", str(git_repo), "checkout", "-b", "lfg/wp-1"],
        check=True,
        capture_output=True,
    )
    (git_repo / "src").mkdir()
    (git_repo / "src" / "allowed.txt").write_text("allowed\n", encoding="utf-8")
    (git_repo / "README.md").write_text("blocked\n", encoding="utf-8")

    result = create_checkpoint_commit(
        files.project,
        task_id="task-WP-1",
        workspace=git_repo,
        attempt=1,
        allowed_paths=("src",),
    )

    assert result.status == "created"
    assert result.files == ("src/allowed.txt",)
    committed = subprocess.run(
        ["git", "-C", str(git_repo), "show", "--name-only", "--format=", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    assert committed == ["src/allowed.txt"]


def test_model_profile_uses_pydanticai_compatible_ref() -> None:
    profile = model_profile_from_ref("openai:gpt-5.2")
    assert profile.model_ref == "openai:gpt-5.2"
    assert profile.transport == "api"


def test_worker_prompt_includes_skills_and_mcp_routing(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    _write_package(git_repo)
    subprocess.run(
        ["git", "-C", str(git_repo), "checkout", "-b", "integration/lfg"],
        check=True,
        capture_output=True,
    )
    files = load_project_files(git_repo)
    create_runtime_skill(files.project, "repo-build", "Always run the build.")
    ensure_task(
        files.project.runtime_directory,
        package_id="WP-1",
        name="First package",
        dependencies=(),
    )
    (files.project.runtime_directory / "state" / "pending-plan.json").write_text(
        json.dumps({"approved": True}) + "\n", encoding="utf-8"
    )

    controller_tick(files, launch=False, integrate=False)

    updated = load_tasks(files.project.runtime_directory)[0]
    prompt = Path(str(updated["prompt_path"])).read_text(encoding="utf-8")
    assert "Skill: repo-build" in prompt
    assert "LFG MCP routing" in prompt


def test_agent_swap_creates_handoff_and_relaunches_with_override(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_project(git_repo, yes=True)
    _write_package(git_repo)
    subprocess.run(
        ["git", "-C", str(git_repo), "checkout", "-b", "integration/lfg"],
        check=True,
        capture_output=True,
    )
    files = load_project_files(git_repo)
    (files.project.runtime_directory / "state").mkdir(parents=True, exist_ok=True)
    (files.project.runtime_directory / "state" / "pending-plan.json").write_text(
        json.dumps({"approved": True, "goal": "ship"}) + "\n", encoding="utf-8"
    )
    controller_tick(files, launch=False, integrate=False)
    assigned = load_tasks(files.project.runtime_directory)[0]
    assert assigned["provider"] == "codex"

    monkeypatch.chdir(git_repo)
    assert (
        main(
            [
                "agent",
                "swap",
                "codex-1",
                "--to",
                "antigravity:claude-opus-4.6-thinking",
            ]
        )
        == 0
    )

    swapped = load_tasks(files.project.runtime_directory)[0]
    assert swapped["status"] == "handoff_needed"
    assert swapped["provider_override"] == "antigravity"
    assert Path(str(swapped["handoff_packet"])).exists()
    profile = load_session_profile(files.project)
    agent = next(item for item in profile.agents if item.agent_id == "codex-1")
    assert agent.executor_adapter == "antigravity"
    assert agent.active_task == "task-WP-1"

    controller_tick(load_project_files(git_repo), launch=False, integrate=False)
    relaunched = load_tasks(files.project.runtime_directory)[0]
    assert relaunched["status"] == "assigned"
    assert relaunched["provider"] == "antigravity"


def test_crash_resume_checkpoints_dirty_worktree_and_reassigns(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    _write_package(git_repo)
    subprocess.run(
        ["git", "-C", str(git_repo), "checkout", "-b", "integration/lfg"],
        check=True,
        capture_output=True,
    )
    files = load_project_files(git_repo)
    (files.project.runtime_directory / "state").mkdir(parents=True, exist_ok=True)
    (files.project.runtime_directory / "state" / "pending-plan.json").write_text(
        json.dumps({"approved": True, "goal": "recover"}) + "\n", encoding="utf-8"
    )
    controller_tick(files, launch=False, integrate=False)
    task = load_tasks(files.project.runtime_directory)[0]
    workspace = Path(str(task["worktree"]))
    (workspace / "src").mkdir()
    (workspace / "src" / "crash.txt").write_text("partial\n", encoding="utf-8")
    log_path = Path(str(task["log_path"]))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("worker process crashed\n", encoding="utf-8")
    task["status"] = "running"
    save_tasks(files.project.runtime_directory, [task])
    process_path = files.project.runtime_directory / "processes" / "task-WP-1.json"
    process_path.parent.mkdir(parents=True, exist_ok=True)
    process_path.write_text(
        json.dumps({"pid": 0, "provider": "codex", "log_path": str(log_path)}),
        encoding="utf-8",
    )

    controller_tick(load_project_files(git_repo), launch=False, integrate=False)

    recovered = load_tasks(files.project.runtime_directory)[0]
    assert recovered["status"] == "assigned"
    assert recovered["provider"] != "codex"
    assert Path(str(recovered["handoff_packet"])).exists()
    assert recovered["checkpoint"]["status"] == "created"
    assert recovered["checkpoint"]["files"] == ["src/crash.txt"]
    message = subprocess.run(
        ["git", "-C", str(workspace), "log", "-1", "--format=%s"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert message == "lfg checkpoint: task-WP-1 attempt 1"


def _write_package(repo: Path) -> None:
    (repo / ".lfg" / "work_packages.yaml").write_text(
        """
schema_version: 1.0.0
work_packages:
  - id: WP-1
    name: First package
    objective: Do work
    owned_paths: ["src"]
    preferred_providers: ["codex"]
""",
        encoding="utf-8",
    )


class _TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True
