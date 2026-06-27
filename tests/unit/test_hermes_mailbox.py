from __future__ import annotations

from pathlib import Path

from lfg.hermes.mailbox import append_message, messages_for, parse_directive


def test_parse_directive_aliases() -> None:
    assert parse_directive("gemini: check dependencies") == (
        "antigravity",
        "check dependencies",
    )
    assert parse_directive("broadcast: pause") == ("broadcast", "pause")
    assert parse_directive("unknown: no") is None


def test_hermes_mailbox_routes_messages(tmp_path: Path) -> None:
    append_message(
        tmp_path,
        sender="hermes",
        recipient="codex",
        body="inspect package",
    )
    append_message(
        tmp_path,
        sender="hermes",
        recipient="broadcast",
        body="pause after current task",
    )
    codex_messages = messages_for(tmp_path, "codex")
    composer_messages = messages_for(tmp_path, "composer")
    assert [message["body"] for message in codex_messages] == [
        "inspect package",
        "pause after current task",
    ]
    assert [message["body"] for message in composer_messages] == [
        "pause after current task"
    ]
