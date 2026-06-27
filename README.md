# LFG

LFG is a standalone, reusable agentic software-development orchestration system. It is installed as a global `lfg` CLI and operates on any Git repository through project-local `.lfg/` configuration and ignored `.lfg-runtime/` state.

LFG is not a TMOM feature and is not nested inside a product repository. TMOM's orchestration prototype was used only as source material for generic behaviors: deterministic DAG scheduling, safe worktree provisioning, lifecycle validation, serialized integration, provider health, process supervision, and tmux coordination.

## Install

```bash
cd /path/to/lfg
uv tool install --editable .
```

Prerequisites: Python 3.12, Git, tmux, and optional provider CLIs (`codex`, `claude`, `agy`, `grok`).

## Workflow

```bash
cd /path/to/project
lfg init --yes
lfg start
```

For an existing repository:

```bash
lfg adopt --dry-run /path/to/project
lfg start
```

`lfg` with no subcommand starts or attaches to the project tmux workspace. The workspace has six panes: Factory Controller, Hermes Console, Codex, Gemini, Composer, and DAG / Queue / Integration Status.

## Planning Approval

The planner interface targets Claude Opus 4.6 Thinking through a provider-neutral planner. LFG does not fake planner success. If the local `claude` CLI is unavailable or its non-interactive Thinking invocation cannot be verified, `lfg doctor` reports the exact blocker.

## Recovery

Runtime state is versioned and stored under `.lfg-runtime/`. Git remains the source of truth for code state. LFG stores supplemental state, logs, validation evidence, locks, and process identifiers under the runtime directory.

## Limitations

Provider adapters expose command construction and health checks. Automated tests use fake/disposable repositories and do not make paid model calls. Hermes is implemented as a generic interactive console and task backend; legacy `tmomcoord` behavior is documented as an adapter target.
