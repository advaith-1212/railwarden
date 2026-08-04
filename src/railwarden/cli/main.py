from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TypedDict

from railwarden import __version__
from railwarden.cli import ui
from railwarden.compat import environment_value, project_config_directory
from railwarden.config.init import initialize_project
from railwarden.config.loader import load_project_files
from railwarden.engine.controller import controller_tick
from railwarden.engine.dashboard import render_dashboard
from railwarden.errors import RailWardenError
from railwarden.git import changed_files_in_commit, discover_repo, head
from railwarden.hermes.kanban import (
    HermesAdapter,
    apply_bootstrap,
    apply_import_plan,
    bootstrap_plan,
    build_import_plan,
    hermes_status,
)
from railwarden.hermes.mailbox import (
    append_message,
    messages_for,
    parse_directive,
    read_messages,
)
from railwarden.hermes.profile import generate_hermes_profile
from railwarden.integration.manager import integrate_one
from railwarden.mcp.server import serve as serve_mcp
from railwarden.migration.tmom import dry_run_tmom_adoption
from railwarden.models.registry import list_models, validate_model_refs
from railwarden.planning.antigravity import AntigravityClaudePlanner
from railwarden.planning.jobs import execute_planning_job
from railwarden.planning.pipeline import approve_latest_plan, create_pending_plan
from railwarden.providers.adapters import default_adapters
from railwarden.runtime.checkpoints import create_checkpoint_commit
from railwarden.runtime.context import context_status, write_context_file
from railwarden.runtime.decisions import inspect_failure, record_decision
from railwarden.runtime.doctor import doctor_report
from railwarden.runtime.events import read_events
from railwarden.runtime.handoff import create_handoff_packet
from railwarden.runtime.launch_setups import (
    LaunchSetup,
    default_auth_env_var,
    load_launch_setups,
    save_launch_setup,
    setup_summary,
)
from railwarden.runtime.model_refs import parse_model_ref
from railwarden.runtime.quota import load_quota, update_usage
from railwarden.runtime.results import normalize_result
from railwarden.runtime.secrets import ensure_runtime_secrets_file
from railwarden.runtime.session import (
    AgentInstance,
    QuotaPolicy,
    SessionProfile,
    load_session_profile,
    model_profile_from_ref,
    reset_agent_for_launch,
    save_session_profile,
    update_agent,
)
from railwarden.runtime.tasks import load_tasks, transition_task
from railwarden.runtime.workflow import advance_workflow, load_workflow
from railwarden.scheduler.classifier import (
    classify_packages,
    execution_plan,
    package_branch,
    package_worktree,
)
from railwarden.scheduler.dag import Dag
from railwarden.tmux.session import create_session, panes, session_name, stop_session
from railwarden.validation.package import run_package_validation
from railwarden.validation.review import run_package_review


def configured_project(start: Path) -> tuple[Path, Any]:
    root = discover_repo(start)
    return root, load_project_files(root)


def default_legacy_source(target: Path, explicit_source: str | None) -> Path:
    if explicit_source:
        return Path(explicit_source).resolve()
    env_source = environment_value("RAILWARDEN_TMOM_SOURCE") or environment_value(
        "RAILWARDEN_TMON_SOURCE"
    )
    if env_source:
        return Path(env_source).resolve()
    candidate = target.parent / "tmom-worktrees" / "orchestrator"
    if candidate.exists():
        return candidate.resolve()
    raise RailWardenError(
        "Legacy adoption requires --source when no sibling prototype exists."
    )


def cmd_init(args: argparse.Namespace) -> int:
    root = discover_repo(Path.cwd())
    if not args.yes:
        result = initialize_project(root, yes=False)
        print("RailWarden project setup preview")
        print()
        print(f"Repository: {root}")
        print(
            "Files to create: .railwarden/project.yaml, .railwarden/work_packages.yaml, .railwarden/validation.yaml"
        )
        print("Runtime state: .railwarden-runtime/ (ignored by git)")
        print()
        print("Proposed .gitignore additions:")
        print(result["gitignore_proposal"] or "  none")
        print()
        print("Run `warden init --yes` to write these files.")
        return 0
    result = initialize_project(root, yes=True)
    print("RailWarden project initialized")
    print()
    for key in ("config", "work_packages", "validation", "state_schema"):
        if key in result:
            print(f"{key.replace('_', ' ').title()}: {result[key]}")
    print()
    print("Next steps:")
    print(
        "  1. Run `warden doctor` to check Hermes, tmux, providers, MCP, and runtime ignore rules."
    )
    print("  2. Run `warden launch` to start the factory tmux session.")
    print("  3. Tell Hermes what to build in the factory window.")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    root = discover_repo(Path.cwd())
    config_dir = project_config_directory(root)
    configured = (config_dir / "project.yaml").exists() or (
        config_dir / "factory.yaml"
    ).exists()
    created = False
    if not configured:
        if not args.yes:
            ui.banner("RailWarden setup", subtitle=str(root))
            ui.warning("This repository is not configured yet.")
            ui.info("Run [bold]warden setup --yes[/] to create project config.")
            ui.info("Then run [bold]warden doctor[/] before launch.")
            return 0
        result = initialize_project(root, yes=True)
        created = True
        if result.get("needs_commit"):
            ui.warning("Configuration files are ready - commit them before proceeding.")
    ui.setup_summary_block(
        repository=str(root),
        configured=configured or created,
        created=created,
    )
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    target = (
        Path(args.repository).resolve()
        if args.repository
        else discover_repo(Path.cwd())
    )
    source = default_legacy_source(target, args.source)
    report = dry_run_tmom_adoption(target, source)
    markdown = report.markdown()
    if args.dry_run:
        output_path = Path(args.report).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        print(markdown)
        print(f"Report written: {output_path}")
        return 0
    raise RailWardenError(
        "Non-dry-run adopt is intentionally approval-gated; run with --dry-run first."
    )


