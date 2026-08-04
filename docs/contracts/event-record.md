# Event record contract

**Purpose:** provides an append-only durable event ledger. **Schema:** `schemas/contracts/event-record.schema.json`. **Required:** `schema_version`, `ts`, `type`, `payload`; `task_id` is optional. **Valid/invalid examples:** `examples.json#/event-record`.

Events are emitted for task, process, failure, checkpoint, and decision facts. Producers redact secrets; consumers treat rows as historical facts and may filter by cursor/type. **Versioning/migration:** provisional; do not mutate existing rows, write migration projections when necessary. **Maturity:** provisional.
