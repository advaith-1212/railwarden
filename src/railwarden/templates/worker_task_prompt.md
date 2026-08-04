# RailWarden Worker Task

You own the complete lifecycle for this package: inspect, plan, implement, debug, test, commit, and return structured JSON evidence.

Do not merge, rebase, push, or modify another worktree.

Stay inside the owned paths unless the task explicitly instructs otherwise. Do not edit forbidden paths. Preserve provider defaults and temp-file behavior; RailWarden only controls cwd, prompt path, result path, logs, and process supervision.

The result JSON must match schema version `1.0.0` and include task id, worker, model, status, summary, workspace, branch, commit_hash, changed_files, tests, blockers, and evidence.
