# LFG v2 Implementation Status

Pivot update: LFG is moving back to an evented-runtime model with Hermes as a
supervisor. LFG runtime state, events, worktrees, results, validation evidence,
and merge gates are authoritative. Hermes Kanban remains useful as a
planning/coordination projection, but it should not own canonical task truth.
The matrix below records implementation status from the v2 runtime work and may
include transition-era labels.

Updated: 2026-06-28

Baseline before implementation:

- Branch: `main...origin/main`
- Pre-existing modified files: `src/lfg/engine/controller.py`, `src/lfg/provisioning/worktrees.py`, `src/lfg/scheduler/classifier.py`, `src/lfg/validation/runner.py`, `src/lfg/validation/worker_result.py`
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
  - `uv run lfg --help`: passed; includes `launch`, `model`, `agent`, `quota`, `checkpoint`, `observability`, `mcp`
  - `uv run lfg launch --help`: passed
  - `uv run lfg model list`: passed
  - `uv run lfg agent --help`: passed
  - `uv run lfg quota --help`: passed
  - `uv run lfg checkpoint --help`: passed
  - `uv run lfg mcp --help`: passed
  - Disposable initialized repo runtime smoke passed for `lfg model doctor`, `lfg model configure openai:gpt-5.2`, `lfg agent list`, `lfg quota status`, and `lfg observability`
- Runtime ignore check: `git check-ignore -q .lfg-runtime` passed

## Matrix

