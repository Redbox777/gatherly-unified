# Database operations, backups, and migrations

## Encrypted SQLite backup

Do not copy the live SQLite database file with a plain filesystem copy. The application uses SQLite connections and may use a write-ahead log (WAL); a raw copy can omit committed WAL pages or capture an inconsistent state.

Generate a 32-byte random key once and store it in a secret manager, separate from the database host and backup destination:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Set `GATHERLY_BACKUP_ENCRYPTION_KEY` to that base64-encoded value in the backup job's secret environment. Do not put the key in source control, a backup directory, shell history, or the same object-storage bucket as the backups.

Create an authenticated, encrypted online backup:

```bash
python -m app.infrastructure.database.backup backup \
  var/gatherly.sqlite3 backups/gatherly-$(date -u +%Y%m%dT%H%M%SZ).sqlite3.enc
```

The backup command uses SQLite's online backup API, verifies the resulting database with `PRAGMA integrity_check` and `PRAGMA foreign_key_check`, then encrypts it with AES-256-GCM. The encrypted format authenticates its header and contents, uses a fresh random nonce, and is written atomically with owner-only permissions. Plaintext staging files are removed after use. The implementation streams data in bounded chunks rather than loading the whole database into memory.

The encryption key must be backed up separately using the operator's secret-management/recovery process. If the key is lost, encrypted backups cannot be restored.

## Schedule and off-host copy

Run the backup job at least daily, preferably during a low-traffic period. Example crontab entry (adjust absolute paths and service account):

```cron
17 2 * * * cd /srv/gatherly && /usr/bin/env bash scripts/backup-gatherly.sh >> /var/log/gatherly-backup.log 2>&1
```

The script creates an encrypted backup, copies it to a dedicated off-host remote using `rclone`, then prunes expired local copies. Install and configure `rclone` for an S3-compatible or other supported object-storage destination. Set:

- `GATHERLY_DB_PATH`: live SQLite path.
- `GATHERLY_BACKUP_DIR`: local backup directory.
- `GATHERLY_BACKUP_REMOTE`: dedicated remote directory, e.g. `s3:gatherly-backups/production`.
- `GATHERLY_BACKUP_ENCRYPTION_KEY`: secret key described above.
- `GATHERLY_BACKUP_RETENTION_DAYS`: local retention in days (default 30).

Configure object-storage bucket versioning and a lifecycle/retention policy independently. Restrict remote access to the backup service identity. Keep at least one backup outside the application host and preferably outside the hosting account/region. Do not expose the remote bucket publicly.

## Restore and recovery drill

Stop every application process that can open the target database before restoring. Then restore an encrypted backup:

```bash
python -m app.infrastructure.database.backup restore \
  backups/gatherly-YYYYMMDDTHHMMSSZ.sqlite3.enc var/gatherly.sqlite3 \
  --confirm-application-stopped
```

The restore command requires the same encryption key environment variable, authenticates/decrypts to a private temporary file, checks SQLite integrity, and atomically installs the restored database. A wrong key or modified backup is rejected before the destination is replaced. The explicit flag is a safety guard, not a process supervisor; the operator remains responsible for stopping all writers.

At least monthly, copy a remote backup to a separate recovery environment, restore it, start the app against that restored copy, and verify `/health/ready` plus representative records. Record backup timestamp/age, integrity result, recovery time (RTO), and data-loss window (RPO). A backup is not operationally proven until this drill succeeds.

**Erasure safety gate:** a database backup is a point-in-time snapshot and can contain accounts that were deleted after the snapshot was created. The current application does not maintain an independent, durable account-erasure ledger outside the backed-up database, and it does not automatically replay later erasure requests during restore. Therefore, do not restore an old production backup directly into a publicly accessible service. Keep the restored instance isolated (no public traffic and no outbound email, Telegram, or push delivery) until all erasure requests received after the backup timestamp have been reconciled and replayed using a verified procedure. If there is no authoritative record of those requests, stop and escalate; do not assume the restored database is GDPR-safe. Implementing and testing an off-database erasure ledger plus idempotent restore replay is a production-readiness requirement, not a feature delivered by the current backup commands.

Local retention cleanup is available with:

```bash
python -m app.infrastructure.database.backup prune backups --retention-days 30
```

It deletes only regular, non-symlink files matching `gatherly-*.sqlite3.enc` in the selected directory. Remote retention should be enforced with the storage provider's lifecycle policy.

## Health checks

- `GET /health/live` (and legacy `GET /health`) reports only that the HTTP process responds. It does not query SQLite.
- `GET /health/ready` checks a database query and verifies that the latest schema migration is applied. It returns HTTP 503 when the database is unavailable or the schema is behind.

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
| 19 | Opt-in Telegram notification delivery state |
| 20 | Telegram consent timestamp and text version |
| 21 | Encrypted Web Push subscriptions, deliveries, and consent |

There is no automatic destructive rollback path. For a faulty migration, stop writes, restore a verified backup when data recovery is required, or add a forward-only corrective migration after assessing data impact.

## Main-branch protection

Before production, protect `main`: require pull requests, require the `quality` workflow on the latest commit, block force-push and branch deletion, and restrict bypass permissions. This repository change documents the desired policy; the actual GitHub branch-protection/ruleset settings must be enabled and verified by a repository administrator.
