# CLI

Commands: `setup`, `init`, `launch`, `observe`, `observability`, `model`,
`agent`, `quota`, `checkpoint`, `mcp serve`, `adopt`, `run`, `approve plan`,
`plan`, `replan`, `controller`, `dashboard`, `handoff`, `events`, `integrate`,
`start`, `attach`, `status`, `stop`, `restart`, `logs`, `doctor`, `config`,
`version`, and default `lfg`.

Running `lfg launch` in a configured repository starts or attaches to the tmux
workspace. The Hermes pane in the `factory` window is the place to communicate
with the rest of the agent system.

Typical flow:

```bash
lfg setup --yes
lfg doctor
lfg launch
```

`lfg setup --yes` creates project config and ignored runtime state. `lfg doctor`
prints readable checks for Hermes, tmux, provider CLIs, selected credentials,
the generated Hermes profile, LFG MCP visibility, and git ignore rules.

`lfg launch` uses the default dev-shop preset unless you pass `--preset` or
choose `advanced` interactively. `lfg observe` and `lfg observability` render
the live DAG, workflow state, agents, quotas, tmux panes, provider health,
integration queue, events, and Git graph.
