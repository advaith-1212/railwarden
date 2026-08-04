from __future__ import annotations

from dataclasses import asdict, dataclass

from railwarden.runtime.model_refs import parse_model_ref


@dataclass(frozen=True)
class RegistryModel:
    ref: str
    role_hint: str
    transport: str
    notes: str


DEFAULT_MODELS: tuple[RegistryModel, ...] = (
    RegistryModel(
        "codex:gpt-5.5?reasoning=high",
        "orchestrator/coder",
        "cli",
        "Codex CLI high reasoning",
    ),
    RegistryModel(
        "antigravity:claude-opus-4.6-thinking",
        "architect/coder",
        "cli",
        "Antigravity CLI",
    ),
    RegistryModel(
        "antigravity:gemini-3.5-flash-low",
        "coder",
        "cli",
        "Antigravity Gemini 3.5 Flash Low",
    ),
    RegistryModel(
        "composer:grok-composer-2.5-fast",
        "coder/repair",
        "cli",
        "Grok Composer CLI",
    ),
    RegistryModel("openai:gpt-5.2", "reviewer/repair", "api", "OpenAI API"),
    RegistryModel(
        "anthropic:claude-opus-4.6", "planner/reviewer", "api", "Anthropic API"
    ),
    RegistryModel("gemini:gemini-3-pro", "planner/coder", "api", "Gemini API"),
    RegistryModel(
        "azure-foundry:<deployment-name>",
        "enterprise",
        "api",
        "Azure Foundry deployment",
    ),
    RegistryModel(
        "ollama:qwen3-coder@http://localhost:11434",
        "local coder",
        "http",
        "Local Ollama",
    ),
    RegistryModel(
        "openai-compatible:<model>@https://api.example.com/v1",
        "custom",
        "api",
        "OpenAI-compatible API",
    ),
)


def list_models() -> list[dict[str, str]]:
    return [asdict(item) for item in DEFAULT_MODELS]


def validate_model_refs(refs: list[str]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for ref in refs:
        try:
            parsed = parse_model_ref(ref)
        except Exception as exc:
            results.append({"ref": ref, "status": "invalid", "reason": str(exc)})
            continue
        results.append(
            {
                "ref": parsed.normalized(),
                "status": "ok",
                "provider": parsed.provider,
                "model": parsed.model,
                "base_url": parsed.base_url or "",
            }
        )
    return results
