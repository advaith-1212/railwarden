# CLI

LFG's CLI manages project setup, runtime state, task execution, validation,
review, and observability.

List commands:

```bash
lfg --help
```

## First Run

In the repository you want LFG to control:

```bash
lfg setup --yes
lfg doctor
```

`lfg setup --yes` writes committed project files under `.lfg/`, creates context
templates under `context/`, and ensures ignored runtime paths exist in
`.gitignore`.

`lfg doctor` checks local prerequisites and prints actionable status for Git,
Hermes, tmux, provider CLIs, credentials, MCP startup, runtime ignore rules, and
configured planning/provider defaults.

## Runtime Launch

```bash
lfg launch
```

The local launch path creates or attaches to a deterministic tmux session,
writes runtime profile state under `.lfg-runtime/`, starts Hermes with an
LFG-aware runtime profile, and prepares worker panes. Worker processes are
visible and recoverable from logs/events.

Common runtime commands:

```bash
lfg status
lfg dashboard
lfg observability
lfg events --limit 50
lfg logs
lfg attach
lfg stop
lfg restart
```

## Planning And Approval

```bash
lfg plan "implement the requested goal"
lfg approve plan
lfg approve-contracts
lfg replan "reason for replan"
```

The approval gate exists so Hermes can present a plan, the human can approve
it, and then LFG can freeze work-package contracts, ownership, prompts, and the
DAG before workers begin.

## Task And Agent Control

```bash
lfg inspect <task-id>
lfg retry <task-id>
lfg reject <task-id>
lfg handoff <task-id> <provider>
lfg validate <task-id>
lfg review <task-id>
lfg approve-merge <task-id>
lfg integrate
```

Agent and quota controls:

```bash
lfg agent list
lfg agent pause <agent-id>
lfg agent resume <agent-id>
lfg agent swap <agent-id> --to <model-ref>
lfg quota status
lfg checkpoint create <task-id>
```

Context, result, failure, and decision helpers:

```bash
lfg context status
lfg context write ARCHITECTURE.md --content-file /tmp/architecture.md
lfg result normalize <task-id>
lfg failure inspect <task-id>
lfg decision record /tmp/decision.json
```

## Events

`lfg events` reads the append-only runtime event log:

```bash
lfg events --limit 100
```

Hermes should use events as signals. LFG emits facts and allowed actions;
Hermes chooses the action and calls back into LFG.

## MCP

LFG exposes tools for Hermes or any MCP client:

```bash
lfg mcp serve
```

Representative tools include:

- `lfg.goal.submit`
- `lfg.plan.show`
- `lfg.plan.approve`
- `lfg.contracts.freeze`
- `lfg.task.list`
- `lfg.task.inspect`
- `lfg.task.route`
- `lfg.task.retry`
- `lfg.agent.swap`
- `lfg.validation.run`
- `lfg.review.run`
- `lfg.merge.approve`
- `lfg.integration.status`

## Hermes Compatibility Commands

The repository still includes Hermes Kanban companion commands:

```bash
lfg hermes status
lfg hermes supervisor --once
lfg hermes bootstrap --dry-run
lfg hermes bootstrap
lfg hermes import --dry-run
lfg hermes import --apply
lfg hermes console
```

Use `lfg hermes supervisor` as the reactive loop over LFG events. Use
`bootstrap` and `import` for a Hermes-facing board/projection. Do not treat the
Kanban board as the canonical database for task status, validation, merge
readiness, or commit truth.

## Updating LFG

For editable installs:

```bash
lfg update
```

When the running install cannot infer its source checkout:

```bash
lfg update --source /path/to/lfg
```
