from __future__ import annotations

from pathlib import Path

import pytest

from railwarden.runtime.events import read_events
from railwarden.runtime.tasks import ensure_task, transition_task


def test_valid_transition_persists_state_and_event(tmp_path: Path) -> None:
    task = ensure_task(tmp_path, package_id="WP-1", name="One", dependencies=())

    updated = transition_task(tmp_path, task, "ready")

    assert updated["status"] == "ready"
    assert read_events(tmp_path)[-1]["payload"]["to"] == "ready"


def test_terminal_task_cannot_return_to_execution(tmp_path: Path) -> None:
    task = ensure_task(tmp_path, package_id="WP-1", name="One", dependencies=())
    transition_task(tmp_path, task, "merged")

    with pytest.raises(ValueError, match="terminal task"):
        transition_task(tmp_path, task, "running")
