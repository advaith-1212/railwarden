# Recovery

LFG is designed to recover from interrupted agent work by keeping deterministic
runtime state outside the chat transcript.

## Recovery Inputs

Useful recovery inputs include:

- `.lfg-runtime/events.jsonl`
- `.lfg-runtime/state/tasks.json`
- `.lfg-runtime/results/`
- `.lfg-runtime/failures/`
- `.lfg-runtime/checkpoints/`
- worker logs
- provider health records
- Git worktrees and branches

## Common Cases

Worker committed code but did not write result JSON:

1. LFG detects the missing result.
2. LFG emits a decision-required event.
3. Hermes chooses whether to normalize from Git/tests or ask for repair.
4. LFG writes normalized runtime result JSON if allowed.

Useful commands:

```bash
lfg failure inspect <task-id>
lfg result normalize <task-id>
lfg hermes supervisor --once
```

Provider wrapper failed before useful work:

1. LFG classifies the provider failure.
2. LFG emits allowed actions.
3. Hermes avoids blind retries when the failure is deterministic.
4. LFG handoffs or repairs according to the chosen action.

Useful commands:

```bash
lfg events --limit 50
lfg handoff <task-id> composer
lfg retry <task-id>
```

Dirty worktree after crash:

1. LFG preserves the worktree.
2. LFG can create checkpoint commits on task branches.
3. Hermes decides whether to resume, hand off, or abandon.

Validation failure:

1. LFG stores validation evidence.
2. Hermes decides repair, replan, or reject.
3. LFG prevents merge until gates pass.

Useful commands:

```bash
lfg validate <task-id>
lfg review <task-id>
lfg approve-merge <task-id>
lfg integrate --execute
```

## Recovery Principle

Hermes can interpret ambiguity, but LFG should persist and verify objective
facts: process state, commits, changed files, validation results, ownership
violations, and merge readiness.