| Plan item | Status | Implementation files | Tests / evidence |
|---|---:|---|---|
| `lfg launch` production entry point | Implemented | `src/lfg/cli/main.py`, `src/lfg/tmux/session.py`, `src/lfg/hermes/profile.py` | CLI smoke `uv run lfg launch --help`; full pytest |
| `lfg start` legacy/simple alias | Preserved | `src/lfg/cli/main.py`, `src/lfg/tmux/session.py` | Existing tests plus CLI smoke |
| Interactive launch wizard every run, with non-interactive defaults; selects session profile, orchestrator, architect, workers, reviewer/validator when present, budget label, quota thresholds, manual token budget, and fallback/swap policy | Implemented | `src/lfg/cli/main.py`, `src/lfg/runtime/session.py` | `test_launch_wizard_persists_budget_quota_fallback_and_review_models`; `test_hermes_profile_generation_is_runtime_only`; CLI smoke |
| Tmux factory and observability windows | Implemented | `src/lfg/tmux/session.py` | `test_tmux_launch_layout_has_factory_and_observability` |
| Pane titles include role/executor/provider/model/health/budget signal | Implemented | `src/lfg/tmux/session.py` | `test_tmux_launch_layout_has_factory_and_observability` asserts role, executor, provider, model, and observability command |
| Hermes remains external; no fork/vendor | Implemented | `src/lfg/hermes/profile.py`, `pyproject.toml` | `command -v hermes` found `/Users/advaith/.local/bin/hermes`; generated profile test |
| Runtime-generated Hermes profile under ignored runtime state | Implemented | `src/lfg/hermes/profile.py`, `src/lfg/runtime/secrets.py` | `test_hermes_profile_generation_is_runtime_only`; `.lfg-runtime` ignored |
| Hermes configured with orchestrator provider/model, LFG MCP, skills, factory instructions, local terminal backend | Implemented | `src/lfg/hermes/profile.py` | `test_hermes_profile_generation_is_runtime_only` |
| LFG MCP server required tools | Implemented plus skill tools over real MCP stdio | `src/lfg/mcp/server.py` | `test_mcp_tool_schemas_cover_required_tools`; `test_mcp_stdio_server_lists_and_calls_lfg_tools` |
| Replace lightweight `lfg hermes` path with launch-generated Hermes profile | Implemented for production launch; legacy console remains compatibility path | `src/lfg/cli/main.py`, `src/lfg/hermes/profile.py` | CLI smoke; generated profile test |
| Model ref parser, normalized refs, and provider registry loading | Implemented | `src/lfg/runtime/model_refs.py`, `src/lfg/models/registry.py` | `test_model_ref_parsing_normalizes_supported_shapes`, `test_provider_registry_lists_valid_default_model_refs`, `test_model_profile_uses_pydanticai_compatible_ref` |
| ModelProfile, AgentRole, AgentInstance, SessionProfile, QuotaState | Implemented | `src/lfg/runtime/session.py` | `test_session_profile_serializes_without_raw_secret`, quota tests |
| Persist launch state in `.lfg-runtime/state/session-profile.json` | Implemented | `src/lfg/runtime/session.py`, `src/lfg/cli/main.py` | `test_session_profile_serializes_without_raw_secret` |
| Durable project defaults in `.lfg/project.yaml` or `.lfg/factory.yaml` | Implemented with compatibility | `src/lfg/config/init.py`, `src/lfg/config/loader.py` | Existing config tests; loader supports `factory.yaml` first |
| Never store raw secrets in tracked `.lfg` | Implemented | `src/lfg/runtime/secrets.py`, `src/lfg/runtime/session.py`, `src/lfg/hermes/profile.py` | `test_session_profile_serializes_without_raw_secret`, `test_secret_redaction_covers_keys_tokens_and_env` |
| Secrets from env/Hermes `.env`/runtime secrets with restrictive permissions | Implemented | `src/lfg/runtime/secrets.py`, `src/lfg/hermes/profile.py` | `test_hermes_profile_generation_is_runtime_only` |
| PydanticAI typed LFG-owned model-call boundary | Implemented with provider-specific model translation for OpenAI, Anthropic, Gemini, Azure Foundry/OpenAI-compatible, and Ollama | `src/lfg/runtime/typed_agents.py`, `pyproject.toml` | `test_pydanticai_agent_translates_lfg_model_refs`; strict mypy; dependency resolved to `pydantic-ai==2.0.0` |
| LangGraph workflow/checkpoint/observability state only | Implemented as runtime checkpoints with LangGraph availability check | `src/lfg/runtime/workflow.py`, `src/lfg/cli/main.py`, `pyproject.toml` | Strict mypy; dependency resolved to `langgraph==1.2.6`; observability CLI smoke |
| LFG task state remains source of truth | Preserved | `src/lfg/runtime/tasks.py`, `src/lfg/engine/controller.py` | Existing controller/disposable repo tests |
| Quota policies and confidence levels | Implemented | `src/lfg/runtime/session.py`, `src/lfg/runtime/quota.py` | `test_low_quota_prevents_new_task_launch` |
| Track usage from metadata/manual budget surfaces | Implemented manual/runtime API; provider metadata hooks prepared | `src/lfg/runtime/quota.py`, `src/lfg/cli/main.py` | `lfg quota status/set` CLI; quota unit test |
| Check quota before model call or worker task | Implemented for worker launch | `src/lfg/engine/controller.py` | `test_low_quota_prevents_new_task_launch` |
| Low quota pauses agent, writes handoff packet, preserves checkpoint when a worktree exists, and marks task `handoff_needed` | Implemented | `src/lfg/engine/controller.py`, `src/lfg/runtime/handoff.py`, `src/lfg/runtime/checkpoints.py`, `src/lfg/runtime/events.py` | `test_low_quota_prevents_new_task_launch` |
| `lfg agent swap <agent_id> --to <model-ref>` updates session, writes handoff packet for active work, sets provider override, and relaunches with the swapped provider/model | Implemented | `src/lfg/cli/main.py`, `src/lfg/engine/controller.py`, `src/lfg/mcp/server.py` | `test_agent_swap_creates_handoff_and_relaunches_with_override`; CLI smoke; strict mypy |
| `lfg agent pause/resume` | Implemented | `src/lfg/cli/main.py`, `src/lfg/mcp/server.py` | CLI smoke; strict mypy |
| Checkpoint commits only on task branches | Implemented | `src/lfg/runtime/checkpoints.py`, `src/lfg/mcp/server.py`, `src/lfg/cli/main.py` | `test_checkpoint_filters_disallowed_files` |
| Checkpoint metadata under `.lfg-runtime/checkpoints/` | Implemented | `src/lfg/runtime/checkpoints.py` | `test_checkpoint_filters_disallowed_files` |
| Resume reloads session profile/tasks/quotas/workflow and tmux metadata | Implemented via `launch` reattach and file-backed loaders | `src/lfg/cli/main.py`, `src/lfg/tmux/session.py`, `src/lfg/runtime/session.py`, `src/lfg/runtime/workflow.py` | CLI smoke; strict mypy |
| `.lfg/skills` and `.lfg-runtime/skills` | Implemented | `src/lfg/runtime/skills.py`, `src/lfg/hermes/profile.py` | `test_worker_prompt_includes_skills_and_mcp_routing` |
| Runtime skill generation and promotion path | Implemented through MCP | `src/lfg/runtime/skills.py`, `src/lfg/mcp/server.py` | `test_mcp_tool_schemas_cover_required_tools` |
| Worker prompts include required skills and MCP routing | Implemented | `src/lfg/engine/controller.py`, `src/lfg/runtime/skills.py` | `test_worker_prompt_includes_skills_and_mcp_routing` |
| Public CLI additions | Implemented | `src/lfg/cli/main.py` | CLI smoke output listed above |
| Extended `lfg doctor` checks | Implemented for Hermes, tmux, provider adapters, selected API credential refs, configured Ollama/Azure endpoints, PydanticAI, LangGraph, MCP stdio startup, and runtime ignore | `src/lfg/cli/main.py`, `src/lfg/runtime/doctor.py`, `src/lfg/providers/adapters.py` | `test_doctor_report_checks_credentials_endpoints_mcp_and_ignore`; `test_mcp_stdio_server_lists_and_calls_lfg_tools`; strict mypy |
| V1 config compatibility/migration/defaulting | Preserved and extended | `src/lfg/config/init.py`, `src/lfg/config/loader.py` | Existing config/planner/controller tests |
| No raw secrets in logs/fixtures/snapshots/errors | Implemented for LFG-owned runtime serialization/redaction | `src/lfg/runtime/secrets.py`, `src/lfg/runtime/checkpoints.py`, `src/lfg/runtime/session.py` | Secret redaction tests |
| Fake providers are test infrastructure only | Preserved | `tests/`, `src/lfg/providers/adapters.py` | Existing integration tests and no fake production adapter added |
| Disposable repo lifecycle | Preserved | `tests/integration/test_disposable_repo.py` | Full pytest pass |
| Failure/recovery, crash-resume, and quota swap | Implemented for provider failure, low-quota handoff, dirty-worktree crash resume with checkpoint, and explicit quota swap | `src/lfg/engine/controller.py`, `src/lfg/runtime/handoff.py`, `src/lfg/runtime/quota.py`, `src/lfg/cli/main.py` | Existing dead-quota handoff test; `test_low_quota_prevents_new_task_launch`; `test_agent_swap_creates_handoff_and_relaunches_with_override`; `test_crash_resume_checkpoints_dirty_worktree_and_reassigns` |
| Tmux/observability tests | Implemented layout-level and CLI-level | `src/lfg/tmux/session.py`, `src/lfg/cli/main.py` | `test_tmux_launch_layout_has_factory_and_observability`, CLI smoke |
| MCP schema and stdio contract tests | Implemented | `src/lfg/mcp/server.py` | `test_mcp_tool_schemas_cover_required_tools`; `test_mcp_stdio_server_lists_and_calls_lfg_tools` |
| Checkpoint filtering tests | Implemented | `src/lfg/runtime/checkpoints.py` | `test_checkpoint_filters_disallowed_files` |
| Secret-redaction tests | Implemented | `src/lfg/runtime/secrets.py` | `test_secret_redaction_covers_keys_tokens_and_env` |
| Package entry point | Preserved | `pyproject.toml` | `uv run lfg --help` |

