# LFG

LFG is a Hermes Kanban companion for software-development work packages.
Hermes Agent owns orchestration: goals, Kanban task lifecycle, dependencies,
worker dispatch, worktrees, retries, dashboard state, logs, and run history.
LFG owns project-local setup, `.lfg/` configuration, work-package import, and
validation-policy rendering.

The old standalone factory runtime still exists for one transition release, but
the default workflow is now Hermes-first.

## Install

```bash
cd /path/to/lfg
uv tool install --editable .
lfg version
```

To upgrade an editable install after pulling new commits:

```bash
lfg update
```

Prerequisites:

- Python 3.12 and Git
- Hermes Agent (`hermes`)
- A running Hermes gateway for Kanban dispatch:

```bash
hermes gateway start
```

Run `lfg doctor` inside a configured project to check local tools, Hermes
version/update status, provider configuration, MCP, and ignored runtime state.

## Hermes-First Workflow

```bash
cd /path/to/project
lfg setup --yes
lfg doctor
lfg hermes status
lfg hermes bootstrap --dry-run
lfg hermes bootstrap
lfg hermes import --dry-run
lfg hermes import --apply
hermes dashboard
```

`lfg hermes bootstrap` creates or verifies the configured Hermes Kanban board,
binds a Hermes project to the repository, and checks the configured assignee
profiles. `lfg hermes import` converts `.lfg/work_packages.yaml` into durable
Hermes Kanban cards.

Each work package becomes one Kanban task. Dependencies become Kanban links.
Owned paths, forbidden paths, acceptance criteria, validation commands, commit
expectations, and skills are rendered into the task body. Code tasks default to
Hermes `worktree` workspaces and deterministic package branches.

## Configuration

Fresh projects include a Hermes section in `.lfg/project.yaml`:

```yaml
hermes:
  board: lfg-my-repo
  project_slug: my-repo
  orchestrator_profile: null
  default_assignee: default
  profile_map: {}
  workspace_mode: worktree
```

Use `profile_map` to route LFG provider names to Hermes profile/assignee names:

```yaml
hermes:
  default_assignee: default
  profile_map:
    codex: backend
    composer: frontend
```

Native Hermes profile lanes are the supported default. External CLI lanes such
as Codex CLI, Antigravity, Composer, or Grok require explicit Hermes lane/plugin
adapter work and should not be treated as paved by LFG import alone.

## Migration Note

Earlier LFG versions implemented their own scheduler, tmux factory layout,
task JSON state, quota state, handoff packets, and serialized integration loop.
Those paths are now legacy. New orchestration state should be created in Hermes
Kanban. Existing `.lfg-runtime/` state may still be useful for diagnostics or
manual migration, but Hermes Kanban is the source of truth going forward.

Legacy commands such as `lfg launch`, `lfg controller`, `lfg dashboard`, and
`lfg observability` remain available during the transition so existing projects
do not break immediately.

## Work Package Import

Given `.lfg/work_packages.yaml`, inspect the planned Kanban cards:

```bash
lfg hermes import --dry-run --json
```

Apply the import:

```bash
lfg hermes import --apply
```

LFG uses stable idempotency keys in the form
`lfg:<repo-name>:<package-id>` so repeated imports do not intentionally create
duplicate Kanban tasks.
