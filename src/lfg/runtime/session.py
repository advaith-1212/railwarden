from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from lfg.config.models import ProjectConfig
from lfg.errors import ConfigurationError
from lfg.runtime.model_refs import parse_model_ref, provider_transport
from lfg.util.atomic import atomic_write_json

AgentRole = Literal[
    "orchestrator",
    "architect",
    "planner",
    "coder",
    "reviewer",
    "validator",
    "repair",
]

AgentState = Literal[
    "ready",
    "running",
    "paused",
    "handoff_needed",
    "rate_limited",
    "unavailable",
]

QuotaConfidence = Literal["exact", "estimated", "manual", "unknown"]


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model: str
    transport: str
    auth_ref: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    context_window: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    @property
    def model_ref(self) -> str:
        value = f"{self.provider}:{self.model}"
        if self.base_url:
            value = f"{value}@{self.base_url}"
        if self.reasoning_effort:
            value = f"{value}?reasoning={self.reasoning_effort}"
        return value


@dataclass(frozen=True)
class QuotaPolicy:
    warning_threshold_percent: float = 15.0
    pause_threshold_percent: float = 5.0
    hard_stop_below_pause: bool = True
    manual_token_limit: int | None = None


@dataclass(frozen=True)
class QuotaState:
    provider: str
    model: str
    used_tokens: int = 0
    limit_tokens: int | None = None
    reset_at: float | None = None
    remaining_percent: float | None = None
    confidence: QuotaConfidence = "unknown"
    updated_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class AgentInstance:
    agent_id: str
    role: AgentRole
    model_profile: ModelProfile
    executor_adapter: str
    state: AgentState = "ready"
    quota_policy: QuotaPolicy = field(default_factory=QuotaPolicy)
    active_task: str | None = None


@dataclass(frozen=True)
class SessionProfile:
    name: str
    project: str
    created_at: float
    updated_at: float
    orchestrator: AgentInstance
    architect: AgentInstance
    workers: tuple[AgentInstance, ...]
    reviewer: AgentInstance | None = None
    validator: AgentInstance | None = None
    fallback_policy: str = "prompt-before-swap"
    budget_label: str = "default"

    @property
    def agents(self) -> tuple[AgentInstance, ...]:
        optional = tuple(
            agent for agent in (self.reviewer, self.validator) if agent is not None
        )
        return (self.orchestrator, self.architect, *self.workers, *optional)


def model_profile_from_ref(ref: str, *, auth_ref: str | None = None) -> ModelProfile:
    parsed = parse_model_ref(ref)
    return ModelProfile(
        provider=parsed.provider,
        model=parsed.model,
        transport=provider_transport(parsed.provider),
        auth_ref=auth_ref or default_auth_ref(parsed.provider),
        base_url=parsed.base_url,
        reasoning_effort=parsed.reasoning_effort,
    )


def default_auth_ref(provider: str) -> str | None:
    mapping = {
        "openai": "env:OPENAI_API_KEY",
        "anthropic": "env:ANTHROPIC_API_KEY",
        "gemini": "env:GEMINI_API_KEY",
        "azure-foundry": "env:AZURE_OPENAI_API_KEY",
        "openai-compatible": "env:OPENAI_API_KEY",
    }
    return mapping.get(provider)


def session_profile_path(config: ProjectConfig) -> Path:
    return config.runtime_directory / "state" / "session-profile.json"


def default_session_profile(
    config: ProjectConfig, *, name: str = "default"
) -> SessionProfile:
    orchestrator_ref = _legacy_model_ref(
        config.hermes_primary_model, provider="codex", role="orchestrator"
    )
    architect_ref = _legacy_model_ref(
        config.planner_model, provider=config.planner_provider, role="architect"
    )
    workers = tuple(
        AgentInstance(
            agent_id=f"{provider}-1",
            role="coder",
            model_profile=model_profile_from_ref(
                _legacy_model_ref(_default_provider_model(provider), provider=provider)
            ),
            executor_adapter=provider,
        )
        for provider in config.worker_providers
    )
    return SessionProfile(
        name=name,
        project=config.name,
        created_at=time.time(),
        updated_at=time.time(),
        orchestrator=AgentInstance(
            agent_id="hermes",
            role="orchestrator",
            model_profile=model_profile_from_ref(orchestrator_ref),
            executor_adapter="hermes",
        ),
        architect=AgentInstance(
            agent_id="architect",
            role="architect",
            model_profile=model_profile_from_ref(architect_ref),
            executor_adapter=config.planner_provider,
        ),
        workers=workers,
        reviewer=AgentInstance(
            agent_id="reviewer",
            role="reviewer",
            model_profile=model_profile_from_ref(orchestrator_ref),
            executor_adapter="pydanticai",
        ),
    )


def reset_agent_for_launch(agent: AgentInstance, *, model_ref: str) -> AgentInstance:
    return AgentInstance(
        agent_id=agent.agent_id,
        role=agent.role,
        model_profile=model_profile_from_ref(model_ref),
        executor_adapter=agent.executor_adapter,
        state="ready",
        quota_policy=agent.quota_policy,
        active_task=None,
    )


def _default_provider_model(provider: str) -> str:
    defaults = {
        "codex": "gpt-5.5?reasoning=high",
        "antigravity": "gemini-3.5-flash-low",
        "composer": "grok-composer-2.5-fast",
        "openai": "gpt-5.2",
        "anthropic": "claude-opus-4.6",
        "gemini": "gemini-3-pro",
        "ollama": "qwen3-coder@http://localhost:11434",
    }
    return defaults.get(provider, "gpt-5.2")


