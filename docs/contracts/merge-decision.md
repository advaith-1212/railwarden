# Merge decision contract

**Purpose:** records the mechanical merge-gate outcome. **Schema:** `schemas/contracts/merge-decision.schema.json`. **Required:** `schema_version`, `package_id`, `decision`, `evidence`; reviewer and rationale are optional. **Valid/invalid examples:** `examples.json#/merge-decision`.

Only approved packages with evidence may integrate. Integration produces the decision/evidence; Hermes and dashboards consume it. **Versioning/migration:** provisional and backwards-compatible additions only. **Maturity:** provisional.
