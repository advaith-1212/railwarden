# Tmux Runtime

The local launch path uses tmux as an inspectable execution surface. It is a
runtime UI and process container, not the source of truth.

## Session Shape

Each project gets a deterministic session name like:

```text
railwarden-<project>-<hash>
```

Typical panes:

1. Factory/controller
2. Hermes console
3. Codex worker shell
4. Antigravity worker shell
5. Composer worker shell
6. Observability/dashboard

Pane identifiers are persisted under:

```text
.railwarden-runtime/state/tmux-session.json
```

## Process Model

Worker panes start as **idle shells** (not interactive provider TUIs). When RailWarden
assigns a task, the controller injects a runner that executes the real batch
provider command in that pane (for example `codex exec ...`), streams output
live in the pane, and tees the same stream to the task log under
`.railwarden-runtime/logs/`.

Headless process launch is only a fallback when the pane is missing/dead or
tmux injection fails. Durable truth still lives in RailWarden runtime state and Git.

## Attach And Stop

```bash
warden attach
warden stop
warden restart
```

`warden restart` (and `warden start` when a session profile already exists) recreate
the **v2** factory layout: Hermes interactive chat on the left, idle worker
shells on the right, observability window separate. Do not use a bare
`warden hermes` pane — that only prints CLI help.

Use `warden events`, `warden dashboard`, and `warden observability` to inspect state
instead of relying only on pane contents.
