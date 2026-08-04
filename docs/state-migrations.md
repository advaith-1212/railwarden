# State migrations

Committed config and runtime state carry schema versions. Migrations must be idempotent, write a backup or checkpoint before mutation where practical, validate the result, and fail closed with an actionable error. Do not delete legacy `.lfg` paths during ordinary startup: RailWarden reads compatible legacy paths only when preferred paths are absent. Rollback should restore the pre-migration data or require an explicit human decision.

Every migration needs fixtures for old/new state, repeated execution, malformed input, rollback/failure behavior, and compatibility notes in the changelog.
