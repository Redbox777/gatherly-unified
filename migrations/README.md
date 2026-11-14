# Database migrations

The application migration registry is maintained in
`app/infrastructure/database/connection.py`. Migration versions are immutable and
append-only: never edit a migration that may already have been applied to an
environment. Add a new numbered migration and test the upgrade from the immediately
preceding schema, including rollback behavior if the migration fails.

The current schema history (versions 1–18), migration policy, backup procedure,
restore procedure, and health-check semantics are documented in
[Database operations](../docs/database-operations.md).

Migration tests live under `tests/integration/test_auth_schema.py` and
`tests/integration/test_database_safety.py`. Add coverage for both successful
upgrade and failure atomicity whenever a migration changes the schema or existing
data.
