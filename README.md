# LFG

LFG is a standalone, reusable agentic software-development orchestration system. It is installed as a global `lfg` CLI and operates on any Git repository through project-local `.lfg/` configuration and ignored `.lfg-runtime/` state.

LFG is not a TMOM feature and is not nested inside a product repository. TMOM's orchestration prototype was used only as source material for generic behaviors: deterministic DAG scheduling, safe worktree provisioning, lifecycle validation, serialized integration, provider health, process supervision, and tmux coordination.

## Install

```bash
cd /path/to/lfg
uv tool install --editable .
lfg version
```

To upgrade an editable install after pulling new commits:

```bash
lfg update
```

`lfg update` fast-forwards the LFG source checkout with `git pull --ff-only`
and reinstalls the global CLI with `uv tool install --editable <checkout>
--force`.

Prerequisites:

- Python 3.12 and Git
- tmux for the terminal workspace
- Hermes Agent (`hermes`) for the orchestration console
- Provider CLIs as needed: `codex`, `agy`, and `grok`

Run `lfg doctor` inside a configured project to see exactly what is installed,
what is missing, whether Hermes can see the LFG MCP tools, and whether runtime
state is ignored by git.

## Workflow

```bash
cd /path/to/project
lfg setup --yes
lfg doctor
lfg launch
```

For an existing repository:

```bash
lfg adopt --dry-run /path/to/project
lfg start
```

`lfg launch` starts or attaches to the project tmux workspace. The default
launch preset starts a `factory` window with Hermes, the LFG controller,
workers, and integration status, plus an `observability` window for DAG,
workflow, git, quota, event, and log state. LFG attaches to the `factory`
window by default so Hermes is the first thing you see.

Hermes is the coordination surface. Tell Hermes what to build in normal
language. LFG exposes durable factory actions to Hermes through MCP tools such
as `lfg.goal.submit`, `lfg.plan.show`, `lfg.contracts.freeze`,
`lfg.task.inspect`, `lfg.validation.run`, `lfg.review.run`,
`lfg.merge.approve`, `lfg.agent.swap`, and `lfg.checkpoint.create`.

Use `lfg observe` or `lfg observability` for a readable terminal view of the
DAG, workflow state, tmux panes, agents, and quotas.

## Planning Approval

The planner interface targets Claude Opus 4.6 Thinking through Antigravity (`agy`). `lfg run "<goal>"` stores the goal under `.lfg-runtime/runs/<run_id>/goal.md`, asks the planner for a human plan, derives machine-readable work packages when needed, writes the pending plan to runtime state, and waits. `lfg approve plan`, `lfg approve-contracts`, or Hermes `approved` writes `.lfg/plan.md`, freezes schema `2.0.0` work-package contracts, creates durable task state, and lets `lfg controller` schedule work.

Approval also writes `.lfg/contract_freeze_manifest.yaml`, `.lfg/model_assignment.yaml`, `.lfg/dependency_graph.mmd`, `.lfg/ownership_matrix.csv`, and `.lfg/agent_prompts/<wp>.md`.

LFG does not fake planner success. If `agy` is unavailable or the model is not listed locally, `lfg doctor` reports the exact blocker.

## Execution

The controller is deterministic: it releases DAG nodes after dependencies are merged, chooses healthy providers by preferred provider and configured priority, provisions one Git worktree per task, writes a prompt and expected result path, launches provider CLIs visibly in tmux worker panes when available, validates structured worker result JSON, runs LFG-owned package validation commands, records independent review evidence, records events in `.lfg-runtime/events.jsonl`, and integrates one reviewed branch at a time through validation and rollback.

Use `lfg dashboard` for the terminal-native DAG, queue, provider health, recent event, and Git graph view. Use `lfg events` to inspect raw runtime events.

## Recovery

Runtime state is versioned and stored under `.lfg-runtime/`. Git remains the source of truth for code state. LFG stores supplemental state, logs, validation evidence, locks, handoff packets, and process identifiers under the runtime directory.

Quota, rate-limit, capacity, and auth failures are classified from provider logs. Partial work is preserved by default; LFG writes `.lfg-runtime/handoffs/<task_id>-<attempt>.md` with the branch, worktree, log excerpt, status, diff summary, tests, and next instruction before assigning the next eligible provider or blocking for human action.

## Limitations

Provider adapters expose command construction, health checks, and failure classification only. Automated tests use fake/disposable repositories and do not make paid model calls. Hermes is implemented as an interactive control plane backed by file state; legacy `tmomcoord` behavior is documented as an adapter target.
