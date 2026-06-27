from __future__ import annotations

from pathlib import Path
from typing import Any

from lfg.git import run_git, status_porcelain
from lfg.runtime.events import append_event
from lfg.util.atomic import atomic_write_text


def _tail(path: Path, *, max_lines: int = 80) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def create_handoff_packet(
    *,
    runtime_dir: Path,
    task: dict[str, Any],
    goal: str,
    objective: str,
    workspace: Path,
    branch: str,
    provider: str,
    failure_kind: str,
    log_path: Path | None,
    tests: list[dict[str, Any]] | None = None,
    next_provider: str | None = None,
) -> Path:
    task_id = str(task["id"])
    attempt = int(task.get("attempt", 0))
    path = runtime_dir / "handoffs" / f"{task_id}-{attempt}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    diff_summary = run_git(workspace, "diff", "--stat", check=False).stdout.strip()
    status = status_porcelain(workspace) if workspace.exists() else ""
    next_line = (
        f"Continue this task with {next_provider} from the preserved worktree."
        if next_provider
        else "No eligible provider is available; human action is required."
    )
    body = f"""# Handoff: {task_id}

Original goal:
{goal}

Task objective:
{objective}

Branch: `{branch}`
Worktree: `{workspace}`
Last provider: `{provider}`
Failure classification: `{failure_kind}`

## Recent Log

```text
{_tail(log_path) if log_path else ""}
```

## Git Status

```text
{status}
```

## Diff Summary

```text
{diff_summary}
```

## Tests

```text
{tests or []}
```

## Next Instruction

{next_line}
"""
    atomic_write_text(path, body)
    append_event(
        runtime_dir,
        "handoff_packet_created",
        {"path": str(path), "failure_kind": failure_kind, "next_provider": next_provider},
        task_id=task_id,
    )
    return path
