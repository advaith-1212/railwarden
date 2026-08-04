# Contract index

Contracts describe exchanged durable facts. All current contracts are **provisional** unless an individual page says otherwise: additive optional fields are expected to be compatible; removed/changed required fields require a versioned migration. Producers must emit schema-valid, redacted data; consumers must validate required fields and tolerate unknown optional fields. Invalid examples and valid examples are machine-readable in [`schemas/contracts/examples.json`](../../schemas/contracts/examples.json) and are checked in tests.

| Contract | Schema | Purpose |
| --- | --- | --- |
| [Work package](work-package.md) | [JSON Schema](../../schemas/contracts/work-package.schema.json) | Frozen scope and dependency contract |
| [Task state](task-state.md) | [JSON Schema](../../schemas/contracts/task-state.schema.json) | Durable package execution state |
| [Worker result](worker-result.md) | [JSON Schema](../../schemas/worker_result.schema.json) | Worker completion declaration |
| [Validation result](validation-result.md) | [JSON Schema](../../schemas/contracts/validation-result.schema.json) | Mechanical check evidence |
| [Event record](event-record.md) | [JSON Schema](../../schemas/contracts/event-record.schema.json) | Append-only event ledger row |
| [Handoff packet](handoff-packet.md) | [JSON Schema](../../schemas/contracts/handoff-packet.schema.json) | Recovery/reassignment context |
| [Merge decision](merge-decision.md) | [JSON Schema](../../schemas/contracts/merge-decision.schema.json) | Gate outcome with evidence |
| [Supervisor action](supervisor-action.md) | [JSON Schema](../../schemas/contracts/supervisor-action.schema.json) | Recorded Hermes decision |

See [compatibility policy](../compatibility-policy.md) and [state migrations](../state-migrations.md).
