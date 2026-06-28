# Tmux

Each project gets a deterministic `lfg-<project>-<hash>` session with six panes:

1. Factory Controller
2. Hermes Console
3. Codex worker shell
4. Antigravity worker shell
5. Grok Composer worker shell
6. DAG / Queue / Integration Status

Pane identifiers are persisted in `.lfg-runtime/state/tmux-session.json`.
Hermes is interactive alongside the autonomous controller; it is not replaced by
the controller.

In production launch mode, worker panes remain at a shell prompt. The controller
sends the exact provider CLI command into the assigned pane, writes a small
runtime process script under `.lfg-runtime/processes/`, and tees output to the
task log so code generation is visible and recoverable.
