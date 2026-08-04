# Evidence and verification

Use CI for reproducible format, lint, typing, test, build, wheel-install, CLI, import, ignore-rule, disposable-repository, and deterministic acceptance evidence. Locally run:

```bash
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest --cov=railwarden --cov-report=term-missing
uv build
```

Then install the generated wheel in a fresh environment and run `warden --help`, `warden version`, `warden doctor`, `python -c "import railwarden"`, and the demo. Contract examples are validated by contract tests. Runtime safety checks include ignored runtime directories, path ownership, secret redaction, clean worktree recovery, validation rollback, and persisted events. This project does not claim external adoption, benchmarks, or production case studies.
