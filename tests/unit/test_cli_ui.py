from __future__ import annotations

from io import StringIO

import pytest

from railwarden.cli import ui


class _TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


def test_plain_prompt_choice_accepts_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAILWARDEN_PLAIN_PROMPTS", "1")
    monkeypatch.setattr("sys.stdin", _TtyStringIO("create-new\n"))
    value = ui.prompt_choice(
        "keep-current",
        "Setup",
        {"keep-current": "Keep", "create-new": "Create"},
    )
    assert value == "create-new"


def test_questionary_style_initializes() -> None:
    _questionary, _style = ui._questionary()
    assert _questionary is not None
    assert _style is not None


def test_plain_prompt_choice_accepts_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAILWARDEN_PLAIN_PROMPTS", "1")
    monkeypatch.setattr("sys.stdin", _TtyStringIO("2\n"))
    value = ui.prompt_choice(
        "guided",
        "Preset",
        {"guided": "Guided", "advanced": "Advanced"},
    )
    assert value == "advanced"
