# Runtime Protocol

The target protocol is "LFG Evented Runtime + Hermes Supervisor."

## Sequence

```text
User launches LFG
-> LFG creates runtime environment
-> Hermes starts inside that environment
-> User gives goal to Hermes
-> Hermes asks LFG for repo/context/state
-> Hermes asks architect agent to generate plan
-> Architect writes architecture/context proposal
-> Hermes reviews/synthesizes plan
-> Hermes presents plan to user
-> User approves
-> Hermes calls LFG tools to freeze plan/work packages/DAG
-> LFG records source-of-truth state
-> Hermes assigns worker agents
-> LFG launches/supervises worker processes
-> Workers edit worktrees
-> LFG validates outputs mechanically
-> Hermes reacts to LFG events/failures
-> Hermes decides retry/handoff/repair/replan/ask-user
-> LFG executes the chosen action
-> LFG owns integration/merge gates
-> Hermes declares completion after LFG verifies completion
```

## LFG Emits Facts

LFG should emit factual events and constraints, not vague prose.

Example:

```json
{
  "type": "decision_required",
  "task_id": "task-WP-004",
  "provider": "antigravity",
  "failure_kind": "wrapper_quoting_failure",
  "result_json_missing": true,
  "commit_exists": false,
  "owned_paths": ["src/example.py", "tests/test_example.py"],
  "allowed_actions": [
    "retry_same_provider",
    "handoff_provider",
    "repair_adapter",
    "ask_user"
  ]
}
```

These events belong in `.lfg-runtime/events.jsonl` and related failure records
under `.lfg-runtime/failures/`.

## Hermes Chooses Actions

Hermes reads LFG state, interprets the event, records a rationale, and calls an
LFG command or MCP tool.

Example decision:

```text
Diagnosis: Antigravity wrapper failed before useful work.
Action: handoff_provider.
Rationale: Retrying the same wrapper is likely deterministic; Composer can
continue the package contract.
Tool call: lfg handoff task-WP-004 composer
```

LFG then executes the handoff and records the new state.

## Worker Result Contract

Workers should write structured result JSON to the expected result path. LFG
then validates and copies/normalizes the result into `.lfg-runtime/results/`.

If a worker commits valid work but misses result JSON, LFG may emit a
decision-required event. Hermes can choose to synthesize the result from Git and
tests, or ask the worker to repair its output.

## Supervisor Loop

The Hermes supervisor loop should repeatedly:

1. Read latest LFG events.
2. Read task state.
3. Read provider health and failure logs.
4. Classify the situation if LFG has not already done so.
5. Choose the next action.
6. Call the relevant LFG tool.
7. Append a Hermes decision record.
8. Continue until LFG verifies completion or asks for human input.

The Hermes console is the user interaction surface. The supervisor loop is the
reactive brain.

Current CLI entry point:

```bash
lfg hermes supervisor
lfg hermes supervisor --once
```

The supervisor stores its cursor in:

```text
.lfg-runtime/supervisor/state.json
```

Hermes decision records are appended to:

```text
.lfg-runtime/decisions.jsonl
```

Failure details are written under:

```text
.lfg-runtime/failures/<task-id>.json
```

Manual inspection and repair commands:

```bash
lfg events --cursor 0 --limit 100
lfg failure inspect <task-id>
lfg result normalize <task-id>
lfg retry <task-id>
lfg handoff <task-id> <provider>
```

## Completion Rule

Hermes should only declare completion after LFG verifies:

- all required tasks are terminal
- validation gates passed
- review gates passed when required
- ownership checks passed
- integration/merge gates passed
- no blocking decision-required events remain
