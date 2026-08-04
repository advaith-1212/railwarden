# User Guide

This guide is the practical path for a new user. The short model is:

```text
RailWarden owns the world and ledger.
Hermes watches the world and decides.
Workers do scoped implementation.
```

## 1. Install RailWarden

Prerequisites:

- Python 3.12
- Git
- `uv`
- `tmux` for the local pane runtime
- Hermes Agent for the supervisor/console flow
- Provider CLIs or API keys for the workers you plan to run

Install from a checkout:

```bash
git clone https://github.com/advaith-1212/railwarden.git
cd railwarden
uv tool install --editable .
warden version
```

Update later:

```bash
warden update
```

For development:

```bash
uv sync
uv run warden --help
```

## 2. Prepare A Target Repository

In the repository you want RailWarden to manage:

```bash
cd /path/to/project
warden setup --yes
warden doctor
```

Setup creates committed configuration:

```text
.railwarden/project.yaml
.railwarden/work_packages.yaml
.railwarden/validation.yaml
.railwarden/state-schema-version
context/
```

It also ensures local runtime paths stay ignored:

```text
.railwarden-runtime/
.railwarden-worktrees/
```

Run this before serious work:

```bash
warden context status
```

If the status says `needs_population`, fill the context files before asking
agents to implement large changes.

## 3. Populate Project Context

RailWarden creates these files:

```text
context/PROJECT_CONTEXT.md
context/ARCHITECTURE.md
context/PRODUCT_INVARIANTS.md
context/SECURITY_MODEL.md
context/TEST_STRATEGY.md
context/CONTRIBUTING_AGENTS.md
```

Hermes should write the content. RailWarden owns the location and enforcement. Workers
read context refs and should not casually mutate them.

You can update context through the CLI:

```bash
warden context write ARCHITECTURE.md --content-file /tmp/ARCHITECTURE.md
warden context status
```

Good context answers:

- What is this repository for?
- Which modules and boundaries matter?
- What product behavior must not regress?
- What security assumptions matter?
- Which tests prove package, integration, and release quality?
- What should worker agents do before editing, committing, and handing off?

## 4. Configure Work Packages

Work packages live in:

```text
.railwarden/work_packages.yaml
```

A useful package includes:

```yaml
id: WP-004
name: Provider failure classification
objective: Classify wrapper, auth, quota, timeout, result, and validation failures.
dependencies:
  - WP-001
owned_paths:
  - src/railwarden/providers/health.py
  - tests/unit/test_provider_health.py
forbidden_paths:
  - .env
context_refs:
  - context/ARCHITECTURE.md
  - context/TEST_STRATEGY.md
  - context/CONTRIBUTING_AGENTS.md
acceptance_criteria:
  - wrapper quoting failures are distinct from provider auth failures
  - quota failures produce handoff-appropriate facts
validation_commands:
  - name: provider-health-tests
    command:
      cwd: .
      argv: ["uv", "run", "pytest", "tests/unit/test_provider_health.py"]
```

Best practice: keep packages small enough that one worker can finish and commit
them, but large enough to produce a meaningful integrated result.

## 5. Launch The Runtime

Start the local runtime:

```bash
warden launch --preset guided
```

Useful variants:

```bash
warden launch --preset default-dev-shop
warden launch --preset local-only
warden launch --profile my-session
warden launch --no-attach
```

Attach or stop:

```bash
warden attach
warden stop
warden restart
```

## 6. Plan, Approve, And Execute

The intended flow is:

```text
User gives goal to Hermes
-> Hermes reads RailWarden state/context
-> Hermes asks architect for a plan
-> Hermes presents the plan
-> user approves
-> Hermes freezes contracts through RailWarden
-> RailWarden launches workers
-> Hermes supervises events
```

CLI equivalents:

```bash
warden plan "implement the goal"
warden approve plan
warden approve-contracts
warden controller --once
```

Use Hermes for the conversation and decisions. Use RailWarden for durable state
transitions.

## 7. Supervise The Run

Run the supervisor once:

```bash
warden hermes supervisor --once
```

Run it continuously:

```bash
warden hermes supervisor
```

The supervisor reads RailWarden events, chooses allowed actions, records decisions, and
updates RailWarden state. Its cursor lives in:

```text
.railwarden-runtime/supervisor/state.json
```

Decision records live in:

```text
.railwarden-runtime/decisions.jsonl
```

## 8. Observe Runtime State

Useful commands:

```bash
warden snapshot
warden status
warden events --cursor 0 --limit 100
warden dashboard
warden observability
warden logs
warden inspect <task-id>
```

Treat `.railwarden-runtime/events.jsonl`, task state, result JSON, validation evidence,
and Git worktrees as the facts. Treat Kanban and panes as views.

## 9. Recover From Failures

Inspect failure facts:

```bash
warden failure inspect <task-id>
```

Common actions:

```bash
warden retry <task-id>
warden handoff <task-id> composer
warden result normalize <task-id>
warden validate <task-id>
warden review <task-id>
warden approve-merge <task-id>
warden integrate --execute
```

Normalize only when the worker has clean committed work and the missing result
can be safely synthesized from Git/tests.

## 10. Best Practices

Keep RailWarden authoritative:

- Do not use Hermes Kanban as the task database.
- Do not manually edit `.railwarden-runtime/state/tasks.json` unless repairing a run
  deliberately.
- Record decisions through RailWarden when possible.

Write useful context:

- Populate context before launching workers.
- Keep context concise, factual, and project-specific.
- Update context when architecture, security, or testing rules change.

Design good work packages:

- Give each package explicit owned paths.
- Declare forbidden paths for sensitive or shared files.
- Include context refs.
- Include package-specific validation commands.
- Keep dependencies explicit.

Run safely:

- Check `warden doctor` before launch.
- Prefer `warden launch --preset guided` for new projects.
- Watch `warden events` and `warden failure inspect`.
- Let validation/review/merge gates run before declaring completion.

Protect secrets:

- Store env references in tracked files, not raw keys.
- Keep `.railwarden-runtime/`, logs, transcripts, setup secrets, and worktrees ignored.
- Rotate any credential that was ever committed.

Develop RailWarden itself:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```
