# CLI

Commands: `init`, `adopt`, `run`, `approve plan`, `plan`, `replan`,
`controller`, `dashboard`, `handoff`, `events`, `integrate`, `start`,
`attach`, `status`, `stop`, `restart`, `logs`, `doctor`, `config`, `version`,
and default `lfg`.

Running `lfg` in a configured repository starts or attaches to the tmux
workspace. The Hermes pane is the place to communicate with the rest of the
agent system.

Typical flow:

```bash
lfg init --yes
lfg run "implement the feature"
lfg approve plan
lfg controller
```

`lfg run` creates a pending planner output under `.lfg-runtime/` and waits for
approval. `lfg approve plan` promotes it to `.lfg/plan.md` and
`.lfg/work_packages.yaml`. `lfg controller` schedules, monitors, hands off, and
integrates work until no runnable task remains. `lfg dashboard` renders the live
DAG, provider health, integration queue, events, and Git graph.
