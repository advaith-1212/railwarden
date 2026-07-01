# Contributing

LFG is an experimental multi-agent software-development runtime. Contributions
should preserve the core authority split:

```text
LFG owns durable state and mechanics.
Hermes owns decisions.
Workers own scoped implementation.
```

## Local Setup

```bash
git clone https://github.com/advaith-1212/lfg.git
cd lfg
uv sync
uv run lfg --help
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
uv run lfg --help
uv run lfg doctor
```

## What To Be Careful With

- Do not make Hermes Kanban the authoritative database for LFG state.
- Do not store runtime truth in prose-only artifacts.
- Do not commit `.lfg-runtime/`, worker logs, result JSON, local secrets, or
  worktrees.
- Do not let workers bypass owned-path validation.
- Do not treat worker-reported success as verified until LFG validates it.
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
