# RailWarden v2 Implementation Status

Pivot update: RailWarden is moving back to an evented-runtime model with Hermes as a
supervisor. RailWarden runtime state, events, worktrees, results, validation evidence,
and merge gates are authoritative. Hermes Kanban remains useful as a
planning/coordination projection, but it should not own canonical task truth.
The matrix below records implementation status from the v2 runtime work and may
include transition-era labels.

Updated: 2026-06-28

Baseline before implementation:

- Branch: `main...origin/main`
- Pre-existing modified files: `src/railwarden/engine/controller.py`, `src/railwarden/provisioning/worktrees.py`, `src/railwarden/scheduler/classifier.py`, `src/railwarden/validation/runner.py`, `src/railwarden/validation/worker_result.py`
- Baseline checks:
  - `uv run ruff format --check .`: failed, 11 files needed formatting
  - `uv run ruff check .`: failed, 3 import-order findings
  - `uv run mypy`: passed
  - `uv run pytest`: passed, 31 tests

Current automated verification:

- `uv lock`: passed; resolved `pydantic-ai==2.0.0` and `langgraph==1.2.6`
- `uv run ruff format --check .`: passed, 75 files formatted
- `uv run ruff check .`: passed
- `uv run mypy`: passed, 75 source files
- `uv run pytest`: passed, 48 tests
- CLI smoke:
  - `uv run warden --help`: passed; includes `launch`, `model`, `agent`, `quota`, `checkpoint`, `observability`, `mcp`
  - `uv run warden launch --help`: passed
  - `uv run warden model list`: passed
  - `uv run warden agent --help`: passed
  - `uv run warden quota --help`: passed
  - `uv run warden checkpoint --help`: passed
  - `uv run warden mcp --help`: passed
  - Disposable initialized repo runtime smoke passed for `warden model doctor`, `warden model configure openai:gpt-5.2`, `warden agent list`, `warden quota status`, and `warden observability`
- Runtime ignore check: `git check-ignore -q .railwarden-runtime` passed

## Matrix

