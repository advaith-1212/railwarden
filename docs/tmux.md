# Tmux Runtime

The local launch path uses tmux as an inspectable execution surface. It is a
runtime UI and process container, not the source of truth.

## Session Shape

Each project gets a deterministic session name like:

```text
lfg-<project>-<hash>
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
.lfg-runtime/state/tmux-session.json
```

## Process Model

Worker panes start as **idle shells** (not interactive provider TUIs). When LFG
assigns a task, the controller injects a runner that executes the real batch
provider command in that pane (for example `codex exec ...`), streams output
live in the pane, and tees the same stream to the task log under
`.lfg-runtime/logs/`.

Headless process launch is only a fallback when the pane is missing/dead or
tmux injection fails. Durable truth still lives in LFG runtime state and Git.

## Attach And Stop

```bash
lfg attach
lfg stop
lfg restart
```

Use `lfg events`, `lfg dashboard`, and `lfg observability` to inspect state
instead of relying only on pane contents.
