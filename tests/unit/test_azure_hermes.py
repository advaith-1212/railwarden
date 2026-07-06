from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lfg.config.init import initialize_project
from lfg.config.loader import load_project_files
from lfg.errors import LfgError
from lfg.hermes.azure import (
    resolve_azure_hermes_config,
    validate_azure_inference_endpoint,
)
from lfg.hermes.profile import generate_hermes_profile
from lfg.runtime.launch_setups import LaunchSetup, save_launch_setup
from lfg.runtime.session import (
    AgentInstance,
    SessionProfile,
    load_session_profile,
    model_profile_from_ref,
    save_session_profile,
)


def test_validate_rejects_foundry_project_url() -> None:
    with pytest.raises(LfgError, match="project URL"):
        validate_azure_inference_endpoint(
            "https://example.services.ai.azure.com/api/projects/demo"
        )


def test_resolve_azure_hermes_config_openai_v1() -> None:
    config = resolve_azure_hermes_config(
        endpoint="https://example.openai.azure.com",
        deployment="gpt-5.4",
        api_version="2025-11-15-preview",
    )
    assert config.provider == "azure-foundry"
    assert config.base_url == "https://example.openai.azure.com/openai/v1"
    assert config.api_mode == "codex_responses"
    assert config.api_version == "2025-11-15-preview"
    assert "api-version=" not in config.base_url


def test_resolve_azure_hermes_config_legacy_endpoint_appends_api_version() -> None:
    config = resolve_azure_hermes_config(
        endpoint="https://example.openai.azure.com/openai/deployments/gpt-5.4",
        deployment="gpt-5.4",
        api_version="2024-10-21",
    )
    assert "api-version=2024-10-21" in config.base_url


def test_parse_azure_foundry_model_ref_splits_endpoint() -> None:
    from lfg.runtime.model_refs import parse_model_ref

    parsed = parse_model_ref(
        "azure-foundry:gpt-5.4@https://corellm.openai.azure.com/openai/v1"
    )
    assert parsed.model == "gpt-5.4"
    assert parsed.base_url == "https://corellm.openai.azure.com/openai/v1"


def test_generated_hermes_config_uses_azure_foundry_provider(git_repo: Path) -> None:
    initialize_project(git_repo, yes=True)
    files = load_project_files(git_repo)
    profile = load_session_profile(files.project)
    orchestrator = AgentInstance(
        agent_id=profile.orchestrator.agent_id,
        role="orchestrator",
        model_profile=model_profile_from_ref("azure-foundry:gpt-5.4"),
        executor_adapter="hermes",
        state="ready",
        quota_policy=profile.orchestrator.quota_policy,
        active_task=None,
        setup_name="azure-test",
    )
    updated = SessionProfile(
        name=profile.name,
        project=profile.project,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        orchestrator=orchestrator,
        architect=profile.architect,
        workers=profile.workers,
        reviewer=profile.reviewer,
        validator=profile.validator,
        budget_label=profile.budget_label,
    )
    save_launch_setup(
        LaunchSetup(
            name="azure-test",
            provider="azure-foundry",
            model="gpt-5.4",
            base_url="https://example.openai.azure.com/openai/v1",
            auth_env_var="LFG_AZURE_TEST_API_KEY",
        ),
        env={
            "LFG_AZURE_TEST_API_KEY": "secret",
            "AZURE_FOUNDRY_API_KEY": "secret",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/openai/v1",
            "OPENAI_API_VERSION": "2024-10-21",
        },
    )
    save_session_profile(files.project, updated)
    hermes = generate_hermes_profile(files.project, updated)
    payload = yaml.safe_load(hermes.config_path.read_text(encoding="utf-8"))
    assert payload["model"]["provider"] == "azure-foundry"
    assert "openai/v1" in str(payload["model"]["base_url"])
    assert payload["model"]["api_mode"] == "codex_responses"
    assert "api-version=" not in str(payload["model"]["base_url"])
    assert payload["model"]["default"] == "gpt-5.4"