# Release process

Release authority currently belongs to the project maintainer. Before a release: review the changelog and compatibility impact; run `uv lock`, `uv sync --frozen`, format, lint, mypy, full tests with coverage, deterministic acceptance, and `uv build`; install the wheel in a fresh environment; run `warden --help`, `warden version`, `warden doctor`, and `import railwarden`; and verify release artifacts and checksums.

Tag only after those checks pass. PyPI publication must use trusted publishing configured in repository settings and is never performed by this repository automatically without maintainer authorization. Publish release notes from verified evidence, not test counts alone.
