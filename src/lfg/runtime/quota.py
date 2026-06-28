from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from lfg.runtime.events import append_event
from lfg.runtime.session import AgentInstance, QuotaState
from lfg.util.atomic import atomic_write_json


def quota_path(runtime_dir: Path, provider: str, model: str) -> Path:
    safe = f"{provider}-{model}".replace("/", "_").replace(":", "_")
    return runtime_dir / "quotas" / f"{safe}.json"


def load_quota(runtime_dir: Path, agent: AgentInstance) -> QuotaState:
    profile = agent.model_profile
    path = quota_path(runtime_dir, profile.provider, profile.model)
    if not path.exists():
        return QuotaState(
            provider=profile.provider,
            model=profile.model,
            limit_tokens=agent.quota_policy.manual_token_limit,
            confidence="manual"
            if agent.quota_policy.manual_token_limit is not None
            else "unknown",
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid quota state: {path}")
    return QuotaState(
        provider=str(payload["provider"]),
        model=str(payload["model"]),
        used_tokens=int(payload.get("used_tokens", 0)),
        limit_tokens=int(payload["limit_tokens"])
        if payload.get("limit_tokens")
        else None,
        reset_at=float(payload["reset_at"]) if payload.get("reset_at") else None,
        remaining_percent=float(payload["remaining_percent"])
        if payload.get("remaining_percent") is not None
        else None,
        confidence=str(payload.get("confidence", "unknown")),  # type: ignore[arg-type]
        updated_at=float(payload.get("updated_at", time.time())),
    )


def save_quota(runtime_dir: Path, state: QuotaState) -> None:
    atomic_write_json(
        quota_path(runtime_dir, state.provider, state.model), asdict(state)
    )


def update_usage(
    runtime_dir: Path,
    agent: AgentInstance,
    *,
    used_tokens_delta: int = 0,
    remaining_percent: float | None = None,
    limit_tokens: int | None = None,
    confidence: str | None = None,
) -> QuotaState:
    old = load_quota(runtime_dir, agent)
    new_limit = limit_tokens if limit_tokens is not None else old.limit_tokens
    used = old.used_tokens + used_tokens_delta
    if remaining_percent is None and new_limit:
        remaining_percent = max(0.0, 100.0 - (used / new_limit * 100.0))
    state = QuotaState(
        provider=old.provider,
        model=old.model,
        used_tokens=used,
        limit_tokens=new_limit,
        reset_at=old.reset_at,
        remaining_percent=remaining_percent
        if remaining_percent is not None
        else old.remaining_percent,
        confidence=str(confidence or old.confidence),  # type: ignore[arg-type]
    )
    save_quota(runtime_dir, state)
    append_event(
        runtime_dir,
        "quota_updated",
        {
            "provider": state.provider,
            "model": state.model,
            "remaining_percent": state.remaining_percent,
            "confidence": state.confidence,
        },
    )
    return state


def quota_allows_start(
    runtime_dir: Path, agent: AgentInstance
) -> tuple[bool, QuotaState, str]:
    state = load_quota(runtime_dir, agent)
    remaining = state.remaining_percent
    if remaining is None:
        return True, state, "unknown"
    policy = agent.quota_policy
    if remaining < policy.pause_threshold_percent and policy.hard_stop_below_pause:
        return False, state, "pause-threshold"
    if remaining < policy.warning_threshold_percent:
        return True, state, "warning-threshold"
    return True, state, "ok"
