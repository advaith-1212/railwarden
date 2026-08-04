from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from railwarden.errors import ConfigurationError
from railwarden.runtime.session import ModelProfile


@dataclass(frozen=True)
class TypedAgentSpec:
    role: str
    model_ref: str
    output_type: str
    concurrency_limit: int


def pydanticai_available() -> bool:
    try:
        import pydantic_ai  # noqa: F401
    except Exception:
        return False
    return True


def typed_agent_spec(
    *,
    role: str,
    model_profile: ModelProfile,
    output_type: type[Any],
    concurrency_limit: int = 1,
) -> TypedAgentSpec:
    return TypedAgentSpec(
        role=role,
        model_ref=model_profile.model_ref,
        output_type=f"{output_type.__module__}.{output_type.__qualname__}",
        concurrency_limit=concurrency_limit,
    )


def pydanticai_model(model_profile: ModelProfile) -> Any:
    provider = model_profile.provider
    api_key = _auth_env_value(model_profile.auth_ref)
    if provider == "openai":
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIResponsesModel(
            model_profile.model,
            provider=OpenAIProvider(api_key=api_key),
        )
    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(
            model_profile.model,
            provider=AnthropicProvider(api_key=api_key),
        )
    if provider == "gemini":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        google_key = api_key or os.environ.get("GOOGLE_API_KEY")
        if google_key is None:
            raise ConfigurationError(
                "Gemini PydanticAI calls require GEMINI_API_KEY or GOOGLE_API_KEY"
            )
        return GoogleModel(
            model_profile.model,
            provider=GoogleProvider(api_key=google_key),
        )
    if provider == "azure-foundry":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.azure import AzureProvider

        return OpenAIChatModel(
            model_profile.model,
            provider=AzureProvider(api_key=api_key),
        )
    if provider == "ollama":
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider

        return OllamaModel(
            model_profile.model,
            provider=OllamaProvider(base_url=model_profile.base_url, api_key=api_key),
        )
    if provider == "openai-compatible":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        if model_profile.base_url is None:
            raise ConfigurationError("openai-compatible model refs require base_url")
        return OpenAIChatModel(
            model_profile.model,
            provider=OpenAIProvider(
                base_url=model_profile.base_url,
                api_key=api_key,
            ),
        )
    raise ConfigurationError(
        f"Provider {provider} is not available for direct PydanticAI calls"
    )


def create_pydanticai_agent(
    *,
    system_prompt: str,
    model_profile: ModelProfile,
    output_type: type[Any],
) -> Any:
    try:
        from pydantic_ai import Agent
    except Exception as exc:
        raise RuntimeError("pydantic-ai is not installed") from exc
    return Agent(
        pydanticai_model(model_profile),
        system_prompt=system_prompt,
        output_type=output_type,
    )


def _auth_env_value(auth_ref: str | None) -> str | None:
    if auth_ref is None or not auth_ref.startswith("env:"):
        return None
    return os.environ.get(auth_ref.removeprefix("env:"))
