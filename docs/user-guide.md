# User Guide

This guide is the practical path for a new user. The short model is:

```text
LFG owns the world and ledger.
Hermes watches the world and decides.
Workers do scoped implementation.
```

## 1. Install LFG

Prerequisites:

- Python 3.12
- Git
- `uv`
- `tmux` for the local pane runtime
- Hermes Agent for the supervisor/console flow
- Provider CLIs or API keys for the workers you plan to run

Install from a checkout:

```bash
git clone https://github.com/advaith-1212/lfg.git
cd lfg
uv tool install --editable .
lfg version
```

Update later:

```bash
lfg update
```

For development:

```bash
uv sync
uv run lfg --help
```

## 2. Prepare A Target Repository

In the repository you want LFG to manage:

```bash
cd /path/to/project
lfg setup --yes
lfg doctor
```

Setup creates committed configuration:

```text
.lfg/project.yaml
.lfg/work_packages.yaml
.lfg/validation.yaml
.lfg/state-schema-version
context/
```

It also ensures local runtime paths stay ignored:

```text
.lfg-runtime/
.lfg-worktrees/
```

Run this before serious work:

```bash
lfg context status
```

If the status says `needs_population`, fill the context files before asking
agents to implement large changes.

## 3. Populate Project Context

LFG creates these files:

```text
context/PROJECT_CONTEXT.md
context/ARCHITECTURE.md
context/PRODUCT_INVARIANTS.md
context/SECURITY_MODEL.md
context/TEST_STRATEGY.md
context/CONTRIBUTING_AGENTS.md
```

Hermes should write the content. LFG owns the location and enforcement. Workers
read context refs and should not casually mutate them.

You can update context through the CLI:

```bash
lfg context write ARCHITECTURE.md --content-file /tmp/ARCHITECTURE.md
lfg context status
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
.lfg/work_packages.yaml
```

A useful package includes:

```yaml
id: WP-004
name: Provider failure classification
objective: Classify wrapper, auth, quota, timeout, result, and validation failures.
dependencies:
  - WP-001
owned_paths:
  - src/lfg/providers/health.py
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
lfg launch --preset guided
```

Useful variants:

```bash
lfg launch --preset default-dev-shop
lfg launch --preset local-only
lfg launch --profile my-session
lfg launch --no-attach
```

Attach or stop:

```bash
lfg attach
lfg stop
lfg restart
```

## 6. Plan, Approve, And Execute

The intended flow is:

```text
User gives goal to Hermes
-> Hermes reads LFG state/context
-> Hermes asks architect for a plan
-> Hermes presents the plan
-> user approves
-> Hermes freezes contracts through LFG
-> LFG launches workers
-> Hermes supervises events
```

CLI equivalents:

```bash
lfg plan "implement the goal"
lfg approve plan
lfg approve-contracts
lfg controller --once
```

Use Hermes for the conversation and decisions. Use LFG for durable state
transitions.

## 7. Supervise The Run

Run the supervisor once:

```bash
lfg hermes supervisor --once
```

Run it continuously:

```bash
lfg hermes supervisor
```

The supervisor reads LFG events, chooses allowed actions, records decisions, and
updates LFG state. Its cursor lives in:

```text
.lfg-runtime/supervisor/state.json
```

Decision records live in:

```text
.lfg-runtime/decisions.jsonl
```

## 8. Observe Runtime State

Useful commands:

```bash
lfg snapshot
lfg status
lfg events --cursor 0 --limit 100
lfg dashboard
lfg observability
lfg logs
lfg inspect <task-id>
```

Treat `.lfg-runtime/events.jsonl`, task state, result JSON, validation evidence,
and Git worktrees as the facts. Treat Kanban and panes as views.

## 9. Recover From Failures

Inspect failure facts:

```bash
lfg failure inspect <task-id>
```

Common actions:

```bash
lfg retry <task-id>
lfg handoff <task-id> composer
lfg result normalize <task-id>
lfg validate <task-id>
lfg review <task-id>
lfg approve-merge <task-id>
lfg integrate --execute
```

Normalize only when the worker has clean committed work and the missing result
can be safely synthesized from Git/tests.

## 10. Best Practices

Keep LFG authoritative:

- Do not use Hermes Kanban as the task database.
- Do not manually edit `.lfg-runtime/state/tasks.json` unless repairing a run
  deliberately.
- Record decisions through LFG when possible.

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

- Check `lfg doctor` before launch.
- Prefer `lfg launch --preset guided` for new projects.
- Watch `lfg events` and `lfg failure inspect`.
- Let validation/review/merge gates run before declaring completion.

Protect secrets:

- Store env references in tracked files, not raw keys.
- Keep `.lfg-runtime/`, logs, transcripts, setup secrets, and worktrees ignored.
- Rotate any credential that was ever committed.

Develop LFG itself:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```
