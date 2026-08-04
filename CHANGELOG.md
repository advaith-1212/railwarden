# Changelog

All notable changes are documented here following Keep a Changelog principles. This project is pre-1.0; no historical release notes are fabricated.

## [Unreleased]

### Fixed

- Reap exited managed POSIX process-group leaders before probing group liveness,
  preventing false termination failures and unnecessary waits in Linux CI.

### Added

- Deterministic credential-free demo and acceptance report.
- CI, package wheel smoke checks, contracts, contributor and release documentation.

## [v0.1.0]

Initial tagged release.
