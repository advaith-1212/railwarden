from __future__ import annotations

from pathlib import Path

import yaml

from lfg.git import branch_exists, output, run_git
from lfg.util.atomic import atomic_write_text


def detect_tools(repository_root: Path) -> list[str]:
    candidates = {
        "pyproject.toml": "python",
        "package.json": "node",
        "Cargo.toml": "rust",
        "go.mod": "go",
        "pom.xml": "java",
    }
    return [
        tool
        for filename, tool in candidates.items()
        if (repository_root / filename).exists()
    ]


def default_project_payload(repository_root: Path) -> dict[str, object]:
    name = repository_root.name
    return {
        "schema_version": "1.0.0",
        "project": {
            "name": name,
            "repository_root": "auto",
            "integration_branch": "integration/lfg",
            "worktree_root": "auto",
            "board": f"lfg-{name}",
        },
        "planning": {
            "provider": "antigravity",
            "model": "Claude Opus 4.6 (Thinking)",
            "approval_required": True,
            "allow_fallback": False,
        },
        "hermes": {
            "primary_model": "Claude Opus 4.6 (Thinking)",
            "fallback_model": None,
            "allow_fallback": False,
            "board": f"lfg-{name}",
            "project_slug": name.lower().replace("_", "-"),
            "orchestrator_profile": None,
            "default_assignee": "default",
            "profile_map": {},
            "workspace_mode": "worktree",
        },
        "providers": {
            "codex": {
                "priority": 10,
                "capabilities": ["code", "tests", "review"],
                "cooldown_seconds": 3600,
            },
            "antigravity": {
                "priority": 20,
                "capabilities": ["code", "analysis", "planning"],
                "cooldown_seconds": 3600,
            },
            "composer": {
                "priority": 30,
                "capabilities": ["code", "ui", "repair"],
                "cooldown_seconds": 3600,
            },
        },
        "workers": {
            "concurrency": 3,
            "providers": ["codex", "antigravity", "composer"],
        },
        "execution": {
            "require_plan_approval": True,
            "preserve_partial_work_on_handoff": True,
        },
        "integration": {
            "serialized": True,
            "rollback_on_validation_failure": True,
        },
        "monitoring": {"git_graph": True},
        "runtime": {"directory": ".lfg-runtime"},
    }


def default_work_packages_payload() -> dict[str, object]:
    return {"schema_version": "1.0.0", "work_packages": []}


def default_validation_payload(repository_root: Path) -> dict[str, object]:
    tools = detect_tools(repository_root)
    commands: list[dict[str, object]] = []
    if "python" in tools:
        commands.extend(
            [
                {
                    "name": "ruff",
                    "command": {"cwd": ".", "argv": ["ruff", "check", "."]},
                },
                {"name": "pytest", "command": {"cwd": ".", "argv": ["pytest"]}},
            ]
        )
    return {"schema_version": "1.0.0", "commands": commands}


def ensure_gitignore(repository_root: Path, *, yes: bool) -> str:
    path = repository_root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    needed = [".lfg-runtime/", ".lfg-worktrees/"]
    missing = [item for item in needed if item not in existing.splitlines()]
    proposal = "\n".join(missing)
    if missing and yes:
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        atomic_write_text(path, existing + prefix + proposal + "\n")
    return proposal


def initialize_project(repository_root: Path, *, yes: bool) -> dict[str, str]:
    run_git(repository_root, "rev-parse", "--is-inside-work-tree")
    config_dir = repository_root / ".lfg"
    prompts_dir = config_dir / "prompts"
    if yes:
        prompts_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            config_dir / "project.yaml",
            yaml.safe_dump(default_project_payload(repository_root), sort_keys=False),
        )
        if not (config_dir / "work_packages.yaml").exists():
            atomic_write_text(
                config_dir / "work_packages.yaml",
                yaml.safe_dump(default_work_packages_payload(), sort_keys=False),
            )
        if not (config_dir / "validation.yaml").exists():
            atomic_write_text(
                config_dir / "validation.yaml",
                yaml.safe_dump(
                    default_validation_payload(repository_root), sort_keys=False
                ),
            )
        atomic_write_text(config_dir / "state-schema-version", "1.0.0\n")
        ensure_context_templates(repository_root)
    proposal = ensure_gitignore(repository_root, yes=yes)
    needs_commit = False
    if yes:
        needs_commit = ensure_integration_baseline(repository_root, "integration/lfg")
    branch = output(repository_root, "branch", "--show-current")
    return {
        "repository": str(repository_root),
        "branch": branch,
        "gitignore_proposal": proposal,
        "needs_commit": needs_commit,
    }


def ensure_integration_baseline(repository_root: Path, integration_branch: str) -> bool:
    has_head = (
        run_git(
            repository_root, "rev-parse", "--verify", "HEAD", check=False
        ).returncode
        == 0
    )
    if not has_head:
        run_git(repository_root, "add", ".gitignore", ".lfg", "context")
        staged = run_git(
            repository_root, "diff", "--cached", "--quiet", check=False
        ).returncode
        if staged != 0:
            run_git(
                repository_root,
                "-c",
                "user.name=LFG",
                "-c",
                "user.email=lfg@example.invalid",
                "commit",
                "-m",
                "chore: initialize lfg",
            )
        has_head = (
            run_git(
                repository_root, "rev-parse", "--verify", "HEAD", check=False
            ).returncode
            == 0
        )
        needs_commit = False
    else:
        run_git(repository_root, "add", ".gitignore", ".lfg", "context")
        staged = run_git(
            repository_root, "diff", "--cached", "--quiet", check=False
        ).returncode
        needs_commit = staged != 0
    if has_head and not branch_exists(repository_root, integration_branch):
        run_git(repository_root, "branch", integration_branch)
    return needs_commit


def ensure_context_templates(repository_root: Path) -> None:
    context_dir = repository_root / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    templates = {
        "PROJECT_CONTEXT.md": "# Project Context\n\nDescribe the repository, product, and current implementation state.\n",
        "ARCHITECTURE.md": "# Architecture\n\nRecord stable architectural decisions and boundaries.\n",
        "PRODUCT_INVARIANTS.md": "# Product Invariants\n\nList behavior that work packages must preserve.\n",
        "SECURITY_MODEL.md": "# Security Model\n\nDocument trust boundaries, secrets, and sensitive operations.\n",
        "TEST_STRATEGY.md": "# Test Strategy\n\nDocument required checks for package, integration, and release validation.\n",
        "CONTRIBUTING_AGENTS.md": "# Contributing Agents\n\nDescribe how LFG workers should inspect, edit, validate, and hand off work.\n",
    }
    for name, text in templates.items():
        path = context_dir / name
        if not path.exists():
            atomic_write_text(path, text)
