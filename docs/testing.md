# Testing

- `tests/unit/` exercises isolated runtime behavior and failure handling.
- `tests/contract/` validates schemas and fixture compatibility.
- `tests/integration/` exercises Git, config, worktrees, and merge mechanics.
- `tests/e2e/` runs the deterministic credential-free acceptance lifecycle.

Run all checks with `uv run pytest --cov=railwarden --cov-report=term-missing`. The coverage threshold is a regression floor, not a claim of exhaustive safety. Run a focused test with `uv run pytest tests/unit/test_railwarden_v2_runtime.py -k quota`.

No test requires live credentials. Live-provider verification, if enabled by a maintainer, is separate from CI and follows the provider runbook.
