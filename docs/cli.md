# CLI

RailWarden's CLI manages project setup, runtime state, task execution, validation,
review, and observability.

List commands:

```bash
warden --help
```

## First Run

In the repository you want RailWarden to control:

```bash
warden setup --yes
warden doctor
```

`warden setup --yes` writes committed project files under `.railwarden/`, creates context
templates under `context/`, and ensures ignored runtime paths exist in
`.gitignore`.

`warden doctor` checks local prerequisites and prints actionable status for Git,
Hermes, tmux, provider CLIs, credentials, MCP startup, runtime ignore rules, and
configured planning/provider defaults.

## Runtime Launch

```bash
warden launch --preset guided
```

The local launch path creates or attaches to a deterministic tmux session,
writes runtime profile state under `.railwarden-runtime/`, starts Hermes with an
RailWarden-aware runtime profile, and prepares worker panes. Worker processes are
visible and recoverable from logs/events.

Useful launch variants:

```bash
warden launch --preset default-dev-shop
warden launch --preset local-only
warden launch --profile my-session
warden launch --no-attach
```

Common runtime commands:

```bash
warden snapshot
warden status
warden dashboard
warden observability
warden events --cursor 0 --limit 50
warden logs
warden attach
warden stop
warden restart
warden controller --once
```

## Planning And Approval

```bash
warden plan "implement the requested goal"
warden approve plan
warden approve-contracts
warden replan "reason for replan"
```

The approval gate exists so Hermes can present a plan, the human can approve
it, and then RailWarden can freeze work-package contracts, ownership, prompts, and the
DAG before workers begin.

## Task And Agent Control

```bash
warden inspect <task-id>
warden retry <task-id>
warden reject <task-id>
warden handoff <task-id> <provider>
warden validate <task-id>
warden review <task-id>
warden approve-merge <task-id>
warden integrate
```

Agent and quota controls:

```bash
warden agent list
warden agent pause <agent-id>
warden agent resume <agent-id>
warden agent swap <agent-id> --to <model-ref>
warden quota status
warden checkpoint create <task-id>
```

Context, result, failure, and decision helpers:

```bash
warden context status
warden context write ARCHITECTURE.md --content-file /tmp/architecture.md
warden result normalize <task-id>
warden failure inspect <task-id>
warden decision record '{"observed_event":{},"diagnosis":"...","allowed_actions":["retry_same_provider"],"chosen_action":"retry_same_provider","rationale":"..."}'
```

## Events

`warden events` reads the append-only runtime event log:

```bash
warden events --cursor 0 --limit 100
```

Hermes should use events as signals. RailWarden emits facts and allowed actions;
Hermes chooses the action and calls back into RailWarden.

## MCP

RailWarden exposes tools for Hermes or any MCP client:

```bash
warden mcp serve
```

Representative tools include:

- `railwarden.goal.submit`
- `railwarden.plan.show`
- `railwarden.plan.approve`
- `railwarden.contracts.freeze`
- `railwarden.task.list`
- `railwarden.task.inspect`
- `railwarden.task.route`
- `railwarden.task.retry`
- `railwarden.agent.swap`
- `railwarden.validation.run`
- `railwarden.review.run`
- `railwarden.merge.approve`
- `railwarden.integration.status`

## Hermes Compatibility Commands

The repository still includes Hermes Kanban companion commands:

```bash
warden hermes status
warden hermes supervisor --once
warden hermes bootstrap --dry-run
warden hermes bootstrap
warden hermes import --dry-run
warden hermes import --apply
warden hermes console
```

Use `warden hermes supervisor` as the reactive loop over RailWarden events. Use
`bootstrap` and `import` for a Hermes-facing board/projection. Do not treat the
Kanban board as the canonical database for task status, validation, merge
readiness, or commit truth.

## Updating RailWarden

For editable installs:

```bash
warden update
```

When the running install cannot infer its source checkout:

```bash
warden update --source /path/to/railwarden
```
