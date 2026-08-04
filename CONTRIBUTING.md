# Contributing

RailWarden is an experimental multi-agent software-development runtime. Contributions
should preserve the core authority split:

```text
RailWarden owns durable state and mechanics.
Hermes owns decisions.
Workers own scoped implementation.
```

## Local Setup

```bash
git clone https://github.com/advaith-1212/railwarden.git
cd railwarden
uv sync
uv run warden --help
```

Optional editable install:

```bash
uv tool install --editable .
```

## Before Submitting Changes

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

For CLI behavior changes, also run the relevant help or smoke command:

```bash
uv run warden --help
uv run warden doctor
```

## What To Be Careful With

- Do not make Hermes Kanban the authoritative database for RailWarden state.
- Do not store runtime truth in prose-only artifacts.
- Do not commit `.railwarden-runtime/`, worker logs, result JSON, local secrets, or
  worktrees.
- Do not let workers bypass owned-path validation.
- Do not treat worker-reported success as verified until RailWarden validates it.
- Do not require live provider credentials in ordinary unit tests.

## Documentation Changes

When changing orchestration behavior, update:

- `README.md`
- `docs/architecture.md`
- `docs/runtime-protocol.md`
- `docs/cli.md`
- any provider, security, context, or recovery docs affected by the change

## Security

Never commit raw credentials. If a credential is accidentally committed, rotate
it even if the later commit removes it.
