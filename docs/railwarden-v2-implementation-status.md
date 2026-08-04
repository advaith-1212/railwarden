# Historical implementation notes

This document is retained as historical transition context, not as a current implementation-status matrix. Its former manually maintained test counts, environment observations, and feature claims can become stale and must not be used as release evidence.

Current evidence is produced by the CI workflow, contract tests, the deterministic acceptance run, wheel-install smoke checks, and the commands in [evidence.md](evidence.md). For a reproducible report, record the source commit, UTC generation date, command output, and generated acceptance report path with the evidence.

The current architecture remains RailWarden’s deterministic runtime and mechanics with Hermes as the recommended supervisor. See [ARCHITECTURE.md](../ARCHITECTURE.md).
