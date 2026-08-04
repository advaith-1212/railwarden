# Provider adapters

Provider adapters translate a configured provider into a supervised process lifecycle. They must declare configuration, executable/auth requirements, timeout behavior, health signals, normalized result handling, and failure classification. Secrets are references or environment variables, never persisted raw in project state; runtime output must pass redaction.

Add an adapter by implementing the adapter interface in `src/railwarden/providers/`, registering it, documenting its capabilities and limits, and adding fake-process tests for success, process failure, timeout, quota/rate-limit, malformed output, and handoff. Fake providers are test infrastructure only and must not be advertised as production adapters. See `docs/test-fixtures.md`.
