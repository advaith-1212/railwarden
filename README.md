# LFG

LFG is an evented runtime for multi-agent software development. It gives an
AI orchestrator a deterministic execution boundary: project state, work
packages, worktrees, provider processes, validation, review, merge gates, and
audit logs live in LFG-owned files instead of in an agent chat transcript.

Mental model:

```text
LFG         = environment / substrate / state machine / execution boundary
Hermes      = brain / supervisor / decision maker
Worker CLIs = hands and legs
```

The intended architecture is not "Hermes Kanban as the database." The intended
architecture is:

```text
LFG state is the source of truth.
Hermes observes LFG state and writes decisions/actions back through LFG tools.
Kanban, dashboards, and consoles are projections over that state.
```

## Status

This repository is experimental and actively evolving. The current direction is
LFG Evented Runtime + Hermes Supervisor:

- LFG owns deterministic truth and mechanics.
- Hermes owns interpretation, planning, and orchestration decisions.
- Worker agents own scoped implementation inside assigned worktrees.

Hermes Kanban support remains as a projection/compatibility surface. Do not use
it as the canonical database for task state, validation evidence, or merge
readiness.

## Install

Prerequisites:

- Python 3.12
- Git
- `uv`
- `tmux` for the legacy/local pane runtime
- Hermes Agent if you want the Hermes console/supervisor experience
- Provider CLIs or API credentials for the workers you configure

Install from a checkout:

```bash
cd /path/to/lfg
uv tool install --editable .
lfg version
```

Upgrade an editable install after pulling new commits:

```bash
lfg update
```

Run the test suite locally:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

## Quick Start In A Project

From the repository you want LFG to manage:

```bash
cd /path/to/project
lfg setup --yes
lfg doctor
lfg context status
```

`lfg setup --yes` creates:

- `.lfg/project.yaml` for durable project configuration
- `.lfg/work_packages.yaml` for planned work-package contracts
- `.lfg/validation.yaml` for mechanical validation commands
- `context/` project-memory templates
- ignored runtime directories such as `.lfg-runtime/` and `.lfg-worktrees/`

Populate the context files before serious work:

```text
context/PROJECT_CONTEXT.md
context/ARCHITECTURE.md
context/PRODUCT_INVARIANTS.md
context/SECURITY_MODEL.md
context/TEST_STRATEGY.md
context/CONTRIBUTING_AGENTS.md
```

Then launch the local runtime:

```bash
lfg launch --preset guided
```

Run or sample the supervisor loop:

```bash
lfg hermes supervisor --once
lfg hermes supervisor
```

Useful observability commands:

```bash
lfg snapshot
lfg status
lfg events --cursor 0 --limit 50
lfg failure inspect <task-id>
lfg result normalize <task-id>
lfg dashboard
lfg observability
lfg logs
```

Compatibility commands for Hermes Kanban projection/import still exist:

```bash
lfg hermes status
lfg hermes bootstrap --dry-run
lfg hermes bootstrap
lfg hermes import --dry-run
lfg hermes import --apply
```

Use those as a Hermes-facing planning/coordination UI, not as the canonical
ledger.

## Correct Protocol

The desired control flow is:

```text
User launches LFG
-> LFG creates runtime environment
-> Hermes starts inside that environment
-> User gives goal to Hermes
-> Hermes asks LFG for repo/context/state
-> Hermes asks architect agent to generate plan
-> Architect writes architecture/context proposal
-> Hermes reviews/synthesizes plan
-> Hermes presents plan to user
-> User approves
-> Hermes calls LFG tools to freeze plan/work packages/DAG
-> LFG records source-of-truth state
-> Hermes assigns worker agents
-> LFG launches/supervises worker processes
-> Workers edit worktrees
-> LFG validates outputs mechanically
-> Hermes reacts to LFG events/failures
-> Hermes decides retry/handoff/repair/replan/ask-user
-> LFG executes the chosen action
-> LFG owns integration/merge gates
-> Hermes declares completion after LFG verifies completion
```

## What LFG Owns

LFG owns durable facts and mechanics:

- repository and `.lfg/` configuration
- `.lfg-runtime/` state and event logs
- task database and work-package contracts
- tmux/session layout
- Git worktrees and task branches
- provider adapters and process supervision
- logs, events, validation evidence, and review artifacts
- merge gates and integration state
- worker result normalization
- file ownership enforcement

These are deterministic environment facts. They should be calculated and
persisted by LFG, then observed by Hermes.

## What Hermes Owns

Hermes owns decisions:

- goal intake and interpretation
- architect-agent planning
- context population and updates
- plan explanation and approval conversation
- parallelism and assignment strategy
- failure diagnosis
- retry, handoff, repair, and replan decisions
- deciding when to ask the human
- completion narrative

Hermes should not be the only owner of raw truth such as task status, commit
hashes, changed files, validation pass/fail, merge readiness, provider process
liveness, or owned-path enforcement.

## Worker Contract

Workers are scoped implementers. A worker receives:

- one task/work package
- allowed and forbidden paths
- dependencies
- context references
- acceptance criteria
- validation commands
- expected result JSON contract

The worker edits only its assigned worktree, commits completed owned-path
changes, runs local validation where possible, and writes a structured result.
LFG verifies and normalizes that output before anything merges.

## Repository Layout

```text
.lfg/                     committed LFG project configuration
.lfg-runtime/             ignored runtime state, events, logs, results
.lfg-worktrees/           ignored worker worktrees
context/                  committed project memory created per target repo
docs/                     LFG documentation
schemas/                  machine-readable contracts
src/lfg/                  CLI, runtime, scheduler, validation, providers
templates/                worker prompt templates
tests/                    unit, contract, and integration tests
```

## Documentation

- [User guide](docs/user-guide.md)
- [Architecture](docs/architecture.md)
- [Runtime protocol](docs/runtime-protocol.md)
- [Context model](docs/context.md)
- [CLI reference](docs/cli.md)
- [Configuration](docs/configuration.md)
- [Providers](docs/providers.md)
- [Planning](docs/planning.md)
- [Tmux runtime](docs/tmux.md)
- [Recovery](docs/recovery.md)
- [Security](docs/security.md)
- [Development](docs/development.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT. See [LICENSE](LICENSE).