| Plan item | Status | Implementation files | Tests / evidence |
|---|---:|---|---|
| `warden launch` production entry point | Implemented | `src/railwarden/cli/main.py`, `src/railwarden/tmux/session.py`, `src/railwarden/hermes/profile.py` | CLI smoke `uv run warden launch --help`; full pytest |
| `warden start` legacy/simple alias | Preserved | `src/railwarden/cli/main.py`, `src/railwarden/tmux/session.py` | Existing tests plus CLI smoke |
| Interactive launch wizard every run, with non-interactive defaults; selects session profile, orchestrator, architect, workers, reviewer/validator when present, budget label, quota thresholds, manual token budget, and fallback/swap policy | Implemented | `src/railwarden/cli/main.py`, `src/railwarden/runtime/session.py` | `test_launch_wizard_persists_budget_quota_fallback_and_review_models`; `test_hermes_profile_generation_is_runtime_only`; CLI smoke |
| Tmux factory and observability windows | Implemented | `src/railwarden/tmux/session.py` | `test_tmux_launch_layout_has_factory_and_observability` |
| Pane titles include role/executor/provider/model/health/budget signal | Implemented | `src/railwarden/tmux/session.py` | `test_tmux_launch_layout_has_factory_and_observability` asserts role, executor, provider, model, and observability command |
| Hermes remains external; no fork/vendor | Implemented | `src/railwarden/hermes/profile.py`, `pyproject.toml` | `command -v hermes` found `/Users/advaith/.local/bin/hermes`; generated profile test |
| Runtime-generated Hermes profile under ignored runtime state | Implemented | `src/railwarden/hermes/profile.py`, `src/railwarden/runtime/secrets.py` | `test_hermes_profile_generation_is_runtime_only`; `.railwarden-runtime` ignored |
| Hermes configured with orchestrator provider/model, RailWarden MCP, skills, factory instructions, local terminal backend | Implemented | `src/railwarden/hermes/profile.py` | `test_hermes_profile_generation_is_runtime_only` |
| RailWarden MCP server required tools | Implemented plus skill tools over real MCP stdio | `src/railwarden/mcp/server.py` | `test_mcp_tool_schemas_cover_required_tools`; `test_mcp_stdio_server_lists_and_calls_railwarden_tools` |
| Replace lightweight `warden hermes` path with launch-generated Hermes profile | Implemented for production launch; legacy console remains compatibility path | `src/railwarden/cli/main.py`, `src/railwarden/hermes/profile.py` | CLI smoke; generated profile test |
| Model ref parser, normalized refs, and provider registry loading | Implemented | `src/railwarden/runtime/model_refs.py`, `src/railwarden/models/registry.py` | `test_model_ref_parsing_normalizes_supported_shapes`, `test_provider_registry_lists_valid_default_model_refs`, `test_model_profile_uses_pydanticai_compatible_ref` |
| ModelProfile, AgentRole, AgentInstance, SessionProfile, QuotaState | Implemented | `src/railwarden/runtime/session.py` | `test_session_profile_serializes_without_raw_secret`, quota tests |
| Persist launch state in `.railwarden-runtime/state/session-profile.json` | Implemented | `src/railwarden/runtime/session.py`, `src/railwarden/cli/main.py` | `test_session_profile_serializes_without_raw_secret` |
| Durable project defaults in `.railwarden/project.yaml` or `.railwarden/factory.yaml` | Implemented with compatibility | `src/railwarden/config/init.py`, `src/railwarden/config/loader.py` | Existing config tests; loader supports `factory.yaml` first |
| Never store raw secrets in tracked `.railwarden` | Implemented | `src/railwarden/runtime/secrets.py`, `src/railwarden/runtime/session.py`, `src/railwarden/hermes/profile.py` | `test_session_profile_serializes_without_raw_secret`, `test_secret_redaction_covers_keys_tokens_and_env` |
| Secrets from env/Hermes `.env`/runtime secrets with restrictive permissions | Implemented | `src/railwarden/runtime/secrets.py`, `src/railwarden/hermes/profile.py` | `test_hermes_profile_generation_is_runtime_only` |
| PydanticAI typed RailWarden-owned model-call boundary | Implemented with provider-specific model translation for OpenAI, Anthropic, Gemini, Azure Foundry/OpenAI-compatible, and Ollama | `src/railwarden/runtime/typed_agents.py`, `pyproject.toml` | `test_pydanticai_agent_translates_railwarden_model_refs`; strict mypy; dependency resolved to `pydantic-ai==2.0.0` |
| LangGraph workflow/checkpoint/observability state only | Implemented as runtime checkpoints with LangGraph availability check | `src/railwarden/runtime/workflow.py`, `src/railwarden/cli/main.py`, `pyproject.toml` | Strict mypy; dependency resolved to `langgraph==1.2.6`; observability CLI smoke |
| RailWarden task state remains source of truth | Preserved | `src/railwarden/runtime/tasks.py`, `src/railwarden/engine/controller.py` | Existing controller/disposable repo tests |
| Quota policies and confidence levels | Implemented | `src/railwarden/runtime/session.py`, `src/railwarden/runtime/quota.py` | `test_low_quota_prevents_new_task_launch` |
| Track usage from metadata/manual budget surfaces | Implemented manual/runtime API; provider metadata hooks prepared | `src/railwarden/runtime/quota.py`, `src/railwarden/cli/main.py` | `warden quota status/set` CLI; quota unit test |
| Check quota before model call or worker task | Implemented for worker launch | `src/railwarden/engine/controller.py` | `test_low_quota_prevents_new_task_launch` |
| Low quota pauses agent, writes handoff packet, preserves checkpoint when a worktree exists, and marks task `handoff_needed` | Implemented | `src/railwarden/engine/controller.py`, `src/railwarden/runtime/handoff.py`, `src/railwarden/runtime/checkpoints.py`, `src/railwarden/runtime/events.py` | `test_low_quota_prevents_new_task_launch` |
| `warden agent swap <agent_id> --to <model-ref>` updates session, writes handoff packet for active work, sets provider override, and relaunches with the swapped provider/model | Implemented | `src/railwarden/cli/main.py`, `src/railwarden/engine/controller.py`, `src/railwarden/mcp/server.py` | `test_agent_swap_creates_handoff_and_relaunches_with_override`; CLI smoke; strict mypy |
| `warden agent pause/resume` | Implemented | `src/railwarden/cli/main.py`, `src/railwarden/mcp/server.py` | CLI smoke; strict mypy |
| Checkpoint commits only on task branches | Implemented | `src/railwarden/runtime/checkpoints.py`, `src/railwarden/mcp/server.py`, `src/railwarden/cli/main.py` | `test_checkpoint_filters_disallowed_files` |
| Checkpoint metadata under `.railwarden-runtime/checkpoints/` | Implemented | `src/railwarden/runtime/checkpoints.py` | `test_checkpoint_filters_disallowed_files` |
| Resume reloads session profile/tasks/quotas/workflow and tmux metadata | Implemented via `launch` reattach and file-backed loaders | `src/railwarden/cli/main.py`, `src/railwarden/tmux/session.py`, `src/railwarden/runtime/session.py`, `src/railwarden/runtime/workflow.py` | CLI smoke; strict mypy |
| `.railwarden/skills` and `.railwarden-runtime/skills` | Implemented | `src/railwarden/runtime/skills.py`, `src/railwarden/hermes/profile.py` | `test_worker_prompt_includes_skills_and_mcp_routing` |
| Runtime skill generation and promotion path | Implemented through MCP | `src/railwarden/runtime/skills.py`, `src/railwarden/mcp/server.py` | `test_mcp_tool_schemas_cover_required_tools` |
| Worker prompts include required skills and MCP routing | Implemented | `src/railwarden/engine/controller.py`, `src/railwarden/runtime/skills.py` | `test_worker_prompt_includes_skills_and_mcp_routing` |
| Public CLI additions | Implemented | `src/railwarden/cli/main.py` | CLI smoke output listed above |
| Extended `warden doctor` checks | Implemented for Hermes, tmux, provider adapters, selected API credential refs, configured Ollama/Azure endpoints, PydanticAI, LangGraph, MCP stdio startup, and runtime ignore | `src/railwarden/cli/main.py`, `src/railwarden/runtime/doctor.py`, `src/railwarden/providers/adapters.py` | `test_doctor_report_checks_credentials_endpoints_mcp_and_ignore`; `test_mcp_stdio_server_lists_and_calls_railwarden_tools`; strict mypy |
| V1 config compatibility/migration/defaulting | Preserved and extended | `src/railwarden/config/init.py`, `src/railwarden/config/loader.py` | Existing config/planner/controller tests |
| No raw secrets in logs/fixtures/snapshots/errors | Implemented for RailWarden-owned runtime serialization/redaction | `src/railwarden/runtime/secrets.py`, `src/railwarden/runtime/checkpoints.py`, `src/railwarden/runtime/session.py` | Secret redaction tests |
| Fake providers are test infrastructure only | Preserved | `tests/`, `src/railwarden/providers/adapters.py` | Existing integration tests and no fake production adapter added |
| Disposable repo lifecycle | Preserved | `tests/integration/test_disposable_repo.py` | Full pytest pass |
| Failure/recovery, crash-resume, and quota swap | Implemented for provider failure, low-quota handoff, dirty-worktree crash resume with checkpoint, and explicit quota swap | `src/railwarden/engine/controller.py`, `src/railwarden/runtime/handoff.py`, `src/railwarden/runtime/quota.py`, `src/railwarden/cli/main.py` | Existing dead-quota handoff test; `test_low_quota_prevents_new_task_launch`; `test_agent_swap_creates_handoff_and_relaunches_with_override`; `test_crash_resume_checkpoints_dirty_worktree_and_reassigns` |
| Tmux/observability tests | Implemented layout-level and CLI-level | `src/railwarden/tmux/session.py`, `src/railwarden/cli/main.py` | `test_tmux_launch_layout_has_factory_and_observability`, CLI smoke |
| MCP schema and stdio contract tests | Implemented | `src/railwarden/mcp/server.py` | `test_mcp_tool_schemas_cover_required_tools`; `test_mcp_stdio_server_lists_and_calls_railwarden_tools` |
| Checkpoint filtering tests | Implemented | `src/railwarden/runtime/checkpoints.py` | `test_checkpoint_filters_disallowed_files` |
| Secret-redaction tests | Implemented | `src/railwarden/runtime/secrets.py` | `test_secret_redaction_covers_keys_tokens_and_env` |
| Package entry point | Preserved | `pyproject.toml` | `uv run warden --help` |

