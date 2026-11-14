# Database operations, backups, and migrations

## SQLite backup

Do not copy the live SQLite database file with a plain filesystem copy. The application uses SQLite connections and may use a write-ahead log (WAL); a raw copy can omit committed WAL pages or capture an inconsistent state.

Create an online backup using SQLite's backup API:

\`\`\`bash
python -m app.infrastructure.database.backup backup \\
  var/gatherly.sqlite3 backups/gatherly-$(date -u +%F).sqlite3
\`\`\`

The command writes to a temporary file, uses SQLite's online backup API, runs \`PRAGMA integrity_check\`, applies owner-only file permissions where supported, and atomically replaces the destination. Store backups outside the application host as well, encrypt them at rest, restrict access, and define a retention schedule appropriate to the data held in Gatherly.

## Restore

Stop every application process that can open the target database before restoring. Then restore to the configured database path:

\`\`\`bash
python -m app.infrastructure.database.backup restore \\
  backups/gatherly-YYYY-MM-DD.sqlite3 var/gatherly.sqlite3 \\
  --confirm-application-stopped
\`\`\`

Restore uses the same online backup API and integrity check. The explicit flag is a safety guard, not a process supervisor; the operator remains responsible for stopping all writers. After restore, start the application and verify \`/health/ready\`.

A backup is not considered operationally proven until a restore has been exercised in a separate path/environment and the restored data has been checked. Repeat restore drills periodically and record the date, backup age, integrity result, and recovery time.

## Health checks

- \`GET /health/live\` (and legacy \`GET /health\`) reports only that the HTTP process responds. It does not query SQLite.
- \`GET /health/ready\` checks a database query and verifies that the latest schema migration is applied. It returns HTTP 503 when the database is unavailable or the schema is behind.

Use liveness for process supervision and readiness for traffic routing. Do not use a database-dependent readiness check as a liveness probe.

## Migration policy

Schema migrations are immutable and append-only. Never edit a migration already applied to an environment; add a new numbered migration and test upgrade from the immediately previous schema. Migration failures must roll back both partial DDL and the migration-version record.

Current migration history:

| Version | Purpose |
|---:|---|
| 1 | Events |
| 2 | Event participants |
| 3 | User identities, sessions, and email tokens |
| 4 | Telegram login replay-protection payloads |
| 5 | Link event participants to internal users |
| 6 | Participant invitations |
| 7 | SMTP rate-limit events |
| 8 | Email-token active-state index |
| 9 | Move email tokens to identity-scoped ownership |
| 10 | HTTP rate-limit events |
| 11 | Notifications and reminder preferences |
| 12 | Event notes |
| 13 | Event tasks |
| 14 | Event venues and coordinates |
| 15 | Event expenses |
| 16 | Expense splits and allocations |
| 17 | Event settlements and payer-name snapshots |
| 18 | Voluntary bug reports |

There is no automatic destructive rollback path. For a faulty migration, stop writes, restore a verified backup when data recovery is required, or add a forward-only corrective migration after assessing data impact.