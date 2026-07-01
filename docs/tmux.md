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

Worker panes are visible execution shells. The controller can send exact
provider CLI commands into assigned panes, write process metadata under
`.lfg-runtime/processes/`, and tee output to task logs.

This makes provider work visible, debuggable, and recoverable. The durable
truth still lives in LFG runtime state and Git.

## Attach And Stop

```bash
lfg attach
lfg stop
lfg restart
```

Use `lfg events`, `lfg dashboard`, and `lfg observability` to inspect state
instead of relying only on pane contents.
