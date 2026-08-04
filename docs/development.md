# Development

This project is a Python 3.12 package managed with `uv`.

## Setup

```bash
cd /path/to/railwarden
uv sync
uv run warden --help
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

- `src/railwarden/cli/`: command parser and user-facing commands
- `src/railwarden/config/`: project setup and configuration loading
- `src/railwarden/runtime/`: durable runtime primitives
- `src/railwarden/engine/`: controller, dashboard, launch/integration behavior
- `src/railwarden/providers/`: external provider adapters and health classification
- `src/railwarden/planning/`: architect planning pipeline
- `src/railwarden/scheduler/`: DAG and package scheduling
- `src/railwarden/validation/`: result validation, path ownership, review gates
- `src/railwarden/mcp/`: MCP server/tool boundary
- `tests/`: unit, contract, and disposable integration tests

## Development Rules

- Keep `.railwarden/` deterministic and reviewable.
- Keep `.railwarden-runtime/` ignored and local.
- Do not put raw secrets in tracked config, fixtures, logs, errors, or docs.
- Prefer structured data files and schemas over prose-only state.
- Add tests when changing task state, event semantics, validation, merge gates,
  provider health, or MCP contracts.
- Treat Hermes as a decision maker, not as the owner of RailWarden truth.

## Public Release Hygiene

Before making or keeping the repository public:

```bash
git status --short --branch
git ls-files | rg '(^|/)(\\.env|.*secret.*|.*token.*|auth\\.json|credentials)'
rg -n '(sk-[A-Za-z0-9]|ghp_[A-Za-z0-9]|github_pat_|BEGIN .*PRIVATE KEY)'
```

Expected tracked runtime-sensitive files are code such as
`src/railwarden/runtime/secrets.py`, not real secret material.
