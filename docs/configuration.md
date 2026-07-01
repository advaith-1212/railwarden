# Configuration

LFG uses committed project configuration plus ignored runtime state.

## Committed Configuration

`lfg setup --yes` creates:

```text
.lfg/project.yaml
.lfg/work_packages.yaml
.lfg/validation.yaml
.lfg/state-schema-version
context/
```

These files can be reviewed, edited, and committed with the repository.

## Runtime State

Runtime state is intentionally ignored:

```text
.lfg-runtime/
.lfg-worktrees/
work/
```

Runtime files include events, task state, logs, result JSON, checkpoints,
failure records, generated Hermes profiles, process metadata, and session
profiles. They are local execution artifacts, not source code.

## `.lfg/project.yaml`

Important sections:

- `project`: repository name, root, integration branch, worktree root, and board
  naming.
- `planning`: architect provider/model and approval policy.
- `hermes`: Hermes-facing profile/board settings. In the target architecture,
  these configure the supervisor/projection surface, not the source of truth.
- `providers`: known worker providers, capabilities, priority, and cooldown.
- `workers`: concurrency and provider list.
- `execution`: plan approval and handoff preservation behavior.
- `integration`: serialized merge and rollback policy.
- `monitoring`: dashboard/graph settings.
- `runtime`: runtime directory path.

## Work Packages

`.lfg/work_packages.yaml` contains package contracts. A package can define:

- `id`
- `name`
- `objective`
- `dependencies`
- `owned_paths`
- `forbidden_paths`
- `acceptance_criteria`
- `validation_commands`
- `preferred_providers`
- `model_profile`
- `reviewer_profile`
- `risk_level`
- `context_refs`
- `merge_policy`
- `approval_required`
- `review_required`
- `branch`
- `worktree`
- `status_notes`

`status_notes` is planner/user context only. Runtime status belongs in
`.lfg-runtime/state/tasks.json`.

## Validation

`.lfg/validation.yaml` declares mechanical commands:

```yaml
schema_version: 1.0.0
commands:
  - name: ruff
    command:
      cwd: .
      argv: ["ruff", "check", "."]
  - name: pytest
    command:
      cwd: .
      argv: ["pytest"]
```

Prefer structured `cwd` plus `argv` over shell strings. It is easier for LFG to
run, log, and classify.

## Context References

Work packages should include `context_refs` pointing to files under `context/`.
Hermes fills these files; LFG enforces that workers receive the relevant refs.

Example:

```yaml
context_refs:
  - context/PROJECT_CONTEXT.md
  - context/ARCHITECTURE.md
  - context/TEST_STRATEGY.md
```

## Secrets

Tracked config stores references such as `env:OPENAI_API_KEY`, not raw secret
values. Runtime setup secrets are written under ignored user-global or
repository-runtime paths:

```text
~/.lfg/launch-setups.d/<setup>.json
.lfg-runtime/secrets.env
```

These files must remain untracked.