## Optional Live Acceptance Runbook

Per the latest goal update, the `MusicArt` live acceptance flow is no longer part
of the active completion criteria. The runbook below remains for future
operator validation when live credentials and an intentionally disposable
project state are available.

Run this when live Hermes/tmux/provider credentials are available and a disposable `MusicArt` checkout is intentionally selected:

1. `cd /Users/advaith/CODE/MusicArt`
2. `git status --short --branch`
3. `lfg init --yes` if the project is not already configured.
4. `lfg doctor` and resolve any missing Hermes, tmux, provider CLI, credential, Ollama, or Azure checks.
5. `lfg launch --profile acceptance`
6. In Hermes, submit a small disposable goal.
7. Watch `factory` worker panes execute and `observability` show DAG, workflow, git, quotas, events, and logs.
8. Force a low quota state with `lfg quota set <agent_id> --remaining-percent 4`.
9. Confirm the controller pauses new work, creates handoff state, and surfaces swap need.
10. Run `lfg agent swap <agent_id> --to <model-ref>`.
11. Run `lfg checkpoint create <task_id>` from the task branch and verify the commit is absent from the integration branch until validation/integration passes.
12. Stop and relaunch with `lfg launch --profile acceptance --no-attach`; verify reattach/resume state.
13. Let validation and serialized integration complete.
14. Run `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy`, and `uv run pytest`.
15. Confirm `git status --short` contains only intentional tracked changes and no `.lfg-runtime/` files.

Optional live acceptance status: not required for the current goal. In
`/Users/advaith/CODE/MusicArt`, `uv run --project /Users/advaith/CODE/lfg lfg doctor`
passed local checks for Hermes, tmux, provider CLIs, LangGraph, PydanticAI,
ignored runtime state, and real MCP stdio (`tool_count: 13`). It reported
`OPENAI_API_KEY` missing for the configured OpenAI-backed `composer-1` agent,
and `MusicArt` was already dirty before acceptance probing with untracked
`.lfg/plan.md`.
