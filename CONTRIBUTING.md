# Contributing to RailWarden

Thank you for improving RailWarden. Start with [DEVELOPMENT.md](DEVELOPMENT.md), [ARCHITECTURE.md](ARCHITECTURE.md), and the [contract index](docs/contracts/README.md).

## Contribution workflow

Keep changes scoped, preserve the kernel boundary, add tests at the lowest useful layer, and run format, lint, typing, tests, and a build before opening a pull request. Include documentation whenever behavior, CLI output, configuration, contracts, provider behavior, or safety expectations change.

## Invariants

- Durable runtime state, not agent chat, is authoritative for execution facts.
- Workers operate in scoped worktrees and cannot self-certify integration.
- Path ownership, validation evidence, and merge gates are mechanical controls.
- Hermes remains the recommended supervisor; do not imply supervisor neutrality.
- Never commit credentials, generated runtime state, or generated worktrees.

Contract changes need schema fixtures, compatibility analysis, and migration documentation. Breaking changes need release-authority review. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md); do not open a public issue for a suspected secret or vulnerability.

Pull requests should state motivation, tests, docs, contract/compatibility/migration impact, and security implications. See the repository PR template and [governance](docs/governance.md).