def cmd_plan(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    states = classify_packages(files.project, files.packages)
    plan = execution_plan(files.project, states)
    critical_path = Dag(files.packages).critical_path() if files.packages else ()
    payload = {
        "packages": [state.to_dict() for state in states],
        "plan": plan,
        "critical_path": list(critical_path),
        "expected_concurrency": files.project.worker_concurrency,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    fixture_path = environment_value("RAILWARDEN_PLANNER_OUTPUT")
    fixture = Path(fixture_path).read_text(encoding="utf-8") if fixture_path else None
    pending = create_pending_plan(files.project, args.goal, planner_output_text=fixture)
    print(f"Created pending plan: {pending.run_id}")
    print(pending.plan_markdown)
    print("Run `warden approve plan` to begin execution.")
    return 0


def cmd_planning_worker(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    try:
        execute_planning_job(files.project, args.run_id)
    except Exception:
        return 1
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    if args.target != "plan":
        raise RailWardenError("Only `warden approve plan` is supported")
    _, files = configured_project(Path.cwd())
    payload = approve_latest_plan(files.project)
    print(f"Approved plan: {payload['run_id']}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Start the tmux factory.

    Prefer the v2 Hermes + worker layout when a session profile already exists
    (the normal case after ``warden launch``). Only fall back to the legacy pane
    layout when there is no saved profile.
    """
    _, files = configured_project(Path.cwd())
    profile_path = files.project.runtime_directory / "state" / "session-profile.json"
    if profile_path.exists():
        profile = load_session_profile(files.project)
        hermes_profile = generate_hermes_profile(files.project, profile)
        name = create_session(
            files.project,
            attach=not args.no_attach,
            profile=profile,
            hermes_profile=hermes_profile,
        )
    else:
        name = create_session(files.project, attach=not args.no_attach)
    print(f"Started RailWarden session: {name}")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    """Stop and recreate the factory using the same path as ``warden start``.

    Uses the saved session profile + Hermes runtime when present, so restart
    does not drop back to the legacy ``warden hermes`` help pane.
    """
    cmd_stop(args)
    return cmd_start(args)


def _prompt(default: str, label: str, *, hint: str = "") -> str:
    return ui.prompt_text(default, label, hint=hint)


def _prompt_float(default: float, label: str) -> float:
    return ui.prompt_float(default, label)


def _prompt_bool(default: bool, label: str) -> bool:
    return ui.prompt_bool(default, label)


def _prompt_optional_int(default: int | None, label: str) -> int | None:
    return ui.prompt_optional_int(default, label)


def _prompt_choice(default: str, label: str, choices: dict[str, str]) -> str:
    return ui.prompt_choice(default, label, choices)


def _prompt_secret(label: str, *, hint: str = "") -> str:
    return ui.prompt_secret(label, hint=hint)


def _role_provider_key(agent: AgentInstance) -> str:
    if agent.role == "coder":
        return f"{agent.role}:{agent.executor_adapter}"
    return agent.role


def _role_label(agent: AgentInstance) -> str:
    if agent.role == "coder":
        return f"{agent.agent_id} ({agent.executor_adapter} worker)"
    return f"{agent.agent_id} ({agent.role})"


def _build_setup_choices(
    agent: AgentInstance, setups: dict[str, LaunchSetup]
) -> dict[str, str]:
    provider_choices = ROLE_PROVIDER_CHOICES[_role_provider_key(agent)]
    choices: dict[str, str] = {}
    if agent.setup_name and agent.setup_name in setups:
        choices[agent.setup_name] = (
            f"Reuse current setup: {setup_summary(setups[agent.setup_name])}"
        )
    for name, setup in sorted(setups.items()):
        if setup.provider not in provider_choices or name in choices:
            continue
        choices[name] = f"Saved setup: {setup_summary(setup)}"
    choices["create-new"] = "Create a new named setup"
    if not agent.setup_name:
        choices["keep-current"] = (
            f"Keep current raw ref: {agent.model_profile.model_ref}"
        )
    return choices


class CreatedSetup(TypedDict):
    setup: LaunchSetup
    env: dict[str, str]


def _select_setup_for_agent(
    agent: AgentInstance,
    *,
    default_model_ref: str,
    setups: dict[str, LaunchSetup],
    step: int | None = None,
    total: int | None = None,
) -> tuple[str, str | None, str | None]:
    if not sys.stdin.isatty():
        return default_model_ref, agent.model_profile.auth_ref, agent.setup_name
    role = _role_label(agent)
    if step is not None and total is not None:
        ui.wizard_step(
            step,
            total,
            role,
            hint="Choose a saved setup or create a new named provider configuration.",
        )
    else:
        ui.role_card(role, "Configure provider credentials and model for this role.")
    choices = _build_setup_choices(agent, setups)
    default_choice = (
        agent.setup_name
        if agent.setup_name and agent.setup_name in choices
        else "keep-current"
        if "keep-current" in choices
        else "create-new"
    )
    selected = _prompt_choice(default_choice, f"{_role_label(agent)} setup", choices)
    if selected == "keep-current":
        return default_model_ref, agent.model_profile.auth_ref, agent.setup_name
    if selected == "create-new":
        created = _create_named_setup(agent)
        save_launch_setup(created["setup"], env=created["env"])
        setups[created["setup"].name] = created["setup"]
        return (
            created["setup"].model_ref,
            f"env:{created['setup'].auth_env_var}"
            if created["setup"].auth_env_var
            else None,
            created["setup"].name,
        )
    setup = setups[selected]
    return (
        setup.model_ref,
        f"env:{setup.auth_env_var}" if setup.auth_env_var else None,
        setup.name,
    )


def _create_named_setup(agent: AgentInstance) -> CreatedSetup:
    provider_choices = ROLE_PROVIDER_CHOICES[_role_provider_key(agent)]
    provider = _prompt_choice(
        next(iter(provider_choices)),
        f"{_role_label(agent)} provider",
        provider_choices,
    )
    model = _prompt_model(provider)
    default_name = _default_setup_name(provider, model)
    name = _prompt(default_name, f"{_role_label(agent)} setup name")
    base_url = None
    reasoning = None
    auth_env_var = None
    env: dict[str, str] = {}

    if provider == "codex":
        model, reasoning = _split_reasoning(model)
    elif provider in {"openai", "anthropic", "gemini"}:
        auth_env_var = default_auth_env_var(provider, name)
        api_key = _prompt_secret(f"{provider} API key")
        env[auth_env_var] = api_key
        if provider == "openai":
            env["OPENAI_API_KEY"] = api_key
        elif provider == "anthropic":
            env["ANTHROPIC_API_KEY"] = api_key
        else:
            env["GEMINI_API_KEY"] = api_key
            env["GOOGLE_API_KEY"] = api_key
    elif provider == "azure-foundry":
        from railwarden.hermes.azure import validate_azure_inference_endpoint

        auth_env_var = default_auth_env_var(provider, name)
        endpoint = _prompt(
            "",
            "Azure inference endpoint URL",
            hint="Use https://<resource>.openai.azure.com/openai/v1 - not a Foundry project URL.",
        )
        endpoint = validate_azure_inference_endpoint(endpoint)
        api_key = _prompt_secret(
            "Azure API key",
            hint="Stored in ~/.railwarden/launch-setups.d/ and .railwarden-runtime/hermes/.../secrets.env",
        )
        api_version = _prompt(
            "",
            "Azure OpenAI API version",
            hint="Leave blank for /openai/v1 GA endpoints. Required only for legacy URLs.",
        )
        base_url = endpoint
        env[auth_env_var] = api_key
        env["AZURE_FOUNDRY_API_KEY"] = api_key
        env["AZURE_OPENAI_API_KEY"] = api_key
        env["AZURE_OPENAI_ENDPOINT"] = endpoint
        env["AZURE_AI_FOUNDRY_ENDPOINT"] = endpoint
        env["AZURE_FOUNDRY_BASE_URL"] = endpoint
        if api_version:
            env["OPENAI_API_VERSION"] = api_version
    elif provider == "ollama":
        base_url = _prompt("http://localhost:11434", "Ollama base URL")
    elif provider == "openai-compatible":
        base_url = _prompt("https://api.example.com/v1", "OpenAI-compatible base URL")
        auth_env_var = default_auth_env_var(provider, name)
        api_key = _prompt_secret("OpenAI-compatible API key")
        env[auth_env_var] = api_key
        env["OPENAI_API_KEY"] = api_key

    return {
        "setup": LaunchSetup(
            name=name,
            provider=provider,
            model=model,
            reasoning_effort=reasoning,
            base_url=base_url,
            auth_env_var=auth_env_var,
            env_vars=tuple(sorted(env)),
        ),
        "env": env,
    }


def _prompt_model(provider: str) -> str:
    choices = MODEL_CHOICES[provider]
    default = next(iter(choices))
    value = _prompt_choice(default, f"{provider} model", choices)
    if value == "custom":
        return _prompt("", f"Custom {provider} model")
    return value


def _default_setup_name(provider: str, model: str) -> str:
    text = f"{provider}-{model}"
    return (
        text.lower()
        .replace("?", "-")
        .replace("=", "-")
        .replace("@", "-")
        .replace(":", "-")
        .replace("/", "-")
    )


def _split_reasoning(model: str) -> tuple[str, str | None]:
    if "?reasoning=" not in model:
        return model, None
    name, _, reasoning = model.partition("?reasoning=")
    return name, reasoning or None


def _section(title: str, *, hint: str = "") -> None:
    ui.section(title, hint=hint)


def _table(headers: list[str], rows: list[list[object]]) -> None:
    ui.print_table(headers, rows)


def _status(value: object) -> str:
    return ui.status_cell(value)


def _print_doctor(payload: dict[str, Any]) -> None:
    ui.banner(
        "RailWarden doctor",
        subtitle="Tools, providers, credentials, Hermes, MCP, and git ignore rules.",
    )

    tools = payload.get("tools", {})
    if isinstance(tools, dict):
        _section("Tools")
        _table(
            ["Tool", "Status", "Path/Detail", "Version/Note"],
            [
                [
                    name,
                    _status(_mapping_or_empty(row).get("status", "-")),
                    _mapping_or_empty(row).get("path")
                    or _mapping_or_empty(row).get("available")
                    or "-",
                    _tool_note(_mapping_or_empty(row)),
                ]
                for name, row in tools.items()
            ],
        )
        print()

    providers = payload.get("providers", {})
    if isinstance(providers, dict):
        _section("Provider CLIs")
        _table(
            ["Provider", "Status", "Executable", "Model"],
            [
                [
                    name,
                    _status(_mapping_or_empty(row).get("status", "-")),
                    _mapping_or_empty(row).get("executable")
                    or _mapping_or_empty(row).get("reason")
                    or "-",
                    _mapping_or_empty(row).get("model", "-"),
                ]
                for name, row in providers.items()
            ],
        )
        print()

    _section("Credentials")
    _table(
        ["Agent", "Provider", "Status", "Auth ref"],
        [
            [
                row.get("agent_id", "-"),
                row.get("provider", "-"),
                _status(row.get("status", "-")),
                row.get("auth_ref", "-"),
            ]
            for row in _list_of_dicts(payload.get("credentials"))
        ],
    )
    print()

    endpoints = _list_of_dicts(payload.get("endpoints"))
    if endpoints:
        _section("Endpoints")
        _table(
            ["Agent", "Provider", "Status", "Base URL", "Detail"],
            [
                [
                    row.get("agent_id", "-"),
                    row.get("provider", "-"),
                    _status(row.get("status", "-")),
                    row.get("base_url", "-"),
                    row.get("reason") or row.get("http_status") or "-",
                ]
                for row in endpoints
            ],
        )
        print()

    coordination = _mapping_or_empty(payload.get("coordination"))
    mcp = _mapping_or_empty(coordination.get("mcp"))
    hermes_profile = _mapping_or_empty(coordination.get("hermes_profile"))
    _section("Coordination")
    _table(
        ["Check", "Status", "Detail"],
        [
            [
                "RailWarden MCP stdio",
                _status(mcp.get("status", "-")),
                f"{mcp.get('tool_count', 0)} tools",
            ],
            [
                "Hermes generated profile",
                _status(hermes_profile.get("status", "-")),
                hermes_profile.get("home") or hermes_profile.get("reason") or "-",
            ],
            [
                "Runtime ignored by git",
                "OK yes" if coordination.get("runtime_ignored") else "FAIL no",
                ".railwarden-runtime",
            ],
        ],
    )
    if hermes_profile.get("mcp_test"):
        print()
        print("Hermes MCP test:")
        for line in str(hermes_profile["mcp_test"]).splitlines()[-8:]:
            print(f"  {line}")
    print()

    planner = _mapping_or_empty(payload.get("planning_architect"))
    if planner:
        _section("Planning architect")
        _table(
            ["Detected", "Authenticated", "Model", "Limitation"],
            [
                [
                    planner.get("detected"),
                    planner.get("authenticated"),
                    planner.get("model_identifier", "-"),
                    planner.get("remaining_limitation", "-"),
                ]
            ],
        )


def _mapping_or_empty(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _tool_note(row: dict[str, Any]) -> str:
    if row.get("update_available"):
        return "update available"
    version = row.get("version")
    if version:
        return str(version).splitlines()[0]
    return "-"


LAUNCH_PRESETS: dict[str, dict[str, str]] = {
    "guided": {
        "description": "Guided provider/setup wizard with saved named setups and provider-specific prompts.",
        "orchestrator": "",
        "architect": "",
        "reviewer": "",
    },
    "default-dev-shop": {
        "description": "Keep the current Hermes orchestrator profile, use Antigravity for planning, and Codex/Antigravity/Composer workers.",
        "orchestrator": "",
        "architect": "antigravity:claude-opus-4.6-thinking",
        "reviewer": "",
    },
    "codex-antigravity": {
        "description": "Keep the current Hermes orchestrator, use Codex for coding, and Antigravity for planning.",
        "orchestrator": "",
        "architect": "antigravity:claude-opus-4.6-thinking",
        "reviewer": "",
    },
    "local-only": {
        "description": "Keep the current Hermes orchestrator and prefer local Ollama for API-backed review/repair roles.",
        "orchestrator": "",
        "architect": "antigravity:claude-opus-4.6-thinking",
        "reviewer": "ollama:qwen3-coder@http://localhost:11434",
    },
    "advanced": {
        "description": "Expert mode: ask for raw model refs and quota settings.",
        "orchestrator": "",
        "architect": "",
        "reviewer": "",
    },
}


FALLBACK_POLICIES = {
    "prompt-before-swap": "Pause and ask before moving active work to a different model.",
    "auto-swap": "Automatically hand off low-quota or failed work to the next eligible provider.",
    "manual-only": "Never swap automatically; only explicit `warden agent swap` changes models.",
}


ROLE_PROVIDER_CHOICES: dict[str, dict[str, str]] = {
    "orchestrator": {
        "codex": "OpenAI Codex CLI",
        "openai": "OpenAI API",
        "anthropic": "Anthropic API",
        "gemini": "Google Gemini API",
        "azure-foundry": "Azure Foundry / Azure OpenAI",
        "ollama": "Local Ollama",
        "openai-compatible": "Custom OpenAI-compatible endpoint",
    },
    "architect": {
        "antigravity": "Antigravity CLI",
    },
    "coder:codex": {
        "codex": "OpenAI Codex CLI",
    },
    "coder:antigravity": {
        "antigravity": "Antigravity CLI",
    },
    "coder:composer": {
        "composer": "Composer CLI",
    },
    "reviewer": {
        "openai": "OpenAI API",
        "anthropic": "Anthropic API",
        "gemini": "Google Gemini API",
        "azure-foundry": "Azure Foundry / Azure OpenAI",
        "ollama": "Local Ollama",
        "openai-compatible": "Custom OpenAI-compatible endpoint",
    },
    "validator": {
        "openai": "OpenAI API",
        "anthropic": "Anthropic API",
        "gemini": "Google Gemini API",
        "azure-foundry": "Azure Foundry / Azure OpenAI",
        "ollama": "Local Ollama",
        "openai-compatible": "Custom OpenAI-compatible endpoint",
    },
}


MODEL_CHOICES: dict[str, dict[str, str]] = {
    "codex": {
        "gpt-5.5?reasoning=high": "GPT-5.5 high reasoning",
        "gpt-5.5?reasoning=medium": "GPT-5.5 medium reasoning",
        "custom": "Enter a custom Codex model slug",
    },
    "antigravity": {
        "claude-opus-4.6-thinking": "Claude Opus 4.6 Thinking",
        "gemini-3.5-flash-low": "Gemini 3.5 Flash Low",
        "custom": "Enter a custom Antigravity model slug",
    },
    "composer": {
        "grok-composer-2.5-fast": "Grok Composer 2.5 Fast",
        "custom": "Enter a custom Composer model slug",
    },
    "openai": {
        "gpt-5.5": "GPT-5.5",
        "gpt-5.2": "GPT-5.2",
        "custom": "Enter a custom OpenAI model slug",
    },
    "anthropic": {
        "claude-opus-4.6": "Claude Opus 4.6",
        "custom": "Enter a custom Anthropic model slug",
    },
    "gemini": {
        "gemini-3-pro": "Gemini 3 Pro",
        "custom": "Enter a custom Gemini model slug",
    },
    "azure-foundry": {
        "gpt-5.5": "GPT-5.5 deployment",
        "gpt-5.4": "GPT-5.4 deployment",
        "custom": "Enter your Azure deployment name",
    },
    "ollama": {
        "qwen3-coder": "qwen3-coder",
        "custom": "Enter a custom Ollama model name",
    },
    "openai-compatible": {
        "custom": "Enter a custom model name",
    },
}


def _agent_with_model_and_policy(
    agent: AgentInstance,
    *,
    model_ref: str,
    quota_policy: QuotaPolicy,
    auth_ref: str | None = None,
    setup_name: str | None = None,
) -> AgentInstance:
    updated = reset_agent_for_launch(
        agent,
        model_ref=model_ref,
        auth_ref=auth_ref,
        setup_name=setup_name,
    )
    return AgentInstance(
        agent_id=updated.agent_id,
        role=updated.role,
        model_profile=updated.model_profile,
        executor_adapter=updated.executor_adapter,
        setup_name=updated.setup_name,
        state=updated.state,
        quota_policy=quota_policy,
        active_task=updated.active_task,
    )


def _launch_wizard_total(profile: SessionProfile, *, advanced: bool) -> int:
    optional_roles = int(profile.reviewer is not None) + int(
        profile.validator is not None
    )
    total = 4 + len(profile.workers) + optional_roles + 2
    if advanced:
        total += 4
    return total


def _build_launch_profile(
    profile: SessionProfile, *, name: str | None, preset_name: str | None = None
) -> SessionProfile:
    ui.banner(
        "RailWarden launch wizard",
        subtitle="Configure Hermes, workers, and factory runtime. Use Up/Down to select options.",
    )
    setups = load_launch_setups()
    advanced = preset_name == "advanced"
    total_steps = _launch_wizard_total(profile, advanced=advanced)
    step = 1
    if name is None:
        ui.wizard_step(step, total_steps, "Session", hint="Name for this factory run.")
        profile_name = _prompt(profile.name, "Session profile name")
        step += 1
    else:
        profile_name = name
    if preset_name is None:
        ui.wizard_step(
            step,
            total_steps,
            "Launch preset",
            hint="Guided is recommended for first-time setup.",
        )
        preset_name = _prompt_choice(
            "guided",
            "Choose a launch preset",
            {key: value["description"] for key, value in LAUNCH_PRESETS.items()},
        )
        step += 1
    elif preset_name not in LAUNCH_PRESETS:
        raise RailWardenError(f"Expected one of: {', '.join(LAUNCH_PRESETS)}")
    preset = LAUNCH_PRESETS[preset_name]
    advanced = preset_name == "advanced"
    guided = preset_name != "advanced"
    if advanced:
        total_steps = _launch_wizard_total(profile, advanced=True)

    orchestrator_ref = (
        preset["orchestrator"] or profile.orchestrator.model_profile.model_ref
    )
    architect_ref = preset["architect"] or profile.architect.model_profile.model_ref
    worker_refs = [_default_worker_ref(worker) for worker in profile.workers]
    orchestrator_auth_ref = profile.orchestrator.model_profile.auth_ref
    architect_auth_ref = profile.architect.model_profile.auth_ref
    worker_auth_refs = [worker.model_profile.auth_ref for worker in profile.workers]
    orchestrator_setup_name = profile.orchestrator.setup_name
    architect_setup_name = profile.architect.setup_name
    worker_setup_names = [worker.setup_name for worker in profile.workers]
    reviewer_ref = (
        preset["reviewer"] or profile.reviewer.model_profile.model_ref
        if profile.reviewer is not None
        else None
    )
    reviewer_auth_ref = (
        profile.reviewer.model_profile.auth_ref
        if profile.reviewer is not None
        else None
    )
    reviewer_setup_name = profile.reviewer.setup_name if profile.reviewer else None
    validator_ref = (
        profile.validator.model_profile.model_ref
        if profile.validator is not None
        else None
    )
    validator_auth_ref = (
        profile.validator.model_profile.auth_ref
        if profile.validator is not None
        else None
    )
    validator_setup_name = profile.validator.setup_name if profile.validator else None

    if guided:
        (
            orchestrator_ref,
            orchestrator_auth_ref,
            orchestrator_setup_name,
        ) = _select_setup_for_agent(
            profile.orchestrator,
            default_model_ref=orchestrator_ref,
            setups=setups,
            step=step,
            total=total_steps,
        )
        step += 1
        architect_ref, architect_auth_ref, architect_setup_name = (
            _select_setup_for_agent(
                profile.architect,
                default_model_ref=architect_ref,
                setups=setups,
                step=step,
                total=total_steps,
            )
        )
        step += 1
        selected_workers = []
        for index, worker in enumerate(profile.workers):
            selected_workers.append(
                _select_setup_for_agent(
                    worker,
                    default_model_ref=worker_refs[index],
                    setups=setups,
                    step=step,
                    total=total_steps,
                )
            )
            step += 1
        worker_refs = [item[0] for item in selected_workers]
        worker_auth_refs = [item[1] for item in selected_workers]
        worker_setup_names = [item[2] for item in selected_workers]
        if profile.reviewer is not None and reviewer_ref is not None:
            reviewer_ref, reviewer_auth_ref, reviewer_setup_name = (
                _select_setup_for_agent(
                    profile.reviewer,
                    default_model_ref=reviewer_ref,
                    setups=setups,
                    step=step,
                    total=total_steps,
                )
            )
            step += 1
        if profile.validator is not None and validator_ref is not None:
            (
                validator_ref,
                validator_auth_ref,
                validator_setup_name,
            ) = _select_setup_for_agent(
                profile.validator,
                default_model_ref=validator_ref,
                setups=setups,
                step=step,
                total=total_steps,
            )
            step += 1
    elif advanced:
        orchestrator_ref = _prompt(orchestrator_ref, "Hermes orchestrator model ref")
        architect_ref = _prompt(architect_ref, "Architect model ref")
        worker_refs = [
            _prompt(worker_refs[index], f"{worker.agent_id} model ref")
            for index, worker in enumerate(profile.workers)
        ]
        reviewer_ref = (
            _prompt(reviewer_ref, "Reviewer model ref")
            if profile.reviewer is not None and reviewer_ref is not None
            else None
        )
        validator_ref = (
            _prompt(validator_ref, "Validator model ref")
            if profile.validator is not None and validator_ref is not None
            else None
        )

    ui.wizard_step(
        step,
        total_steps,
        "Session labels",
        hint="Shown in pane titles and quota reports.",
    )
    budget_label = _prompt(
        profile.budget_label,
        "Budget/session label",
    )
    step += 1
    fallback_policy = _prompt_choice(
        profile.fallback_policy
        if profile.fallback_policy in FALLBACK_POLICIES
        else "prompt-before-swap",
        "Fallback/swap policy",
        FALLBACK_POLICIES,
    )
    step += 1
    default_policy = profile.orchestrator.quota_policy
    if advanced:
        quota_policy = QuotaPolicy(
            warning_threshold_percent=_prompt_float(
                default_policy.warning_threshold_percent,
                "Quota warning threshold percent (warn below this)",
            ),
            pause_threshold_percent=_prompt_float(
                default_policy.pause_threshold_percent,
                "Quota pause threshold percent (stop new work below this)",
            ),
            hard_stop_below_pause=_prompt_bool(
                default_policy.hard_stop_below_pause,
                "Hard stop below pause threshold",
            ),
            manual_token_limit=_prompt_optional_int(
                default_policy.manual_token_limit,
                "Manual token budget limit (blank when provider reports quota)",
            ),
        )
    else:
        quota_policy = default_policy
    workers = tuple(
        _agent_with_model_and_policy(
            worker,
            model_ref=worker_refs[index],
            quota_policy=quota_policy,
            auth_ref=worker_auth_refs[index],
            setup_name=worker_setup_names[index],
        )
        for index, worker in enumerate(profile.workers)
    )
    return SessionProfile(
        name=profile_name,
        project=profile.project,
        created_at=profile.created_at,
        updated_at=time.time(),
        orchestrator=_agent_with_model_and_policy(
            profile.orchestrator,
            model_ref=orchestrator_ref,
            quota_policy=quota_policy,
            auth_ref=orchestrator_auth_ref,
            setup_name=orchestrator_setup_name,
        ),
        architect=_agent_with_model_and_policy(
            profile.architect,
            model_ref=architect_ref,
            quota_policy=quota_policy,
            auth_ref=architect_auth_ref,
            setup_name=architect_setup_name,
        ),
        workers=workers,
        reviewer=_agent_with_model_and_policy(
            profile.reviewer,
            model_ref=reviewer_ref,
            quota_policy=quota_policy,
            auth_ref=reviewer_auth_ref,
            setup_name=reviewer_setup_name,
        )
        if profile.reviewer is not None and reviewer_ref is not None
        else None,
        validator=_agent_with_model_and_policy(
            profile.validator,
            model_ref=validator_ref,
            quota_policy=quota_policy,
            auth_ref=validator_auth_ref,
            setup_name=validator_setup_name,
        )
        if profile.validator is not None and validator_ref is not None
        else None,
        fallback_policy=fallback_policy,
        budget_label=budget_label,
    )


def _default_worker_ref(worker: AgentInstance) -> str:
    if worker.executor_adapter == "codex":
        return "codex:gpt-5.5?reasoning=high"
    if worker.executor_adapter == "antigravity":
        return "antigravity:gemini-3.5-flash-low"
    if worker.executor_adapter == "composer":
        return "composer:grok-composer-2.5-fast"
    return worker.model_profile.model_ref


def cmd_launch(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    profile = _build_launch_profile(profile, name=args.profile, preset_name=args.preset)
    save_session_profile(files.project, profile)
    hermes_profile = generate_hermes_profile(files.project, profile)
    advance_workflow(
        files.project.runtime_directory,
        "GOAL_RECEIVED",
        payload={"session_profile": profile.name},
    )
    name = create_session(
        files.project,
        attach=not args.no_attach,
        profile=profile,
        hermes_profile=hermes_profile,
    )
    attach_hint = (
        f"Attach with: [bold]warden attach[/] or [bold]tmux attach -t {name}[/]"
    )
    if not args.no_attach:
        attach_hint = ""
    ui.launch_summary(
        session=name,
        profile_path=str(
            files.project.runtime_directory / "state" / "session-profile.json"
        ),
        hermes_home=str(hermes_profile.home),
        attach_hint=attach_hint,
    )
    return 0


def cmd_attach(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    name = session_name(files.project)
    subprocess.run(["tmux", "attach-session", "-t", name], check=False)
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    stopped = stop_session(files.project)
    print("Stopped." if stopped else "RailWarden session is not running.")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    pane_rows = panes(files.project)
    _section("RailWarden status")
    print(f"Session: {session_name(files.project)}")
    print(f"Runtime: {files.project.runtime_directory}")
    print()
    _section("Tmux panes")
    _table(
        ["Pane", "Title", "PID", "Dead"],
        [[row["pane"], row["title"], row["pid"], row["dead"]] for row in pane_rows],
    )
    return 0


def cmd_observability(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    workflow = load_workflow(files.project.runtime_directory)
    profile = load_session_profile(files.project)
    print(render_dashboard(files), end="")
    print()
    _section("Workflow")
    print(f"State: {workflow.node}")
    print(f"Updated: {time.ctime(workflow.updated_at)}")
    print()
    _section("Agents and quotas")
    _table(
        ["Agent", "Role", "State", "Model", "Remaining", "Confidence"],
        [
            [
                agent.agent_id,
                agent.role,
                agent.state,
                agent.model_profile.model_ref,
                load_quota(files.project.runtime_directory, agent).remaining_percent,
                load_quota(files.project.runtime_directory, agent).confidence,
            ]
            for agent in profile.agents
        ],
    )
    print()
    _section("Tmux")
    _table(
        ["Pane", "Title", "Dead"],
        [[row["pane"], row["title"], row["dead"]] for row in panes(files.project)],
    )
    return 0


def cmd_dashboard(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(render_dashboard(files), end="")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    events = read_events(files.project.runtime_directory)
    cursor = int(args.cursor or 0)
    if cursor:
        events = events[cursor:]
    if args.limit is not None:
        events = events[-args.limit :]
    print(json.dumps({"cursor": cursor + len(events), "events": events}, indent=2))
    return 0


def cmd_snapshot(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    payload = {
        "project": files.project.__dict__,
        "tasks": load_tasks(files.project.runtime_directory),
        "events": read_events(files.project.runtime_directory, limit=50),
        "context": context_status(files.project, files.packages),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def cmd_failure_inspect(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(
        json.dumps(
            inspect_failure(files.project.runtime_directory, args.task_id),
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def cmd_result_normalize(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    task, package = _task_and_package(files, args.task_id)
    payload = normalize_result(files.project, task=task, package=package)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def cmd_context_status(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(
        json.dumps(
            context_status(files.project, files.packages),
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def cmd_context_write(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    content = args.content or ""
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    payload = write_context_file(files.project, args.file, content)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def cmd_decision_record(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    payload = json.loads(args.payload)
    if not isinstance(payload, dict):
        raise RailWardenError("Decision payload must be a JSON object")
    decision = record_decision(
        files.project.runtime_directory,
        observed_event=_mapping_or_empty(payload.get("observed_event")),
        diagnosis=str(payload.get("diagnosis", "")),
        allowed_actions=[str(item) for item in payload.get("allowed_actions", [])],
        chosen_action=str(payload.get("chosen_action", "")),
        rationale=str(payload.get("rationale", "")),
        tool_call=_mapping_or_empty(payload.get("tool_call")),
        result=_mapping_or_empty(payload.get("result")),
    )
    print(json.dumps(decision, indent=2, sort_keys=True, default=str))
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    adapters = default_adapters()
    planner = AntigravityClaudePlanner(files.project.planner_model).doctor()
    payload = doctor_report(files.project, adapters=adapters)
    payload["planning_architect"] = planner.__dict__
    payload["coordination"]["agent"] = "Hermes"
    payload["coordination"]["mailbox"] = str(
        files.project.runtime_directory / "state" / "hermes-mailbox.jsonl"
    )
    _print_doctor(payload)
    return 0


def cmd_config(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print(json.dumps(files.project.__dict__, indent=2, sort_keys=True, default=str))
    return 0


def _source_checkout_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _git_toplevel(path: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RailWardenError(
            "warden update requires RailWarden to be installed from a Git checkout. "
            f"git error: {completed.stderr.strip()}"
        )
    return Path(completed.stdout.strip()).resolve()


def cmd_update(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve() if args.source else _source_checkout_root()
    checkout = _git_toplevel(source)
    print(f"Updating RailWarden from: {checkout}")
    pull = subprocess.run(
        ["git", "-C", str(checkout), "pull", "--ff-only"],
        text=True,
        capture_output=True,
        check=False,
    )
    if pull.returncode != 0:
        raise RailWardenError(
            f"git pull failed: {pull.stderr.strip() or pull.stdout.strip()}"
        )
    if pull.stdout.strip():
        print(pull.stdout.strip())
    install = subprocess.run(
        ["uv", "tool", "install", "--editable", str(checkout), "--force"],
        text=True,
        capture_output=True,
        check=False,
    )
    if install.returncode != 0:
        raise RailWardenError(
            f"uv tool install failed: {install.stderr.strip() or install.stdout.strip()}"
        )
    if install.stdout.strip():
        print(install.stdout.strip())
    print(f"RailWarden updated to {__version__}.")
    return 0


def cmd_logs(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    log_dir = files.project.runtime_directory / "logs"
    print(log_dir)
    return 0


def cmd_controller(args: argparse.Namespace) -> int:
    root = discover_repo(Path.cwd())
    while True:
        files = load_project_files(root)
        result = controller_tick(
            files,
            launch=not args.no_launch,
            integrate=not args.no_integrate,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.once:
            return 0
        time.sleep(args.interval)


def cmd_handoff(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    tasks = load_tasks(files.project.runtime_directory)
    for task in tasks:
        if (
            str(task.get("id")) == args.task_id
            or str(task.get("package_id")) == args.task_id
        ):
            payload: dict[str, object] = {"manual": True}
            if args.provider:
                payload["provider_override"] = args.provider
            transition_task(
                files.project.runtime_directory, task, "handoff_needed", payload
            )
            print(f"Marked {task.get('id')} for handoff.")
            return 0
    raise RailWardenError(f"Unknown task: {args.task_id}")


def _task_and_package(files: Any, identifier: str) -> tuple[dict[str, Any], Any]:
    for task in load_tasks(files.project.runtime_directory):
        if (
            str(task.get("id")) == identifier
            or str(task.get("package_id")) == identifier
        ):
            package = files.packages.get(str(task.get("package_id", "")))
            if package is None:
                raise RailWardenError(f"Package is not loaded for task: {identifier}")
            return task, package
    raise RailWardenError(f"Unknown task or package: {identifier}")


def cmd_inspect(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    task, package = _task_and_package(files, args.identifier)
    print(
        json.dumps(
            {"task": task, "package": package.__dict__},
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    task, _package = _task_and_package(files, args.identifier)
    updated = transition_task(files.project.runtime_directory, task, "ready")
    print(f"Marked {updated.get('id')} ready.")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    task, _package = _task_and_package(files, args.identifier)
    updated = transition_task(
        files.project.runtime_directory,
        task,
        "rejected",
        {"reason": args.reason or "manual rejection"},
    )
    print(f"Rejected {updated.get('id')}.")
    return 0


def cmd_approve_merge(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    task, _package = _task_and_package(files, args.identifier)
    updated = transition_task(
        files.project.runtime_directory,
        task,
        "merge_approved",
        {"merge_approved_by": "human", "merge_approved_at": time.time()},
    )
    print(f"Approved merge for {updated.get('id')}.")
    return 0


def cmd_approve_contracts(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    payload = approve_latest_plan(files.project)
    print(f"Approved contracts: {payload['run_id']}")
    return 0


def cmd_abort_goal(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    reason = args.reason or "manual abort"
    for task in load_tasks(files.project.runtime_directory):
        status = str(task.get("status", ""))
        if status not in {"merged", "blocked", "failed", "rejected"}:
            transition_task(
                files.project.runtime_directory,
                task,
                "blocked",
                {"goal_aborted": True, "reason": reason},
            )
    advance_workflow(files.project.runtime_directory, "RECOVERY_OR_SWAP")
    print(f"Aborted active goal: {reason}")
    return 0


def cmd_validate_package(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    task, package = _task_and_package(files, args.identifier)
    workspace = Path(
        str(task.get("worktree", package_worktree(files.project, package)))
    )
    commit_hash = str(task.get("commit_hash") or head(workspace))
    evidence = run_package_validation(files.project, package, workspace, commit_hash)
    transition_task(
        files.project.runtime_directory,
        task,
        "validated" if evidence["status"] == "passed" else "blocked",
        {"package_validation": evidence},
    )
    print(json.dumps(evidence, indent=2, sort_keys=True, default=str))
    return 0


def cmd_review_package(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    task, package = _task_and_package(files, args.identifier)
    workspace = Path(
        str(task.get("worktree", package_worktree(files.project, package)))
    )
    commit_hash = str(task.get("commit_hash") or head(workspace))
    changed = changed_files_in_commit(workspace, commit_hash)
    validation = task.get("package_validation")
    if not isinstance(validation, dict):
        validation = run_package_validation(
            files.project, package, workspace, commit_hash
        )
    review = run_package_review(
        files.project,
        package,
        task=task,
        worker_provider=str(task.get("provider", "")),
        reviewer_provider=args.reviewer,
        changed_files=changed,
        validation_evidence=validation,
    )
    transition_task(
        files.project.runtime_directory,
        task,
        "review_passed" if review["status"] == "passed" else "blocked",
        {"review": review},
    )
    print(json.dumps(review, indent=2, sort_keys=True, default=str))
    return 0


def cmd_release_review(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    tasks = load_tasks(files.project.runtime_directory)
    incomplete = [
        task for task in tasks if str(task.get("status")) not in {"merged", "rejected"}
    ]
    status = "passed" if not incomplete else "blocked"
    payload = {
        "status": status,
        "total_tasks": len(tasks),
        "incomplete": [task.get("id") for task in incomplete],
    }
    if status == "passed":
        advance_workflow(files.project.runtime_directory, "RELEASE_REVIEW")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_model_list(_args: argparse.Namespace) -> int:
    models = list_models()
    antigravity = AntigravityClaudePlanner().doctor()
    for model in antigravity.available_models:
        ref = f"antigravity:{_model_slug(model)}"
        if ref not in {item["ref"] for item in models}:
            models.append(
                {
                    "ref": ref,
                    "role_hint": "coder/planner",
                    "transport": "cli",
                    "notes": f"Discovered by agy models: {model}",
                }
            )
    _section("Available model refs")
    _table(
        ["Model ref", "Role", "Transport", "Notes"],
        [
            [item["ref"], item["role_hint"], item["transport"], item["notes"]]
            for item in models
        ],
    )
    return 0


def _model_slug(model: str) -> str:
    return (
        model.lower()
        .replace("(", "")
        .replace(")", "")
        .replace("/", "-")
        .replace(" ", "-")
        .replace("--", "-")
    )


def cmd_model_doctor(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    refs = [agent.model_profile.model_ref for agent in profile.agents]
    _section("Configured model refs")
    _table(
        ["Ref", "Status", "Provider", "Model", "Base URL"],
        [
            [
                item["ref"],
                _status(item["status"]),
                item.get("provider", "-"),
                item.get("model", "-"),
                item.get("base_url", "-"),
            ]
            for item in validate_model_refs(refs)
        ],
    )
    return 0


def cmd_model_configure(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    if args.model_ref:
        parse_model_ref(args.model_ref)
    path = ensure_runtime_secrets_file(files.project.runtime_directory)
    print("Model configuration")
    print()
    print(f"Validated model ref: {args.model_ref or 'none supplied'}")
    print(f"Runtime secrets file: {path}")
    print()
    print(
        "Put provider API keys in your shell environment or this ignored runtime file."
    )
    print(
        "RailWarden stores env references such as env:OPENAI_API_KEY, not raw secrets."
    )
    return 0


def _find_agent(profile: SessionProfile, agent_id: str) -> AgentInstance:
    for agent in profile.agents:
        if agent.agent_id == agent_id:
            return agent
    raise RailWardenError(f"Unknown agent: {agent_id}")


def _current_goal(runtime_dir: Path) -> str:
    path = runtime_dir / "state" / "pending-plan.json"
    if not path.exists():
        return ""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("goal", "")) if isinstance(payload, dict) else ""


def _active_task_for_agent(
    tasks: list[dict[str, Any]], agent: AgentInstance
) -> dict[str, Any] | None:
    if agent.active_task:
        for task in tasks:
            if str(task.get("id")) == agent.active_task:
                return task
    active_statuses = {
        "assigned",
        "running",
        "handoff_needed",
        "cooldown_wait",
        "validating",
    }
    for task in tasks:
        if (
            str(task.get("provider")) == agent.executor_adapter
            and str(task.get("status")) in active_statuses
        ):
            return task
    return None


def _create_swap_handoff(
    files: Any,
    task: dict[str, Any],
    agent: AgentInstance,
    *,
    target_provider: str,
) -> dict[str, Any]:
    package = files.packages.get(str(task.get("package_id", "")))
    if package is None:
        raise RailWardenError(f"Task package is not available: {task.get('id')}")
    workspace = Path(
        str(task.get("worktree", package_worktree(files.project, package)))
    )
    branch = str(task.get("branch", package_branch(package)))
    checkpoint: dict[str, object] | None = None
    if workspace.exists():
        try:
            checkpoint = create_checkpoint_commit(
                files.project,
                task_id=str(task["id"]),
                workspace=workspace,
                attempt=int(task.get("attempt", 0)),
                allowed_paths=package.owned_paths,
            ).to_dict()
        except RailWardenError:
            checkpoint = None
    packet = create_handoff_packet(
        runtime_dir=files.project.runtime_directory,
        task=task,
        goal=_current_goal(files.project.runtime_directory),
        objective=package.objective,
        workspace=workspace,
        branch=branch,
        provider=agent.executor_adapter,
        failure_kind="agent_swap",
        log_path=Path(str(task["log_path"])) if task.get("log_path") else None,
        next_provider=target_provider,
    )
    payload: dict[str, Any] = {
        "handoff_packet": str(packet),
        "last_provider": agent.executor_adapter,
        "provider_override": target_provider,
        "failure_kind": "agent_swap",
    }
    if checkpoint is not None:
        payload["checkpoint"] = checkpoint
    return transition_task(
        files.project.runtime_directory,
        task,
        "handoff_needed",
        payload,
    )


def cmd_agent_list(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    _section("Agents")
    _table(
        ["Agent", "Role", "State", "Executor", "Model", "Active task"],
        [
            [
                agent.agent_id,
                agent.role,
                agent.state,
                agent.executor_adapter,
                agent.model_profile.model_ref,
                agent.active_task,
            ]
            for agent in profile.agents
        ],
    )
    return 0


def cmd_agent_swap(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    agent = _find_agent(profile, args.agent_id)
    model_profile = model_profile_from_ref(args.to)
    target_provider = (
        model_profile.provider
        if model_profile.provider in files.project.worker_providers
        else agent.executor_adapter
    )
    tasks = load_tasks(files.project.runtime_directory)
    active_task = _active_task_for_agent(tasks, agent)
    updated_task = (
        _create_swap_handoff(
            files,
            active_task,
            agent,
            target_provider=target_provider,
        )
        if active_task is not None
        else None
    )
    updated = AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=model_profile,
        executor_adapter=target_provider,
        state="handoff_needed" if updated_task is not None else "ready",
        quota_policy=agent.quota_policy,
        active_task=str(updated_task["id"]) if updated_task is not None else None,
    )
    save_session_profile(files.project, update_agent(profile, updated))
    advance_workflow(
        files.project.runtime_directory,
        "RECOVERY_OR_SWAP",
        payload={"agent_id": args.agent_id, "to": args.to},
    )
    print(
        json.dumps(
            {
                "agent_id": updated.agent_id,
                "model_ref": updated.model_profile.model_ref,
                "executor_adapter": updated.executor_adapter,
                "task_id": updated_task.get("id") if updated_task else None,
                "handoff_packet": updated_task.get("handoff_packet")
                if updated_task
                else None,
            },
            indent=2,
        )
    )
    return 0


def cmd_agent_state(args: argparse.Namespace, state: str) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    agent = _find_agent(profile, args.agent_id)
    updated = AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=agent.model_profile,
        executor_adapter=agent.executor_adapter,
        state=state,  # type: ignore[arg-type]
        quota_policy=agent.quota_policy,
        active_task=agent.active_task,
    )
    save_session_profile(files.project, update_agent(profile, updated))
    print(json.dumps({"agent_id": updated.agent_id, "state": updated.state}, indent=2))
    return 0


def cmd_quota_status(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    _section("Quota status")
    _table(
        ["Agent", "Provider", "Model", "Used", "Limit", "Remaining", "Confidence"],
        [
            [
                agent.agent_id,
                quota.provider,
                quota.model,
                quota.used_tokens,
                quota.limit_tokens,
                quota.remaining_percent,
                quota.confidence,
            ]
            for agent in profile.agents
            for quota in [load_quota(files.project.runtime_directory, agent)]
        ],
    )
    return 0


def cmd_quota_set(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    profile = load_session_profile(files.project)
    agent = _find_agent(profile, args.agent_id)
    quota = update_usage(
        files.project.runtime_directory,
        agent,
        remaining_percent=args.remaining_percent,
        confidence="manual",
    )
    print(f"Updated quota for {args.agent_id}")
    print(f"Provider/model: {quota.provider}:{quota.model}")
    print(f"Remaining: {quota.remaining_percent:g}% ({quota.confidence})")
    return 0


def cmd_checkpoint_create(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    tasks = load_tasks(files.project.runtime_directory)
    for task in tasks:
        if (
            str(task.get("id")) != args.task_id
            and str(task.get("package_id")) != args.task_id
        ):
            continue
        package = files.packages[str(task["package_id"])]
        workspace = Path(
            str(task.get("worktree", package_worktree(files.project, package)))
        )
        result = create_checkpoint_commit(
            files.project,
            task_id=str(task["id"]),
            workspace=workspace,
            attempt=int(task.get("attempt", 0)),
            allowed_paths=package.owned_paths,
        )
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0
    raise RailWardenError(f"Unknown task: {args.task_id}")


def cmd_worker(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    provider = args.provider
    print(f"RailWarden worker adapter pane ready: {provider}")
    print("Waiting for Hermes directives. Press Ctrl-C to stop this pane.")
    seen = 0
    try:
        while True:
            all_messages = read_messages(files.project.runtime_directory)
            for message in messages_for(
                files.project.runtime_directory,
                provider,
                after=seen,
            ):
                print(
                    f"[Hermes -> {provider}] {message.get('body', '')}",
                    flush=True,
                )
            seen = len(all_messages)
            time.sleep(2)
    except KeyboardInterrupt:
        return 0


def _print_hermes_status(payload: dict[str, object]) -> None:
    _section("Hermes projection")
    print(f"Current board: {payload.get('current_board')}")
    print(f"Project slug: {payload.get('project_slug')}")
    print(
        "Update available: "
        + ("yes" if payload.get("update_available") else "no or unknown")
    )
    print()
    for key, title in (
        ("version", "Hermes version"),
        ("gateway", "Gateway"),
        ("boards", "Boards"),
        ("projects", "Projects"),
        ("profiles", "Profiles"),
        ("diagnostics", "Kanban diagnostics"),
    ):
        _section(title)
        text = str(payload.get(key, "")).strip() or "none"
        for line in text.splitlines():
            print(line)
        print()


def cmd_hermes_status(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    payload = hermes_status(files.project, HermesAdapter())
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _print_hermes_status(payload)
    return 0


def cmd_hermes_bootstrap(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    plan = bootstrap_plan(files.project)
    if args.dry_run:
        result_plan = plan
    else:
        result_plan = apply_bootstrap(files.project, HermesAdapter())
    payload = result_plan.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0
    _section("Hermes bootstrap")
    print(f"Board: {result_plan.board}")
    print(f"Project: {result_plan.project_slug}")
    print(f"Repository: {result_plan.repository}")
    print()
    _section("Actions")
    for action in result_plan.actions:
        print(f"- {action}")
    external = result_plan.external_lanes
    if external:
        print()
        _section("External lane note")
        print(
            "These assignees are not valid Hermes profile names and require "
            "explicit external lane/plugin support:"
        )
        for lane in external:
            print(f"- {lane}")
    return 0


def cmd_hermes_import(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    plan = build_import_plan(files)
    if not args.apply:
        payload = plan.to_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
            return 0
        _section("Hermes Kanban import dry-run")
        print(f"Board: {plan.board}")
        print(f"Project: {plan.project_slug}")
        print()
        _section("Tasks")
        for task in plan.tasks:
            print(
                f"- {task.package_id}: {task.title} "
                f"@{task.assignee or 'unassigned'} "
                f"branch={task.branch} workspace={task.workspace}"
            )
        print()
        _section("Links")
        for link in plan.links:
            print(f"- {link.parent_package_id} -> {link.child_package_id}")
        if not plan.tasks:
            print("No work packages found.")
        return 0
    payload = apply_import_plan(plan, HermesAdapter())
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        _section("Hermes Kanban import applied")
        _table(
            ["Package", "Task"],
            [
                [row.get("package_id"), row.get("task_id")]
                for row in _list_of_dicts(payload.get("created"))
            ],
        )
        print()
        _section("Links")
        _table(
            ["Parent", "Child"],
            [
                [row.get("parent"), row.get("child")]
                for row in _list_of_dicts(payload.get("linked"))
            ],
        )
    return 0


def cmd_console(_args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    print("RailWarden Planning Console.")
    print(
        "Commands: goal <text>, approve plan, status, dag, agents, tasks, logs, pause, resume, stop-after-current, quit"
    )
    print(
        "Route messages with `codex: ...`, `gemini: ...`, `composer: ...`, or `broadcast: ...`."
    )
    while True:
        try:
            line = input("warden> ").strip()
        except EOFError:
            return 0
        if line in {"quit", "exit"}:
            return 0
        if line.startswith("goal "):
            pending = create_pending_plan(
                files.project, line.removeprefix("goal ").strip()
            )
            print(f"Created pending plan {pending.run_id}.")
            print()
            print(pending.plan_markdown)
            print("Work packages:")
            for package in pending.work_packages:
                print(
                    f"- {package['id']} {package.get('name', '')} "
                    f"deps={package.get('dependencies', [])} "
                    f"risk={package.get('risk_level', 'medium')}"
                )
            print()
            print("Type `approved` or `approve plan` to freeze contracts and execute.")
        elif line in {"approved", "approve plan", "approve contracts"}:
            payload = approve_latest_plan(files.project)
            print(f"Approved plan {payload['run_id']}.")
        elif line == "reject plan":
            path = files.project.runtime_directory / "state" / "pending-plan.json"
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload["approved"] = False
                    payload["rejected"] = True
                    payload["rejected_at"] = time.time()
                    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print("Rejected pending plan.")
        elif line in {"status", "plan", "dag", "agents", "tasks"}:
            cmd_plan(argparse.Namespace())
        elif line == "logs":
            cmd_logs(argparse.Namespace())
        elif line in {"pause", "resume", "stop-after-current"}:
            append_message(
                files.project.runtime_directory,
                sender="hermes",
                recipient="broadcast",
                body=line,
            )
            print(f"Recorded {line}.")
        elif line.startswith("handoff "):
            parts = line.split()
            cmd_handoff(
                argparse.Namespace(
                    task_id=parts[1], provider=parts[2] if len(parts) > 2 else None
                )
            )
        elif line.startswith("inspect "):
            cmd_inspect(argparse.Namespace(identifier=line.split()[1]))
        elif line.startswith("review "):
            cmd_review_package(
                argparse.Namespace(identifier=line.split()[1], reviewer=None)
            )
        elif line.startswith("retry ") or line.startswith("unblock "):
            task_id = line.split()[1]
            cmd_retry(argparse.Namespace(identifier=task_id))
        elif line.startswith("reject "):
            cmd_reject(argparse.Namespace(identifier=line.split()[1], reason=None))
        elif line.startswith("approve-merge "):
            cmd_approve_merge(argparse.Namespace(identifier=line.split()[1]))
        elif line == "abort-goal":
            cmd_abort_goal(argparse.Namespace(reason=None))
        elif line.startswith("block "):
            task_id = line.split()[1]
            tasks = load_tasks(files.project.runtime_directory)
            for task in tasks:
                if (
                    str(task.get("id")) == task_id
                    or str(task.get("package_id")) == task_id
                ):
                    transition_task(
                        files.project.runtime_directory,
                        task,
                        "blocked",
                        {"manual": True},
                    )
                    print(f"Blocked {task.get('id')}.")
                    break
        elif directive := parse_directive(line):
            recipient, body = directive
            append_message(
                files.project.runtime_directory,
                sender="hermes",
                recipient=recipient,
                body=body,
            )
            print(f"Sent to {recipient}.")
        elif line:
            print(f"Recorded coordinator instruction: {line}")


def _choose_supervisor_action(event: dict[str, Any]) -> tuple[str, str]:
    payload = _mapping_or_empty(event.get("payload"))
    failure_kind = str(payload.get("failure_kind", ""))
    allowed = {str(item) for item in payload.get("allowed_actions", [])}
    if (
        failure_kind == "missing_worker_result_with_commit"
        and "normalize_result" in allowed
    ):
        return (
            "normalize_result",
            "clean committed work can be normalized by RailWarden",
        )
    if failure_kind in {"quota_exhausted", "rate_limited", "provider_quota_exhausted"}:
        return "handoff_provider", "provider quota is exhausted or rate limited"
    if failure_kind in {"authentication", "provider_auth_required"}:
        return "handoff_provider", "provider requires authentication"
    if failure_kind == "wrapper_quoting_failure":
        return "handoff_provider", "adapter wrapper failed before worker execution"
    if "retry_same_provider" in allowed:
        return "retry_same_provider", "default retry is permitted"
    if "ask_user" in allowed:
        return "ask_user", "no deterministic recovery action is available"
    return "ask_user", "no allowed recovery action matched"


def _supervisor_seen_path(runtime_dir: Path) -> Path:
    return runtime_dir / "supervisor" / "state.json"


def _load_supervisor_cursor(runtime_dir: Path) -> int:
    path = _supervisor_seen_path(runtime_dir)
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    return int(payload.get("cursor", 0)) if isinstance(payload, dict) else 0


def _save_supervisor_cursor(runtime_dir: Path, cursor: int) -> None:
    path = _supervisor_seen_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cursor": cursor, "updated_at": time.time()}, indent=2) + "\n",
        encoding="utf-8",
    )


def cmd_hermes_supervisor(args: argparse.Namespace) -> int:
    root = discover_repo(Path.cwd())
    while True:
        files = load_project_files(root)
        events = read_events(files.project.runtime_directory)
        cursor = _load_supervisor_cursor(files.project.runtime_directory)
        for index, event in enumerate(events[cursor:], start=cursor):
            if event.get("type") != "decision_required":
                continue
            task_id = str(event.get("task_id") or "")
            try:
                task, package = _task_and_package(files, task_id)
            except RailWardenError:
                continue
            if str(task.get("status")) != "decision_required":
                continue
            action, rationale = _choose_supervisor_action(event)
            result: dict[str, Any]
            if action == "normalize_result":
                result = normalize_result(files.project, task=task, package=package)
                transition_task(
                    files.project.runtime_directory,
                    task,
                    "validating",
                    {"runtime_result_path": result["path"]},
                )
            elif action == "handoff_provider":
                result = transition_task(
                    files.project.runtime_directory,
                    task,
                    "handoff_needed",
                    {"supervisor_action": action},
                )
            elif action == "retry_same_provider":
                result = transition_task(
                    files.project.runtime_directory,
                    task,
                    "ready",
                    {"supervisor_action": action},
                )
            else:
                result = transition_task(
                    files.project.runtime_directory,
                    task,
                    "blocked",
                    {"supervisor_action": action, "reason": rationale},
                )
            payload = _mapping_or_empty(event.get("payload"))
            record_decision(
                files.project.runtime_directory,
                observed_event=event,
                diagnosis=str(payload.get("failure_kind", "decision_required")),
                allowed_actions=[
                    str(item) for item in payload.get("allowed_actions", [])
                ],
                chosen_action=action,
                rationale=rationale,
                tool_call={"name": f"railwarden.{action}", "task_id": task_id},
                result=result,
            )
            _save_supervisor_cursor(files.project.runtime_directory, index + 1)
        if args.once:
            return 0
        _save_supervisor_cursor(files.project.runtime_directory, len(events))
        time.sleep(args.interval)


def cmd_integrate(args: argparse.Namespace) -> int:
    _, files = configured_project(Path.cwd())
    states = classify_packages(files.project, files.packages)
    plan = execution_plan(files.project, states)
    queue = plan.get("integration_queue", [])
    if not isinstance(queue, list) or not queue:
        print("Integration queue is empty.")
        return 0
    print(
        json.dumps(
            integrate_one(
                config=files.project,
                candidate=queue[0],
                validation_commands=files.validation,
                execute=args.execute,
            ),
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    if args.mcp_command != "serve":
        raise RailWardenError("Only `warden mcp serve` is supported")
    return serve_mcp(Path.cwd())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="warden")
    parser.add_argument("--version", action="store_true", help="Show version and exit.")
    sub = parser.add_subparsers(dest="command")
    init = sub.add_parser("init")
    init.add_argument("--yes", action="store_true")
    init.set_defaults(func=cmd_init)
    setup = sub.add_parser("setup")
    setup.add_argument("--yes", action="store_true")
    setup.set_defaults(func=cmd_setup)
    adopt = sub.add_parser("adopt")
    adopt.add_argument("repository", nargs="?")
    adopt.add_argument("--dry-run", action="store_true")
    adopt.add_argument("--source")
    adopt.add_argument("--report", default="artifacts/tmom-adoption-dry-run.md")
    adopt.set_defaults(func=cmd_adopt)
    for name, func in [
        ("plan", cmd_plan),
        ("replan", cmd_plan),
        ("status", cmd_status),
        ("dashboard", cmd_dashboard),
        ("observability", cmd_observability),
        ("observe", cmd_observability),
        ("snapshot", cmd_snapshot),
        ("logs", cmd_logs),
        ("doctor", cmd_doctor),
        ("config", cmd_config),
    ]:
        item = sub.add_parser(name)
        item.set_defaults(func=func)
    update = sub.add_parser("update")
    update.add_argument(
        "--source",
        help="Override the RailWarden source checkout to pull and install.",
    )
    update.set_defaults(func=cmd_update)
    run = sub.add_parser("run")
    run.add_argument("goal")
    run.set_defaults(func=cmd_run)
    planning_worker = sub.add_parser(
        "planning-worker",
        help=argparse.SUPPRESS,
    )
    planning_worker.add_argument("--run-id", required=True)
    planning_worker.set_defaults(func=cmd_planning_worker)
    approve = sub.add_parser("approve")
    approve.add_argument("target")
    approve.set_defaults(func=cmd_approve)
    events = sub.add_parser("events")
    events.add_argument("--limit", type=int, default=50)
    events.add_argument("--cursor", type=int, default=0)
    events.set_defaults(func=cmd_events)
    console = sub.add_parser("console", help="Open the RailWarden planning console")
    console.set_defaults(func=cmd_console)
    start = sub.add_parser("start", help="Legacy tmux session start.")
    start.add_argument("--no-attach", action="store_true")
    start.set_defaults(func=cmd_start)
    launch = sub.add_parser(
        "launch",
        help="Start or attach to the local RailWarden evented runtime.",
    )
    launch.add_argument("--profile")
    launch.add_argument("--preset", choices=sorted(LAUNCH_PRESETS))
    launch.add_argument("--no-attach", action="store_true")
    launch.set_defaults(func=cmd_launch)
    sub.add_parser("attach").set_defaults(func=cmd_attach)
    sub.add_parser("stop").set_defaults(func=cmd_stop)
    restart = sub.add_parser(
        "restart",
        help="Stop and recreate the RailWarden tmux session (prefers v2 Hermes layout).",
    )
    restart.add_argument("--no-attach", action="store_true")
    restart.set_defaults(func=cmd_restart)
    integrate = sub.add_parser("integrate")
    integrate.add_argument("--execute", action="store_true")
    integrate.set_defaults(func=cmd_integrate)
    controller = sub.add_parser(
        "controller",
        help="Run one or more RailWarden controller ticks over canonical runtime state.",
    )
    controller.add_argument("--once", action="store_true")
    controller.add_argument("--no-launch", action="store_true")
    controller.add_argument("--no-integrate", action="store_true")
    controller.add_argument("--interval", type=float, default=5.0)
    controller.set_defaults(func=cmd_controller)
    handoff = sub.add_parser("handoff")
    handoff.add_argument("task_id")
    handoff.add_argument("provider", nargs="?")
    handoff.set_defaults(func=cmd_handoff)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("identifier")
    inspect.set_defaults(func=cmd_inspect)
    retry = sub.add_parser("retry")
    retry.add_argument("identifier")
    retry.set_defaults(func=cmd_retry)
    reject = sub.add_parser("reject")
    reject.add_argument("identifier")
    reject.add_argument("--reason")
    reject.set_defaults(func=cmd_reject)
    approve_merge = sub.add_parser("approve-merge")
    approve_merge.add_argument("identifier")
    approve_merge.set_defaults(func=cmd_approve_merge)
    sub.add_parser("approve-contracts").set_defaults(func=cmd_approve_contracts)
    abort_goal = sub.add_parser("abort-goal")
    abort_goal.add_argument("--reason")
    abort_goal.set_defaults(func=cmd_abort_goal)
    validate = sub.add_parser("validate")
    validate.add_argument("identifier")
    validate.set_defaults(func=cmd_validate_package)
    review = sub.add_parser("review")
    review.add_argument("identifier")
    review.add_argument("--reviewer")
    review.set_defaults(func=cmd_review_package)
    sub.add_parser("release-review").set_defaults(func=cmd_release_review)
    worker = sub.add_parser("worker")
    worker.add_argument("provider")
    worker.set_defaults(func=cmd_worker)
    model = sub.add_parser("model")
    model_sub = model.add_subparsers(dest="model_command", required=True)
    model_sub.add_parser("list").set_defaults(func=cmd_model_list)
    model_sub.add_parser("doctor").set_defaults(func=cmd_model_doctor)
    configure = model_sub.add_parser("configure")
    configure.add_argument("model_ref", nargs="?")
    configure.set_defaults(func=cmd_model_configure)
    agent = sub.add_parser("agent")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_sub.add_parser("list").set_defaults(func=cmd_agent_list)
    swap = agent_sub.add_parser("swap")
    swap.add_argument("agent_id")
    swap.add_argument("--to", required=True)
    swap.set_defaults(func=cmd_agent_swap)
    pause = agent_sub.add_parser("pause")
    pause.add_argument("agent_id")
    pause.set_defaults(func=lambda args: cmd_agent_state(args, "paused"))
    resume = agent_sub.add_parser("resume")
    resume.add_argument("agent_id")
    resume.set_defaults(func=lambda args: cmd_agent_state(args, "ready"))
    quota = sub.add_parser("quota")
    quota_sub = quota.add_subparsers(dest="quota_command", required=True)
    quota_sub.add_parser("status").set_defaults(func=cmd_quota_status)
    quota_set = quota_sub.add_parser("set")
    quota_set.add_argument("agent_id")
    quota_set.add_argument("--remaining-percent", type=float, required=True)
    quota_set.set_defaults(func=cmd_quota_set)
    checkpoint = sub.add_parser("checkpoint")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_create = checkpoint_sub.add_parser("create")
    checkpoint_create.add_argument("task_id")
    checkpoint_create.set_defaults(func=cmd_checkpoint_create)
    result = sub.add_parser("result")
    result_sub = result.add_subparsers(dest="result_command", required=True)
    result_normalize = result_sub.add_parser("normalize")
    result_normalize.add_argument("task_id")
    result_normalize.set_defaults(func=cmd_result_normalize)
    context = sub.add_parser("context")
    context_sub = context.add_subparsers(dest="context_command", required=True)
    context_sub.add_parser("status").set_defaults(func=cmd_context_status)
    context_write = context_sub.add_parser("write")
    context_write.add_argument("file")
    context_write.add_argument("content", nargs="?")
    context_write.add_argument("--content-file")
    context_write.set_defaults(func=cmd_context_write)
    failure = sub.add_parser("failure")
    failure_sub = failure.add_subparsers(dest="failure_command", required=True)
    failure_inspect = failure_sub.add_parser("inspect")
    failure_inspect.add_argument("task_id")
    failure_inspect.set_defaults(func=cmd_failure_inspect)
    decision = sub.add_parser("decision")
    decision_sub = decision.add_subparsers(dest="decision_command", required=True)
    decision_record = decision_sub.add_parser("record")
    decision_record.add_argument("payload")
    decision_record.set_defaults(func=cmd_decision_record)
    mcp = sub.add_parser("mcp")
    mcp.add_argument("mcp_command", choices=["serve"])
    mcp.set_defaults(func=cmd_mcp)
    hermes = sub.add_parser(
        "hermes", help="Hermes supervisor and Kanban projection commands."
    )
    # Removed backwards-compatible 'hermes console' default because it's replaced by top-level console
    hermes_sub = hermes.add_subparsers(dest="hermes_command")
    hermes_status_parser = hermes_sub.add_parser("status")
    hermes_status_parser.add_argument("--json", action="store_true")
    hermes_status_parser.set_defaults(func=cmd_hermes_status)
    hermes_bootstrap = hermes_sub.add_parser("bootstrap")
    hermes_bootstrap.add_argument("--dry-run", action="store_true", default=False)
    hermes_bootstrap.add_argument("--json", action="store_true")
    hermes_bootstrap.set_defaults(func=cmd_hermes_bootstrap)
    hermes_import = hermes_sub.add_parser("import")
    import_mode = hermes_import.add_mutually_exclusive_group()
    import_mode.add_argument("--dry-run", action="store_true")
    import_mode.add_argument("--apply", action="store_true")
    hermes_import.add_argument("--json", action="store_true")
    hermes_import.set_defaults(func=cmd_hermes_import)
    hermes_supervisor = hermes_sub.add_parser("supervisor")
    hermes_supervisor.add_argument("--once", action="store_true")
    hermes_supervisor.add_argument("--interval", type=float, default=5.0)
    hermes_supervisor.set_defaults(func=cmd_hermes_supervisor)
    # Removed hermes console subcommand
    sub.add_parser("version").set_defaults(func=lambda _args: print(__version__) or 0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    if args.command is None:
        return cmd_start(argparse.Namespace(no_attach=False))
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    try:
        return int(func(args))
    except RailWardenError as exc:
        print(f"warden: {exc}", file=sys.stderr)
        return 2


def legacy_main(argv: list[str] | None = None) -> int:
    print("The 'lfg' command is deprecated; use 'warden' instead.", file=sys.stderr)
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
