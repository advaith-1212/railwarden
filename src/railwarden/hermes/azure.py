from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from railwarden.errors import RailWardenError


@dataclass(frozen=True)
class AzureHermesConfig:
    provider: str
    base_url: str
    api_mode: str
    api_version: str | None = None


_PROJECT_URL_MARKERS = ("/api/projects/",)


def validate_azure_inference_endpoint(endpoint: str) -> str:
    normalized = endpoint.strip().rstrip("/")
    if not normalized:
        raise RailWardenError("Azure inference endpoint URL is required")
    lowered = normalized.lower()
    if any(marker in lowered for marker in _PROJECT_URL_MARKERS):
        raise RailWardenError(
            "Azure endpoint looks like a Foundry project URL. "
            "Use an inference endpoint such as "
            "https://<resource>.openai.azure.com/openai/v1"
        )
    return normalized


def normalize_azure_inference_endpoint(endpoint: str) -> str:
    normalized = validate_azure_inference_endpoint(endpoint)
    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")
    if "/anthropic" in path.lower():
        return normalized
    if path.endswith("/openai"):
        path = f"{path}/v1"
    elif "/openai/" not in path.lower():
        path = "/openai/v1" if path in {"", "/"} else f"{path}/openai/v1"
    return urlunparse(parsed._replace(path=path))


def infer_azure_api_mode(*, endpoint: str, deployment: str) -> str:
    lowered = endpoint.lower()
    if "/anthropic" in lowered:
        return "anthropic_messages"
    deployment_lower = deployment.lower()
    if re.search(r"gpt-5|codex|o[134]", deployment_lower):
        return "codex_responses"
    return "chat_completions"


def _uses_openai_v1_ga_endpoint(endpoint: str) -> bool:
    path = urlparse(endpoint).path.rstrip("/").lower()
    return path.endswith("/openai/v1") or path.endswith("/v1")


def resolve_azure_hermes_config(
    *,
    endpoint: str,
    deployment: str,
    api_version: str | None = None,
) -> AzureHermesConfig:
    base_url = normalize_azure_inference_endpoint(endpoint)
    mode = infer_azure_api_mode(endpoint=base_url, deployment=deployment)
    version = api_version.strip() if api_version else None
    # Hermes treats /openai/v1 as GA: api-version belongs in config/default_query,
    # not baked into the base URL (that yields HTTP 400 on many resources).
    if (
        version
        and "api-version=" not in base_url.lower()
        and not _uses_openai_v1_ga_endpoint(base_url)
    ):
        parsed = urlparse(base_url)
        query = parse_qs(parsed.query)
        query["api-version"] = [version]
        base_url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return AzureHermesConfig(
        provider="azure-foundry",
        base_url=base_url,
        api_mode=mode,
        api_version=version,
    )
