from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ClaudeStatus:
    detected: bool
    authenticated: bool | None
    model_identifier: str
    verified_invocation: str | None
    remaining_limitation: str | None


class ClaudePlanner:
    def __init__(self, model: str = "claude-opus-4-6") -> None:
        self.model = model

    def doctor(self) -> ClaudeStatus:
        path = shutil.which("claude")
        if path is None:
            return ClaudeStatus(
                detected=False,
                authenticated=None,
                model_identifier=self.model,
                verified_invocation=None,
                remaining_limitation="claude CLI is not installed or not on PATH",
            )
        help_result = subprocess.run(
            [path, "--help"], text=True, capture_output=True, check=False
        )
        authenticated = None
        limitation = (
            None
            if help_result.returncode == 0
            else "claude --help failed; authentication/model invocation not verified"
        )
        invocation = (
            f"{path} --model {self.model}" if help_result.returncode == 0 else None
        )
        return ClaudeStatus(
            detected=True,
            authenticated=authenticated,
            model_identifier=self.model,
            verified_invocation=invocation,
            remaining_limitation=limitation,
        )

    def plan(self) -> None:
        status = self.doctor()
        if not status.detected:
            raise RuntimeError(
                status.remaining_limitation or "Claude planner unavailable"
            )
        raise RuntimeError(
            "Claude planner interface is installed, but non-interactive Thinking invocation has not been verified locally"
        )
