# Worker result contract

**Purpose:** carries a worker’s claimed completion for mechanical verification. **Schema:** `schemas/worker_result.schema.json`. **Required:** `schema_version`, `task_id`, `worker`, `model`, `status`, `summary`; workspace, branch, commit, files, tests, blockers, and evidence are optional. **Valid/invalid examples:** `examples.json#/worker-result`.

A result is never self-certifying: a completed result must resolve to the expected branch/head, clean worktree, owned paths, and passing reported tests. Workers produce it; RailWarden validates/normalizes it; Hermes consumes verified outcomes. **Versioning/migration:** provisional, additive fields preferred. **Maturity:** provisional.
