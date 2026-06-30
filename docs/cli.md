# CLI

Commands: `setup`, `init`, `launch`, `observe`, `observability`, `model`,
`agent`, `quota`, `checkpoint`, `mcp serve`, `adopt`, `run`, `approve plan`,
`approve-contracts`, `inspect`, `retry`, `reject`, `approve-merge`,
`abort-goal`, `validate`, `review`, `release-review`, `plan`, `replan`,
`controller`, `dashboard`, `handoff`, `events`, `integrate`, `start`,
`attach`, `status`, `stop`, `restart`, `logs`, `doctor`, `config`, `update`,
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

`lfg launch` uses the guided preset by default unless you pass `--preset`.
The guided wizard lets you pick role-appropriate providers, create or reuse
named saved setups, and enter only the fields required for that provider.
Choose `advanced` only when you want to enter raw model refs directly. Worker
panes are visible execution shells; the controller sends provider CLI commands
into them when a tmux session exists.
`lfg observe` and `lfg observability` render the live DAG, factory lifecycle,
agents, quotas, tmux panes, provider health, validation/review evidence,
integration queue, events, and Git graph.

`lfg update` updates the installed global CLI by running `git pull --ff-only`
in the LFG source checkout and then `uv tool install --editable <checkout>
--force`. Use `lfg update --source /path/to/lfg` when the running install
cannot infer the intended source checkout.