## Optional Live Acceptance Runbook

Per the latest goal update, the `MusicArt` live acceptance flow is no longer part
of the active completion criteria. The runbook below remains for future
operator validation when live credentials and an intentionally disposable
project state are available.

Run this when live Hermes/tmux/provider credentials are available and a disposable `MusicArt` checkout is intentionally selected:

1. `cd /Users/advaith/CODE/MusicArt`
2. `git status --short --branch`
3. `warden init --yes` if the project is not already configured.
4. `warden doctor` and resolve any missing Hermes, tmux, provider CLI, credential, Ollama, or Azure checks.
5. `warden launch --profile acceptance`
6. In Hermes, submit a small disposable goal.
7. Watch `factory` worker panes execute and `observability` show DAG, workflow, git, quotas, events, and logs.
8. Force a low quota state with `warden quota set <agent_id> --remaining-percent 4`.
9. Confirm the controller pauses new work, creates handoff state, and surfaces swap need.
10. Run `warden agent swap <agent_id> --to <model-ref>`.
11. Run `warden checkpoint create <task_id>` from the task branch and verify the commit is absent from the integration branch until validation/integration passes.
12. Stop and relaunch with `warden launch --profile acceptance --no-attach`; verify reattach/resume state.
13. Let validation and serialized integration complete.
14. Run `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy`, and `uv run pytest`.
15. Confirm `git status --short` contains only intentional tracked changes and no `.railwarden-runtime/` files.

Optional live acceptance status: not required for the current goal. In
`/Users/advaith/CODE/MusicArt`, `uv run --project /Users/advaith/CODE/railwarden warden doctor`
passed local checks for Hermes, tmux, provider CLIs, LangGraph, PydanticAI,
ignored runtime state, and real MCP stdio (`tool_count: 13`). It reported
`OPENAI_API_KEY` missing for the configured OpenAI-backed `composer-1` agent,
and `MusicArt` was already dirty before acceptance probing with untracked
`.railwarden/plan.md`.
