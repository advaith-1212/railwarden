# Release process

Release authority currently belongs to the project maintainer. Before a release: review the changelog and compatibility impact; run `uv lock`, `uv sync --frozen`, format, lint, mypy, full tests with coverage, deterministic acceptance, and `uv build`; install the wheel in a fresh environment; run `warden --help`, `warden version`, `warden doctor`, and `import railwarden`; and verify release artifacts and checksums.

Create a GitHub Release from a semantic-version tag only after those checks pass, targeting `main`. Publishing the release triggers `.github/workflows/pypi-publish.yml`, which repeats verification, builds distributions, attaches the verified wheel and sdist to that GitHub Release, then uploads them to PyPI through Trusted Publishing. Configure the PyPI trusted publisher for this repository, this workflow file, and the protected `pypi` environment; require maintainer approval for that environment. Do not add a long-lived PyPI token to repository secrets.
