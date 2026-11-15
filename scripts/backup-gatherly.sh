#!/usr/bin/env bash
set -euo pipefail

: "${GATHERLY_DB_PATH:?Set GATHERLY_DB_PATH}"
: "${GATHERLY_BACKUP_REMOTE:?Set GATHERLY_BACKUP_REMOTE}"
: "${GATHERLY_BACKUP_ENCRYPTION_KEY:?Set GATHERLY_BACKUP_ENCRYPTION_KEY}"
: "${GATHERLY_ERASURE_LEDGER_KEY:?Set GATHERLY_ERASURE_LEDGER_KEY}"
: "${GATHERLY_ERASURE_LEDGER_PATH:?Set GATHERLY_ERASURE_LEDGER_PATH}"
: "${GATHERLY_ERASURE_LEDGER_REMOTE:?Set GATHERLY_ERASURE_LEDGER_REMOTE}"

BACKUP_DIR="${GATHERLY_BACKUP_DIR:-/var/backups/gatherly}"
RETENTION_DAYS="${GATHERLY_BACKUP_RETENTION_DAYS:-30}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="$BACKUP_DIR/gatherly-$TIMESTAMP.sqlite3.enc"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Keep the independent account-erasure ledger replicated before taking a snapshot.
python -m app.infrastructure.database.backup ledger-sync
python -m app.infrastructure.database.backup backup "$GATHERLY_DB_PATH" "$BACKUP_FILE"
rclone copyto "$BACKUP_FILE" "$GATHERLY_BACKUP_REMOTE/$(basename "$BACKUP_FILE")" --immutable
python -m app.infrastructure.database.backup prune "$BACKUP_DIR" --retention-days "$RETENTION_DAYS"
