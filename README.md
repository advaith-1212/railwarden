# RailWarden

RailWarden is a deterministic, persistent, multi-provider software-delivery
control plane for AI coding agents. It gives an AI orchestrator a durable
execution boundary: project state, work packages, worktrees, provider processes,
validation, review, merge gates, and audit logs live in RailWarden-owned files
instead of in an agent chat transcript.

Mental model:

```text
RailWarden         = environment / substrate / state machine / execution boundary
Hermes      = brain / supervisor / decision maker
Worker CLIs = hands and legs
```

The intended architecture is not "Hermes Kanban as the database." The intended
architecture is:

```text
RailWarden state is the source of truth.
Hermes observes RailWarden state and writes decisions/actions back through RailWarden tools.
Kanban, dashboards, and consoles are projections over that state.
```

## Status

This repository is experimental and actively evolving. The current direction is
RailWarden Evented Runtime + Hermes Supervisor:

- RailWarden owns deterministic truth and mechanics.
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
git clone https://github.com/advaith-1212/railwarden.git
cd railwarden
uv sync
uv tool install --editable .
warden --help
warden setup
warden launch
```

For an existing local checkout:

```bash
cd /path/to/railwarden
uv tool install --editable .
warden version
```

Upgrade an editable install after pulling new commits:

```bash
warden update
```

Run the test suite locally:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

## Quick Start In A Project

From the repository you want RailWarden to manage:

```bash
cd /path/to/project
warden setup --yes
warden doctor
warden context status
```

`warden setup --yes` creates:

- `.railwarden/project.yaml` for durable project configuration
- `.railwarden/work_packages.yaml` for planned work-package contracts
- `.railwarden/validation.yaml` for mechanical validation commands
- `context/` project-memory templates
- ignored runtime directories such as `.railwarden-runtime/` and `.railwarden-worktrees/`

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
warden launch --preset guided
```

Run or sample the supervisor loop:

```bash
warden hermes supervisor --once
warden hermes supervisor
```

Useful observability commands:

```bash
warden snapshot
warden status
warden events --cursor 0 --limit 50
warden failure inspect <task-id>
warden result normalize <task-id>
warden dashboard
warden observability
warden logs
```

Compatibility commands for Hermes Kanban projection/import still exist:

```bash
warden hermes status
warden hermes bootstrap --dry-run
warden hermes bootstrap
warden hermes import --dry-run
warden hermes import --apply
```

Use those as a Hermes-facing planning/coordination UI, not as the canonical
ledger.

## Correct Protocol

The desired control flow is:

```text
User launches RailWarden
-> RailWarden creates runtime environment
-> Hermes starts inside that environment
-> User gives goal to Hermes
-> Hermes asks RailWarden for repo/context/state
-> Hermes asks architect agent to generate plan
-> Architect writes architecture/context proposal
-> Hermes reviews/synthesizes plan
-> Hermes presents plan to user
-> User approves
-> Hermes calls RailWarden tools to freeze plan/work packages/DAG
-> RailWarden records source-of-truth state
-> Hermes assigns worker agents
-> RailWarden launches/supervises worker processes
-> Workers edit worktrees
-> RailWarden validates outputs mechanically
-> Hermes reacts to RailWarden events/failures
-> Hermes decides retry/handoff/repair/replan/ask-user
-> RailWarden executes the chosen action
-> RailWarden owns integration/merge gates
-> Hermes declares completion after RailWarden verifies completion
```

## What RailWarden Owns

RailWarden owns durable facts and mechanics:

- repository and `.railwarden/` configuration
- `.railwarden-runtime/` state and event logs
- task database and work-package contracts
- tmux/session layout
- Git worktrees and task branches
- provider adapters and process supervision
- logs, events, validation evidence, and review artifacts
- merge gates and integration state
- worker result normalization
- file ownership enforcement

These are deterministic environment facts. They should be calculated and
persisted by RailWarden, then observed by Hermes.

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
RailWarden verifies and normalizes that output before anything merges.

## Repository Layout

```text
.railwarden/                     committed RailWarden project configuration
.railwarden-runtime/             ignored runtime state, events, logs, results
.railwarden-worktrees/           ignored worker worktrees
context/                  committed project memory created per target repo
docs/                     RailWarden documentation
schemas/                  machine-readable contracts
src/railwarden/                  CLI, runtime, scheduler, validation, providers
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

## Legacy Compatibility

`warden` is the canonical command. The temporary `lfg` executable delegates to
the same CLI implementation and prints a deprecation warning.

RailWarden prefers `.railwarden`, `.railwarden-runtime`,
`.railwarden-worktrees`, `.railwarden-results`, and `~/.railwarden`. When a
preferred path does not exist but its legacy `.lfg` equivalent does, RailWarden
reads and reuses the legacy path without overwriting or deleting it. Explicit
migration is intentionally separate from normal startup.

Environment settings use the `RAILWARDEN_*` namespace. When a
`RAILWARDEN_*` variable is unset, RailWarden reads the corresponding deprecated
`LFG_*` variable. If both are set, `RAILWARDEN_*` wins.

## License

MIT. See [LICENSE](LICENSE).
