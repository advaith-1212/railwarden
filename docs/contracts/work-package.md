# Work package contract

**Purpose:** freezes a unit of worker scope. **Schema:** `schemas/contracts/work-package.schema.json`. **Required:** `schema_version`, `id`, `name`, `objective`, `dependencies`, `owned_paths`; optional fields include forbidden paths, validation, provider preferences, and review policy. **Valid/invalid examples:** `examples.json#/work-package`.

Lifecycle: approval freezes the package before execution; dependencies must reference existing packages and owned paths must not overlap. The planner produces it; workers consume its scope; the scheduler enforces dependency readiness. **Versioning/migration:** provisional 1.x schema, additive optional fields only; incompatible change needs migration. **Maturity:** provisional.
