# Tmux

Each project gets a deterministic `lfg-<project>-<hash>` session with six panes:

1. Factory Controller
2. Hermes Console
3. Codex - GPT-5.5 High
4. Antigravity - Gemini 3.1 Pro High
5. Grok Composer 2.5
6. DAG / Queue / Integration Status

Pane identifiers are persisted in `.lfg-runtime/state/tmux-session.json`.
Hermes is interactive alongside the autonomous controller; it is not replaced by
the controller.
