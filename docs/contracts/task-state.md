# Task state contract

**Purpose:** records a package’s durable execution lifecycle. **Schema:** `schemas/contracts/task-state.schema.json`. **Required:** `schema_version`, `id`, `package_id`, `status`, `dependencies`; attempts, provider, workspace, checkpoints, and evidence are optional. **Valid/invalid examples:** `examples.json#/task-state`.

States progress from planned/ready through assignment, validation, review, integration, and merged, with failure, handoff, cooldown, decision, blocked, and rejected paths. The controller produces transitions; views and Hermes consume them. **Versioning/migration:** provisional and versioned runtime-state migration required for changed required fields. **Maturity:** provisional.
