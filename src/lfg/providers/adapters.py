from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lfg.providers.health import classify_failure


@dataclass(frozen=True)
class ProviderAdapter:
    name: str
    executable: str
    model: str

    def health_check(self) -> dict[str, object]:
        path = shutil.which(self.executable)
        if path is None:
            return {
                "name": self.name,
                "status": "unavailable",
                "reason": f"{self.executable} not found",
            }
        help_result = subprocess.run(
            [path, "--help"], text=True, capture_output=True, check=False
        )
        return {
            "name": self.name,
            "status": "healthy" if help_result.returncode == 0 else "degraded",
            "executable": path,
            "model": self.model,
            "help_returncode": help_result.returncode,
        }

    def prepare(self, workspace: Path) -> None:
        if not workspace.is_dir():
            raise RuntimeError(f"Workspace does not exist: {workspace}")

    def launch_command(
        self, workspace: Path, prompt_path: Path, result_path: Path
    ) -> list[str]:
        if self.name == "codex":
            return [
                self.executable,
                "exec",
                "--sandbox",
                "workspace-write",
                "--cd",
                str(workspace),
                "--model",
                self.model,
                "--output-last-message",
                str(result_path),
                str(prompt_path),
            ]
        if self.name == "antigravity":
            return [
                self.executable,
                "--model",
                self.model,
                "--add-dir",
                str(workspace),
                "--print",
                prompt_path.read_text(encoding="utf-8"),
            ]
        if self.name == "composer":
            return [
                self.executable,
                "--cwd",
                str(workspace),
                "--model",
                self.model,
                "--output-format",
                "json",
                "--prompt-file",
                str(prompt_path),
            ]
        raise RuntimeError(f"Unsupported provider: {self.name}")

    def classify_failure(self, text: str) -> tuple[str, bool, bool, str | None]:
        return classify_failure(text)


def default_adapters() -> dict[str, ProviderAdapter]:
    return {
        "codex": ProviderAdapter("codex", "codex", "gpt-5.5"),
        "antigravity": ProviderAdapter("antigravity", "agy", "Gemini 3.1 Pro (High)"),
        "composer": ProviderAdapter("composer", "grok", "grok-composer-2.5-fast"),
    }
