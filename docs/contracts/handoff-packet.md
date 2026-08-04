# Handoff packet contract

**Purpose:** passes recoverable task context between providers or humans. **Schema:** `schemas/contracts/handoff-packet.schema.json`. **Required:** `schema_version`, `task_id`, `reason`, `from_provider`; destination provider, checkpoint, and evidence are optional. **Valid/invalid examples:** `examples.json#/handoff-packet`.

Created after quota, provider, process, or human-decision failure; it must not contain raw secrets. The controller/Hermes produces it; the next assignee consumes it. **Versioning/migration:** provisional, additive changes preferred. **Maturity:** provisional.