def _legacy_model_ref(model: str, *, provider: str, role: str | None = None) -> str:
    if ":" in model:
        return model
    if provider == "codex" and role in {"orchestrator", "reviewer"}:
        return "codex:gpt-5.5?reasoning=high"
    normalized_provider = (
        provider if provider in {"codex", "antigravity", "composer"} else "openai"
    )
    slug = (
        model.lower()
        .replace("(", "")
        .replace(")", "")
        .replace(" ", "-")
        .replace("--", "-")
    )
    return f"{normalized_provider}:{slug}"


def save_session_profile(config: ProjectConfig, profile: SessionProfile) -> None:
    atomic_write_json(session_profile_path(config), _session_to_json(profile))


def load_session_profile(config: ProjectConfig) -> SessionProfile:
    path = session_profile_path(config)
    if not path.exists():
        profile = default_session_profile(config)
        save_session_profile(config, profile)
        return profile
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigurationError(f"Invalid session profile: {path}")
    return _session_from_json(payload)


def update_agent(profile: SessionProfile, agent: AgentInstance) -> SessionProfile:
    workers = tuple(
        agent if item.agent_id == agent.agent_id else item for item in profile.workers
    )
    return SessionProfile(
        name=profile.name,
        project=profile.project,
        created_at=profile.created_at,
        updated_at=time.time(),
        orchestrator=agent
        if profile.orchestrator.agent_id == agent.agent_id
        else profile.orchestrator,
        architect=agent
        if profile.architect.agent_id == agent.agent_id
        else profile.architect,
        workers=workers,
        reviewer=agent
        if profile.reviewer and profile.reviewer.agent_id == agent.agent_id
        else profile.reviewer,
        validator=agent
        if profile.validator and profile.validator.agent_id == agent.agent_id
        else profile.validator,
        fallback_policy=profile.fallback_policy,
        budget_label=profile.budget_label,
    )


def _session_to_json(profile: SessionProfile) -> dict[str, object]:
    payload = asdict(profile)
    _assert_no_raw_secret(payload)
    return payload


def _session_from_json(payload: dict[str, object]) -> SessionProfile:
    return SessionProfile(
        name=str(payload["name"]),
        project=str(payload["project"]),
        created_at=_float(payload.get("created_at"), time.time()),
        updated_at=_float(payload.get("updated_at"), time.time()),
        orchestrator=_agent_from_json(_mapping(payload["orchestrator"])),
        architect=_agent_from_json(_mapping(payload["architect"])),
        workers=tuple(
            _agent_from_json(_mapping(item))
            for item in _list(payload.get("workers", []))
        ),
        reviewer=_agent_from_json(_mapping(payload["reviewer"]))
        if payload.get("reviewer")
        else None,
        validator=_agent_from_json(_mapping(payload["validator"]))
        if payload.get("validator")
        else None,
        fallback_policy=str(payload.get("fallback_policy", "prompt-before-swap")),
        budget_label=str(payload.get("budget_label", "default")),
    )


def _agent_from_json(payload: dict[str, object]) -> AgentInstance:
    return AgentInstance(
        agent_id=str(payload["agent_id"]),
        role=str(payload["role"]),  # type: ignore[arg-type]
        model_profile=_model_from_json(_mapping(payload["model_profile"])),
        executor_adapter=str(payload["executor_adapter"]),
        state=str(payload.get("state", "ready")),  # type: ignore[arg-type]
        quota_policy=_quota_policy_from_json(_mapping(payload.get("quota_policy", {}))),
        active_task=str(payload["active_task"]) if payload.get("active_task") else None,
    )


def _model_from_json(payload: dict[str, object]) -> ModelProfile:
    return ModelProfile(
        provider=str(payload["provider"]),
        model=str(payload["model"]),
        transport=str(payload["transport"]),
        auth_ref=str(payload["auth_ref"]) if payload.get("auth_ref") else None,
        base_url=str(payload["base_url"]) if payload.get("base_url") else None,
        reasoning_effort=str(payload["reasoning_effort"])
        if payload.get("reasoning_effort")
        else None,
        context_window=_int(payload.get("context_window"))
        if payload.get("context_window") is not None
        else None,
        input_cost_per_million=_float(payload.get("input_cost_per_million"), 0.0)
        if payload.get("input_cost_per_million") is not None
        else None,
        output_cost_per_million=_float(payload.get("output_cost_per_million"), 0.0)
        if payload.get("output_cost_per_million") is not None
        else None,
    )


def _quota_policy_from_json(payload: dict[str, object]) -> QuotaPolicy:
    return QuotaPolicy(
        warning_threshold_percent=_float(
            payload.get("warning_threshold_percent"), 15.0
        ),
        pause_threshold_percent=_float(payload.get("pause_threshold_percent"), 5.0),
        hard_stop_below_pause=bool(payload.get("hard_stop_below_pause", True)),
        manual_token_limit=_int(payload.get("manual_token_limit"))
        if payload.get("manual_token_limit") is not None
        else None,
    )


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigurationError("Expected mapping in session profile")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ConfigurationError("Expected list in session profile")
    return value


def _float(value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, int | float | str):
        return float(value)
    raise ConfigurationError("Expected numeric value in session profile")


def _int(value: object) -> int:
    if isinstance(value, int | str):
        return int(value)
    raise ConfigurationError("Expected integer value in session profile")


def _assert_no_raw_secret(value: object) -> None:
    text = json.dumps(value, sort_keys=True)
    for env_name, env_value in os.environ.items():
        if not env_name.endswith(("API_KEY", "TOKEN", "SECRET")):
            continue
        if env_value and env_value in text:
            raise ConfigurationError("Session profile contains a raw secret value")
