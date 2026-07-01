# Planning

Planning belongs to Hermes and the architect agent, but the approved plan must
be written back into LFG-owned contracts before execution.

## Responsibilities

Hermes:

- receives the user's goal
- asks LFG for repository/config/runtime context
- calls an architect agent
- synthesizes the architecture and work-package proposal
- explains the plan to the user
- asks for approval
- freezes approved work through LFG tools

Architect agent:

- inspects the repository
- proposes architecture and implementation strategy
- identifies risks and invariants
- decomposes work into packages
- recommends dependencies and parallelism
- drafts context files when needed

LFG:

- persists the approved package contracts
- validates the dependency DAG
- freezes ownership and prompts
- creates task state
- launches/supervises workers after approval

## Plan Shape

A useful plan should include:

- goal summary
- architecture summary
- product invariants
- security notes
- testing strategy
- work-package list
- dependency graph
- context references
- provider/parallelism recommendations
- merge and validation gates

## Approval Gate

LFG should not dispatch workers until the plan is approved and frozen. The
approval boundary prevents workers from implementing moving targets and gives
LFG a deterministic contract to enforce.

## Current Defaults

The default setup still records Antigravity as the architect provider:

```yaml
planning:
  provider: antigravity
  model: Claude Opus 4.6 (Thinking)
  approval_required: true
```

Use `lfg doctor` to verify whether the configured planner is installed and
usable.
