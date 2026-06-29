from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

from lfg.config.models import ProviderState
from lfg.util.atomic import atomic_write_json

TRANSIENT_FAILURE_KINDS = {"quota_exhausted", "rate_limited", "capacity"}
HUMAN_FAILURE_KINDS = {"authentication"}
FAILURE_PATTERNS: dict[str, tuple[str, ...]] = {
    "quota_exhausted": (
        "quota exceeded",
        "quota exhausted",
        "usage limit",
        "resource exhausted",
        "out of credits",
    ),
    "rate_limited": ("rate limit", "too many requests", "http 429", "retry-after"),
    "capacity": (
        "provider overloaded",
        "capacity unavailable",
        "temporarily unavailable",
        "server busy",
    ),
    "git_failure": (
        "fatal: not a git repository",
        "unable to read current working directory",
        "permission denied .git",
    ),
    "workspace_permission": ("operation not permitted", "permission denied"),
    "result_path_unwritable": (
        "output-last-message",
        "expected result json path",
        "result json cannot be placed",
        "blocked_by_sandbox",
        ".lfg-runtime/results",
    ),
    "authentication": (
        "authentication required",
        "invalid access token",
        "unauthorized",
        "login required",
        "expired token",
    ),
    "test_failure": ("pytest", "test failed", "assertionerror"),
}


def classify_failure(text: str) -> tuple[str, bool, bool, str | None]:
    normalized = text.lower()
    if (
        ("result json" in normalized or "output-last-message" in normalized)
        and ("sandbox" in normalized or ".lfg-runtime/results" in normalized)
    ):
        return "result_path_unwritable", False, False, "result json sandbox"
    for kind, patterns in FAILURE_PATTERNS.items():
        for pattern in patterns:
            if pattern in normalized:
                return (
                    kind,
                    kind in TRANSIENT_FAILURE_KINDS,
                    kind in HUMAN_FAILURE_KINDS,
                    pattern,
                )
    return "adapter_failure", False, False, None


def state_path(runtime_dir: Path, provider: str) -> Path:
    return runtime_dir / "provider-health" / f"{provider}.json"


def load_state(runtime_dir: Path, provider: str) -> ProviderState:
    path = state_path(runtime_dir, provider)
    if not path.exists():
        return ProviderState(name=provider)
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid provider state: {path}")
    return ProviderState(
        name=provider,
        status=str(payload.get("status", "healthy")),  # type: ignore[arg-type]
        failure_kind=str(payload["failure_kind"])
        if payload.get("failure_kind")
        else None,
        failure_count=int(payload.get("failure_count", 0)),
        cooldown_until=float(payload["cooldown_until"])
        if payload.get("cooldown_until")
        else None,
        last_failure_at=float(payload["last_failure_at"])
        if payload.get("last_failure_at")
        else None,
        last_success_at=float(payload["last_success_at"])
        if payload.get("last_success_at")
        else None,
        last_error=str(payload["last_error"]) if payload.get("last_error") else None,
        matched_pattern=str(payload["matched_pattern"])
        if payload.get("matched_pattern")
        else None,
    )


def save_state(runtime_dir: Path, state: ProviderState) -> None:
    atomic_write_json(state_path(runtime_dir, state.name), asdict(state))


def refresh_state(state: ProviderState, *, now: float | None = None) -> ProviderState:
    current = time.time() if now is None else now
    if (
        state.status == "cooldown"
        and state.cooldown_until is not None
        and current >= state.cooldown_until
    ):
        state.status = "probe"
        state.cooldown_until = None
    return state


def is_available(runtime_dir: Path, provider: str, *, now: float | None = None) -> bool:
    state = refresh_state(load_state(runtime_dir, provider), now=now)
    if state.status == "probe":
        save_state(runtime_dir, state)
    return state.status in {"healthy", "probe"}


def record_failure(
    runtime_dir: Path, provider: str, error_text: str, *, cooldown_seconds: int = 3600
) -> ProviderState:
    kind, transient, requires_human, pattern = classify_failure(error_text)
    state = load_state(runtime_dir, provider)
    current = time.time()
    state.failure_count += 1
    state.failure_kind = kind
    state.last_failure_at = current
    state.last_error = error_text[:4000]
    state.matched_pattern = pattern
    if transient:
        state.status = "cooldown"
        state.cooldown_until = current + cooldown_seconds
    elif requires_human:
        state.status = "needs_auth"
        state.cooldown_until = None
    else:
        state.status = "degraded"
        state.cooldown_until = None
    save_state(runtime_dir, state)
    return state


def record_success(runtime_dir: Path, provider: str) -> ProviderState:
    state = ProviderState(name=provider, status="healthy", last_success_at=time.time())
    save_state(runtime_dir, state)
    return state
