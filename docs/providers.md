# Providers

Providers are external execution backends. RailWarden should adapt to them, supervise
them, and classify their failures, but provider processes should not own the
orchestration state.

## Roles

Common roles:

- Hermes supervisor: reasons over RailWarden state and chooses actions.
- Architect: proposes architecture, context, DAG, and package contracts.
- Coding workers: implement scoped package contracts.
- Reviewer/validator: checks package output independently where configured.

## Adapter Responsibilities

Provider adapters should expose:

- health checks
- command construction
- launch/cancel boundaries
- process metadata
- log locations
- result collection
- failure classification

Adapters should convert provider-specific behavior into RailWarden facts. For example:

```text
provider=antigravity
failure_kind=wrapper_quoting_failure
result_json_missing=true
commit_exists=false
allowed_actions=[retry_same_provider, handoff_provider, repair_adapter, ask_user]
```

Hermes then decides what to do with those facts.

## Failure Classification

Good failure classification is critical. RailWarden should distinguish:

- missing or invalid credentials
- wrapper/quoting failures
- provider process crashes
- quota exhaustion
- missing worker result JSON
- dirty worktree with no committed result
- validation failure
- ownership violation
- merge conflict
- provider timeout

Do not hide all failures behind "retry." A precise failure kind lets Hermes
choose a better action.

## Secrets

Provider credentials should be supplied through environment references or
ignored runtime setup files. Tracked files should contain references such as
`env:OPENAI_API_KEY`, not key values.
