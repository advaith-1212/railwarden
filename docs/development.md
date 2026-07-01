# Development

This project is a Python 3.12 package managed with `uv`.

## Setup

```bash
cd /path/to/lfg
uv sync
uv run lfg --help
```

Install the CLI globally from the checkout:

```bash
uv tool install --editable .
```

## Checks

Run these before publishing or opening a PR:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

Tests use disposable Git repositories and fake providers where possible. Avoid
requiring live provider credentials for unit or contract tests.

## Code Organization

- `src/lfg/cli/`: command parser and user-facing commands
- `src/lfg/config/`: project setup and configuration loading
- `src/lfg/runtime/`: durable runtime primitives
- `src/lfg/engine/`: controller, dashboard, launch/integration behavior
- `src/lfg/providers/`: external provider adapters and health classification
- `src/lfg/planning/`: architect planning pipeline
- `src/lfg/scheduler/`: DAG and package scheduling
- `src/lfg/validation/`: result validation, path ownership, review gates
- `src/lfg/mcp/`: MCP server/tool boundary
- `tests/`: unit, contract, and disposable integration tests

## Development Rules

- Keep `.lfg/` deterministic and reviewable.
- Keep `.lfg-runtime/` ignored and local.
- Do not put raw secrets in tracked config, fixtures, logs, errors, or docs.
- Prefer structured data files and schemas over prose-only state.
- Add tests when changing task state, event semantics, validation, merge gates,
  provider health, or MCP contracts.
- Treat Hermes as a decision maker, not as the owner of LFG truth.

## Public Release Hygiene

Before making or keeping the repository public:

```bash
git status --short --branch
git ls-files | rg '(^|/)(\\.env|.*secret.*|.*token.*|auth\\.json|credentials)'
rg -n '(sk-[A-Za-z0-9]|ghp_[A-Za-z0-9]|github_pat_|BEGIN .*PRIVATE KEY)'
```

Expected tracked runtime-sensitive files are code such as
`src/lfg/runtime/secrets.py`, not real secret material.
