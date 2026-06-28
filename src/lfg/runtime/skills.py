from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from lfg.config.models import ProjectConfig
from lfg.errors import LfgError
from lfg.runtime.events import append_event
from lfg.util.atomic import atomic_write_text


@dataclass(frozen=True)
class Skill:
    name: str
    path: Path
    text: str
    runtime_only: bool


def skill_directories(config: ProjectConfig) -> tuple[Path, Path]:
    return (
        config.repository_root / ".lfg" / "skills",
        config.runtime_directory / "skills",
    )


def load_skills(config: ProjectConfig) -> tuple[Skill, ...]:
    skills: list[Skill] = []
    project_dir, runtime_dir = skill_directories(config)
    for directory, runtime_only in ((project_dir, False), (runtime_dir, True)):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            skills.append(
                Skill(
                    name=path.stem,
                    path=path,
                    text=path.read_text(encoding="utf-8"),
                    runtime_only=runtime_only,
                )
            )
    return tuple(skills)


def create_runtime_skill(config: ProjectConfig, name: str, text: str) -> Path:
    if "/" in name or "\\" in name or not name.strip():
        raise ValueError("Skill name must be a simple file stem")
    path = config.runtime_directory / "skills" / f"{name}.md"
    atomic_write_text(path, text)
    append_event(
        config.runtime_directory,
        "runtime_skill_created",
        {"name": name, "path": str(path)},
    )
    return path


def promote_runtime_skill(config: ProjectConfig, name: str) -> Path:
    runtime_path = config.runtime_directory / "skills" / f"{name}.md"
    if not runtime_path.exists():
        raise LfgError(f"Runtime skill does not exist: {name}")
    project_path = config.repository_root / ".lfg" / "skills" / f"{name}.md"
    project_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(runtime_path, project_path)
    append_event(
        config.runtime_directory,
        "runtime_skill_promoted",
        {"name": name, "path": str(project_path)},
    )
    return project_path
