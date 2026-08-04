# Architecture

RailWarden is the deterministic substrate for agentic software work. Hermes can reason,
plan, and decide, but RailWarden owns the state machine that makes those decisions
durable and auditable.

The central boundary is:

```text
User
  -> Hermes supervisor
    -> RailWarden tools and evented runtime
      -> worker CLIs
        -> Git worktrees, tests, validation, review, merge gates
```

## Ownership Model

RailWarden owns durable truth and mechanics:

- repository-local configuration in `.railwarden/`
- runtime state in `.railwarden-runtime/state/`
- append-only events in `.railwarden-runtime/events.jsonl`
- task state and work-package contracts
- context file locations and enforcement rules
- worktree and branch creation
- tmux/session/process layout
- provider adapter command construction and health checks
- worker result validation and normalization
- validation and review artifacts
- ownership checks for changed files
- merge gates and integration queue

Hermes owns decisions:

- interpreting a user's goal
- asking an architect agent for a proposed design
- turning the design into a plan the user can approve
- deciding parallelism and provider assignment
- reacting to RailWarden events
- choosing retry, handoff, repair, replan, or ask-user actions
- writing decision rationales
- explaining completion to the user

Workers own implementation inside bounded contracts:

- inspect assigned context
- edit only owned paths unless explicitly allowed
- run local checks
- commit completed work
- produce a structured worker result

## Canonical State

The canonical RailWarden ledger is the combination of:

```text
.railwarden/
.railwarden-runtime/state/
.railwarden-runtime/events.jsonl
.railwarden-runtime/results/
.railwarden-runtime/checkpoints/
.railwarden-runtime/failures/
git branches and worktrees
validation/review artifacts
```

Hermes Kanban, dashboards, and panes should be projections over that ledger.
They are useful coordination surfaces, but they should not become a second
authoritative database for task state, commit hashes, validation evidence, or
merge readiness.

## Why Kanban Is A Projection

If Hermes Kanban owns task truth while RailWarden also owns worktrees, events, results,
and merge gates, the system risks split brain:

- a card may say "done" while RailWarden validation failed
- a card may contain a commit hash RailWarden has not verified
- a worker may be alive in RailWarden while the board says it is blocked
- merge readiness may be inferred from prose instead of deterministic checks

The safer model is:

```text
RailWarden tasks/events/results -> Hermes board/cards/dashboard
Hermes decisions -> RailWarden tool calls -> RailWarden state transitions
```

## Runtime Components

The current codebase is organized around these boundaries:

- `src/railwarden/cli/`: command-line entry points
- `src/railwarden/config/`: `.railwarden/` project configuration loading and setup
- `src/railwarden/runtime/`: events, tasks, session profiles, quota, checkpoints,
  context status, decisions, and result normalization
- `src/railwarden/engine/`: controller loop, dashboard, task launch and integration
- `src/railwarden/providers/`: provider adapters and health/failure classification
- `src/railwarden/planning/`: planning pipeline and architect-provider integration
- `src/railwarden/scheduler/`: DAG and task classification
- `src/railwarden/validation/`: worker result schema, package validation, review, and
  path ownership checks
- `src/railwarden/mcp/`: RailWarden tools exposed to Hermes or other MCP clients
- `src/railwarden/tmux/`: local pane/session runtime

## Evented Supervision

RailWarden emits facts:

```json
{
  "type": "decision_required",
  "task_id": "task-WP-004",
  "failure_kind": "wrapper_quoting_failure",
  "facts": {
    "provider": "antigravity",
    "result_json_missing": true,
    "commit_exists": false
  },
  "allowed_actions": [
    "retry_same_provider",
    "handoff_provider",
    "repair_adapter",
    "ask_user"
  ]
}
```

Hermes decides:

```text
handoff task-WP-004 to composer
```

Then Hermes calls an RailWarden tool or command, and RailWarden performs the state transition,
process launch, validation, or integration action.

## Context Ownership

Context files are committed project memory, but their ownership is split:

- RailWarden owns file existence, location, and enforcement.
- Hermes owns content and updates.
- Workers consume context references and avoid casual mutation.

See [Context Model](context.md).
