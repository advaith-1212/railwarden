# Supervisor action contract

**Purpose:** records an interpretation/decision while keeping facts in RailWarden. **Schema:** `schemas/contracts/supervisor-action.schema.json`. **Required:** `schema_version`, `task_id`, `action`, `rationale`; tool call and result are optional. **Valid/invalid examples:** `examples.json#/supervisor-action`.

Hermes produces actions such as retry, handoff, model swap, or ask-user; RailWarden executes only allowed mechanics and records the result. **Versioning/migration:** provisional. A generic supervisor contract is not yet promised. **Maturity:** experimental.
