from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PLANNER_MODEL = "Claude Opus 4.6 (Thinking)"


@dataclass(frozen=True)
class AntigravityPlannerStatus:
    detected: bool
    authenticated: bool | None
    model_identifier: str
    verified_invocation: str | None
    remaining_limitation: str | None
    available_models: tuple[str, ...]


class AntigravityClaudePlanner:
    def __init__(self, model: str = DEFAULT_PLANNER_MODEL) -> None:
        self.model = model

    def doctor(self) -> AntigravityPlannerStatus:
        path = shutil.which("agy")
        if path is None:
            return AntigravityPlannerStatus(
                detected=False,
                authenticated=None,
                model_identifier=self.model,
                verified_invocation=None,
                remaining_limitation="Antigravity CLI `agy` is not installed or not on PATH",
                available_models=(),
            )
        help_result = subprocess.run(
            [path, "--help"], text=True, capture_output=True, check=False
        )
        models_result = subprocess.run(
            [path, "models"], text=True, capture_output=True, check=False
        )
        available_models = tuple(
            line.strip() for line in models_result.stdout.splitlines() if line.strip()
        )
        if help_result.returncode != 0:
            return AntigravityPlannerStatus(
                detected=True,
                authenticated=None,
                model_identifier=self.model,
                verified_invocation=None,
                remaining_limitation="`agy --help` failed; planner invocation not verified",
                available_models=available_models,
            )
        if self.model not in available_models:
            return AntigravityPlannerStatus(
                detected=True,
                authenticated=None,
                model_identifier=self.model,
                verified_invocation=None,
                remaining_limitation=f"Antigravity model is not listed locally: {self.model}",
                available_models=available_models,
            )
        return AntigravityPlannerStatus(
            detected=True,
            authenticated=None,
            model_identifier=self.model,
            verified_invocation=(
                f"{path} --model {self.model!r} --add-dir <repo> "
                "--sandbox --print <planning prompt>"
            ),
            remaining_limitation=None,
            available_models=available_models,
        )

    def command(self, *, repository: Path, prompt: str) -> list[str]:
        status = self.doctor()
        if status.remaining_limitation is not None:
            raise RuntimeError(status.remaining_limitation)
        path = shutil.which("agy")
        if path is None:
            raise RuntimeError("Antigravity CLI `agy` is not installed")
        return [
            path,
            "--model",
            self.model,
            "--add-dir",
            str(repository),
            "--sandbox",
            "--print-timeout",
            "45m",
            "--print",
            prompt,
        ]
