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

The script first synchronizes the account-erasure ledger to its off-host remote, creates an encrypted backup, copies it to a dedicated off-host backup remote using `rclone`, then prunes expired local backup copies. The ledger is never pruned by the backup-retention command. Install and configure `rclone` for an S3-compatible or other supported object-storage destination. Set:

- `GATHERLY_DB_PATH`: live SQLite path.
- `GATHERLY_BACKUP_DIR`: local backup directory.
- `GATHERLY_BACKUP_REMOTE`: dedicated remote directory, e.g. `s3:gatherly-backups/production`.
- `GATHERLY_BACKUP_ENCRYPTION_KEY`: secret key described above.
- `GATHERLY_BACKUP_RETENTION_DAYS`: local retention in days (default 30).
- `GATHERLY_ERASURE_LEDGER_PATH`: persistent local directory for signed account-erasure tombstones; keep it outside the SQLite database and backup directory.
- `GATHERLY_ERASURE_LEDGER_KEY`: independent 32-byte base64 key used to pseudonymize account IDs and authenticate tombstones. Do not reuse the database backup encryption key.
- `GATHERLY_ERASURE_LEDGER_REMOTE`: dedicated off-host rclone directory for the immutable erasure ledger, separate from the backup destination.

Generate a separate ledger key (do not reuse the backup-encryption key) and store it in the secret manager:

```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

The local ledger directory must be on persistent storage and survive application restarts. The off-host remote must be separately permissioned from the backup remote. Enable provider-side object versioning and Object Lock/WORM where available; rclone's `--immutable` option is a client-side guard, not a substitute for provider-side protection.

Configure object-storage bucket versioning and a lifecycle/retention policy independently. Restrict remote access to the backup service identity. Keep at least one backup outside the application host and preferably outside the hosting account/region. Do not expose the remote bucket publicly.

## Restore and recovery drill

Stop every application process that can open the target database before restoring. Then restore an encrypted backup:

```bash
python -m app.infrastructure.database.backup restore \
  backups/gatherly-YYYYMMDDTHHMMSSZ.sqlite3.enc var/gatherly.sqlite3 \
  --confirm-application-stopped
```

The restore command requires the backup encryption key and the independent erasure-ledger key. It authenticates/decrypts to a private staging file, checks SQLite integrity and foreign keys, synchronizes and verifies the signed off-host erasure ledger, migrates the staged database, replays every matching erasure tombstone, verifies the result again, and only then atomically installs the restored database. A wrong key, modified backup, missing remote ledger, or invalid tombstone is rejected before the destination is replaced. The explicit flag is a safety guard, not a process supervisor; the operator remains responsible for stopping all writers.

At least monthly, copy a remote backup to a separate recovery environment, restore it, start the app against that restored copy, and verify `/health/ready` plus representative records. Record backup timestamp/age, integrity result, recovery time (RTO), and data-loss window (RPO). A backup is not operationally proven until this drill succeeds.

**Erasure safety gate:** database backups can predate account deletions. The current restore command requires the independent signed erasure ledger, synchronizes and verifies it, and replays matching tombstones against the staged database before publishing the restored file. Restore fails closed if the ledger is missing, invalid, or unavailable. See [Account-erasure ledger and restore safety](#account-erasure-ledger-and-restore-safety) below. Operators must still configure and protect the off-host remote, retain the ledger for as long as any older backup can be restored, and complete a recovery drill before production.

Local retention cleanup is available with:

```bash
python -m app.infrastructure.database.backup prune backups --retention-days 30
```

It deletes only regular, non-symlink files matching `gatherly-*.sqlite3.enc` in the selected directory. Remote retention should be enforced with the storage provider's lifecycle policy.

## Account-erasure ledger and restore safety

The application writes one signed tombstone per account deletion to `GATHERLY_ERASURE_LEDGER_PATH`, a persistent directory outside the SQLite database and its backup directory. A tombstone contains a random event ID, UTC deletion time, and a keyed HMAC of the internal user ID; it does not store the raw account ID, email, Telegram ID, or other login identity. Files are created with owner-only permissions and authenticated with `GATHERLY_ERASURE_LEDGER_KEY`, which must be a separate 32-byte secret from the backup-encryption key.

In production, `GATHERLY_ERASURE_LEDGER_REMOTE` is mandatory. It must be a dedicated off-host rclone remote with restricted write/read access, object versioning, and preferably provider-side Object Lock/WORM retention. The application uses `rclone copy --immutable`; it never uses remote sync/delete for this ledger. Account deletion commits only after the tombstone has been copied off-host successfully. If the ledger cannot be written or replicated, the API returns HTTP 503 and does not report deletion as successful. The tombstone remains locally so a retry or startup can replicate it.

On application startup, Gatherly synchronizes and verifies the ledger before serving requests, then replays tombstones against any matching accounts in the database. During restore, the same replay is performed against a private staging database before it can replace the destination. If the remote ledger is unavailable, corrupted, or has conflicting event copies, startup/restore fails closed. Newly created accounts are unaffected because their generated internal IDs do not match the pseudonymous tombstones.

Keep tombstones for at least as long as any backup, replica, snapshot, or archive that could predate the deletion remains recoverable. The ledger is intentionally not removed by `prune_backups`. Configure remote lifecycle/Object Lock retention to exceed the maximum backup retention, and do not retain older backups beyond the ledger's retention horizon. If backups are retained indefinitely, the corresponding minimal pseudonymous tombstones must also be retained indefinitely. Protect the ledger key separately; losing it makes tombstones unverifiable and recovery must stop rather than skip them.

The production app and backup/restore jobs require `rclone` to be installed and configured with the same restricted remote. Before enabling production account deletion, test a real remote write/read round trip and a restore drill in an isolated environment.

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


Migration 24 adds event sync revisions, trigger-based revision tracking for events/participants/notes/tasks/venues, and the per-user idempotency ledger (30-day retention policy for application cleanup).
