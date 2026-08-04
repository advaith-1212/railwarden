# Context Model

Context is committed project memory. It makes the plan and worker prompts
grounded in stable files instead of transient chat history.

## Ownership

```text
RailWarden owns file existence, location, and enforcement.
Hermes owns content and updates.
Workers consume context refs and avoid casual mutation.
```

`warden setup --yes` creates the standard context files:

```text
context/PROJECT_CONTEXT.md
context/ARCHITECTURE.md
context/PRODUCT_INVARIANTS.md
context/SECURITY_MODEL.md
context/TEST_STRATEGY.md
context/CONTRIBUTING_AGENTS.md
```

## File Purposes

`PROJECT_CONTEXT.md`

Repository purpose, product surface, important directories, current state, and
known constraints.

`ARCHITECTURE.md`

Stable boundaries, modules, data flow, interfaces, and architectural decisions.

`PRODUCT_INVARIANTS.md`

Behavior that workers must preserve. These are non-negotiable product rules.

`SECURITY_MODEL.md`

Trust boundaries, credentials, sensitive operations, threat assumptions, and
security-sensitive paths.

`TEST_STRATEGY.md`

Required checks for package validation, integration validation, and release
review.

`CONTRIBUTING_AGENTS.md`

Rules for worker agents: how to inspect, edit, validate, commit, write results,
and hand off.

## Flow

```text
User goal enters Hermes
-> Hermes calls architect agent
-> Architect proposes architecture, invariants, risks, tests, and package split
-> Hermes writes/updates context files
-> Hermes creates work packages with context_refs
-> RailWarden injects or enforces those refs in worker prompts
-> Workers read relevant context before editing
```

## Work Package References

Each meaningful package should point to the context it needs:

```yaml
id: WP-004
name: Implement provider failure classification
objective: Classify wrapper, auth, quota, timeout, result, and validation failures.
owned_paths:
  - src/railwarden/providers/health.py
  - tests/unit/test_provider_health.py
context_refs:
  - context/ARCHITECTURE.md
  - context/TEST_STRATEGY.md
  - context/CONTRIBUTING_AGENTS.md
```

## Mutation Rules

Hermes may update context when:

- architecture changes
- product invariants are discovered
- security assumptions change
- test strategy changes
- agent contribution rules change

Workers should not casually mutate context while implementing a task. If a
worker discovers stale context, it should report that finding in its result or
ask for a context-update task.
