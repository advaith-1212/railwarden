from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl

from lfg.errors import ConfigurationError

KNOWN_PROVIDERS = {
    "codex",
    "antigravity",
    "composer",
    "openai",
    "anthropic",
    "gemini",
    "azure-foundry",
    "ollama",
    "openai-compatible",
}


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str
    base_url: str | None = None
    options: Mapping[str, str] | None = None

    @property
    def reasoning_effort(self) -> str | None:
        if self.options is None:
            return None
        return self.options.get("reasoning")

    def normalized(self) -> str:
        value = f"{self.provider}:{self.model}"
        if self.base_url:
            value = f"{value}@{self.base_url}"
        options = self.options or {}
        if options:
            query = "&".join(f"{key}={options[key]}" for key in sorted(options))
            value = f"{value}?{query}"
        return value


def parse_model_ref(value: str) -> ModelRef:
    text = value.strip()
    if not text:
        raise ConfigurationError("Model ref cannot be empty")
    head, _, query = text.partition("?")
    provider, sep, model_part = head.partition(":")
    if not sep or not provider or not model_part:
        raise ConfigurationError(
            "Model ref must use provider:model syntax, for example openai:gpt-5.2"
        )
    if provider not in KNOWN_PROVIDERS:
        raise ConfigurationError(f"Unsupported model provider in ref: {provider}")
    model, base_url = _split_model_base_url(provider, model_part)
    options = dict(parse_qsl(query, keep_blank_values=False)) if query else {}
    reasoning = options.get("reasoning")
    if reasoning is not None and reasoning not in {"minimal", "low", "medium", "high"}:
        raise ConfigurationError(f"Unsupported reasoning effort: {reasoning}")
    return ModelRef(
        provider=provider,
        model=model,
        base_url=base_url,
        options=options or None,
    )


def _split_model_base_url(provider: str, model_part: str) -> tuple[str, str | None]:
    if provider not in {"ollama", "openai-compatible"}:
        return model_part, None
    model, sep, base_url = model_part.partition("@")
    if not sep:
        if provider == "ollama":
            return model, "http://localhost:11434"
        raise ConfigurationError(f"{provider} model refs must include @<base-url>")
    if not model or not base_url:
        raise ConfigurationError(f"Invalid {provider} model ref")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigurationError(f"{provider} base URL must be absolute")
    return model, base_url


def provider_transport(provider: str) -> str:
    if provider in {"codex", "antigravity", "composer"}:
        return "cli"
    if provider == "ollama":
        return "http"
    return "api"
