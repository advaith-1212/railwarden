from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

AGENT_ALIASES = {
    "codex": "codex",
    "gpt": "codex",
    "gemini": "antigravity",
    "antigravity": "antigravity",
    "composer": "composer",
    "grok": "composer",
    "all": "broadcast",
    "broadcast": "broadcast",
}


def mailbox_path(runtime_dir: Path) -> Path:
    return runtime_dir / "state" / "hermes-mailbox.jsonl"


def append_message(
    runtime_dir: Path,
    *,
    sender: str,
    recipient: str,
    body: str,
) -> dict[str, Any]:
    path = mailbox_path(runtime_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "timestamp": time.time(),
        "sender": sender,
        "recipient": AGENT_ALIASES.get(recipient.lower(), recipient.lower()),
        "body": body,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload


def read_messages(runtime_dir: Path) -> list[dict[str, Any]]:
    path = mailbox_path(runtime_dir)
    if not path.exists():
        return []
    messages: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            messages.append(payload)
    return messages


def messages_for(
    runtime_dir: Path, provider: str, *, after: int = 0
) -> list[dict[str, Any]]:
    provider_name = AGENT_ALIASES.get(provider.lower(), provider.lower())
    messages = read_messages(runtime_dir)
    return [
        message
        for message in messages[after:]
        if message.get("recipient") in {provider_name, "broadcast"}
    ]


def parse_directive(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    recipient, body = line.split(":", 1)
    recipient = recipient.strip().lower()
    body = body.strip()
    if not recipient or not body:
        return None
    mapped = AGENT_ALIASES.get(recipient)
    if mapped is None:
        return None
    return mapped, body
