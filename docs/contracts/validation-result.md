# Validation result contract

**Purpose:** records deterministic command evidence. **Schema:** `schemas/contracts/validation-result.schema.json`. **Required:** `schema_version`, `name`, `status`, `command`; duration, stdout, stderr, cwd, and return code are optional runtime evidence. **Valid/invalid examples:** `examples.json#/validation-result`.

Required failures block merge; timeouts/process failures normalize to failure evidence. Validators produce it; review and integration gates consume it. **Versioning/migration:** provisional; consumers must ignore future optional fields. **Maturity:** provisional.
