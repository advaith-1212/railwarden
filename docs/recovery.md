# Recovery

RailWarden is designed to recover from interrupted agent work by keeping deterministic
runtime state outside the chat transcript.

## Recovery Inputs

Useful recovery inputs include:

- `.railwarden-runtime/events.jsonl`
- `.railwarden-runtime/state/tasks.json`
- `.railwarden-runtime/results/`
- `.railwarden-runtime/failures/`
- `.railwarden-runtime/checkpoints/`
- worker logs
- provider health records
- Git worktrees and branches

## Common Cases

Worker committed code but did not write result JSON:

1. RailWarden detects the missing result.
2. RailWarden emits a decision-required event.
3. Hermes chooses whether to normalize from Git/tests or ask for repair.
4. RailWarden writes normalized runtime result JSON if allowed.

Useful commands:

```bash
warden failure inspect <task-id>
warden result normalize <task-id>
warden hermes supervisor --once
```

Provider wrapper failed before useful work:

1. RailWarden classifies the provider failure.
2. RailWarden emits allowed actions.
3. Hermes avoids blind retries when the failure is deterministic.
4. RailWarden handoffs or repairs according to the chosen action.

Useful commands:

```bash
warden events --limit 50
warden handoff <task-id> composer
warden retry <task-id>
```

Dirty worktree after crash:

1. RailWarden preserves the worktree.
2. RailWarden can create checkpoint commits on task branches.
3. Hermes decides whether to resume, hand off, or abandon.

Validation failure:

1. RailWarden stores validation evidence.
2. Hermes decides repair, replan, or reject.
3. RailWarden prevents merge until gates pass.

Useful commands:

```bash
warden validate <task-id>
warden review <task-id>
warden approve-merge <task-id>
warden integrate --execute
```

## Recovery Principle

Hermes can interpret ambiguity, but RailWarden should persist and verify objective
facts: process state, commits, changed files, validation results, ownership
violations, and merge readiness.
