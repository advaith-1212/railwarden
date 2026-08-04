# Development

## Prerequisites

Supported development target: Python 3.12, Git, and `uv`; Windows, macOS, and Linux are exercised as portable Python/Git environments, while tmux is optional and primarily useful on Unix-like systems.

```bash
uv sync
uv run warden --help
uv run pytest tests/unit
uv run pytest tests/contract
uv run pytest tests/integration
uv run pytest tests/e2e
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv build
```

For editable local development, use `uv run` from this checkout. Public users should use `uvx railwarden` or `pipx install railwarden` after release publication. Use `warden init --demo` and `warden demo run` for a no-credential diagnostic. Common failures: ensure Git has a configured identity for a demo, run from a Git repository, and use `warden doctor` for optional provider, Hermes, tmux, and ignore-rule diagnostics.
