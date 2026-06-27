# LFG

LFG is a standalone, reusable agentic software-development orchestration system. It is installed as a global `lfg` CLI and operates on any Git repository through project-local `.lfg/` configuration and ignored `.lfg-runtime/` state.

LFG is not a TMOM feature and is not nested inside a product repository. TMOM's orchestration prototype was used only as source material for generic behaviors: deterministic DAG scheduling, safe worktree provisioning, lifecycle validation, serialized integration, provider health, process supervision, and tmux coordination.

## Install

```bash
cd /path/to/lfg
uv tool install --editable .
```

Prerequisites: Python 3.12, Git, tmux, and provider CLIs as needed:
`agy` for Claude Opus 4.6 Thinking planning and Gemini workers, `codex`
for GPT-5.5 High workers, and `grok` for Composer 2.5 workers.

## Workflow

```bash
cd /path/to/project
lfg init --yes
lfg start
```

For an existing repository:

```bash
lfg adopt --dry-run /path/to/project
lfg start
```

`lfg` with no subcommand starts or attaches to the project tmux workspace. The workspace has six panes: Factory Controller, Hermes Console, Codex - GPT-5.5 High, Antigravity - Gemini 3.1 Pro High, Grok Composer 2.5, and DAG / Queue / Integration Status.

Hermes is the coordination surface. In the Hermes pane you can submit `goal <text>`, review the generated plan, then run `approve plan` to allow unattended execution. You can also inspect `status`, `dag`, `agents`, `tasks`, and `logs`; route instructions with `codex: ...`, `gemini: ...`, `composer: ...`, or `broadcast: ...`; and use `handoff <task_id> [provider]`, `retry <task_id>`, `block <task_id>`, and `unblock <task_id>` for manual intervention.

## Planning Approval

The planner interface targets Claude Opus 4.6 Thinking through Antigravity (`agy`). `lfg run "<goal>"` stores the goal under `.lfg-runtime/runs/<run_id>/goal.md`, asks the planner for a human plan, derives machine-readable work packages when needed, writes the pending plan to runtime state, and waits. `lfg approve plan` writes `.lfg/plan.md` and `.lfg/work_packages.yaml`, creates durable task state, and lets `lfg controller` schedule work.

LFG does not fake planner success. If `agy` is unavailable or the model is not listed locally, `lfg doctor` reports the exact blocker.

## Execution

The controller is deterministic: it releases DAG nodes after dependencies are merged, chooses healthy providers by preferred provider and configured priority, provisions one Git worktree per task, writes a prompt and expected result path, launches provider CLIs, validates structured worker result JSON, records events in `.lfg-runtime/events.jsonl`, and integrates one branch at a time through the existing validation and rollback flow.

Use `lfg dashboard` for the terminal-native DAG, queue, provider health, recent event, and Git graph view. Use `lfg events` to inspect raw runtime events.

## Recovery

Runtime state is versioned and stored under `.lfg-runtime/`. Git remains the source of truth for code state. LFG stores supplemental state, logs, validation evidence, locks, handoff packets, and process identifiers under the runtime directory.

Quota, rate-limit, capacity, and auth failures are classified from provider logs. Partial work is preserved by default; LFG writes `.lfg-runtime/handoffs/<task_id>-<attempt>.md` with the branch, worktree, log excerpt, status, diff summary, tests, and next instruction before assigning the next eligible provider or blocking for human action.

## Limitations

Provider adapters expose command construction, health checks, and failure classification only. Automated tests use fake/disposable repositories and do not make paid model calls. Hermes is implemented as an interactive control plane backed by file state; legacy `tmomcoord` behavior is documented as an adapter target.
