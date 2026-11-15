from datetime import UTC, datetime
import os
import sqlite3

import pytest

from app.infrastructure.database.backup import (
    backup_database,
    backup_database_encrypted,
    prune_backups,
    restore_database,
)
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.erasure_ledger import (
    AccountErasureLedger,
    ErasureLedgerError,
)


def _create_database(path, title="Backed up event"):
    connection = connect(path)
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO events(id, owner_id, title, starts_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("event-1", "owner-1", title, None, "2026-11-14T00:00:00Z"),
        )
        connection.commit()
    finally:
        connection.close()


def _ledger(tmp_path):
    return AccountErasureLedger(tmp_path / "erasure-ledger", b"l" * 32)


def _event_title(path):
    connection = connect(path)
    try:
        row = connection.execute(
            "SELECT title FROM events WHERE id = ?", ("event-1",)
        ).fetchone()
        return row["title"]
    finally:
        connection.close()


def test_online_backup_and_restore_preserve_database_contents(tmp_path) -> None:
    source = tmp_path / "live.sqlite3"
    backup = tmp_path / "backups" / "gatherly.sqlite3"
    restored = tmp_path / "restore" / "gatherly.sqlite3"
    _create_database(source)
    backup_database(source, backup)

    restore_database(
        backup,
        restored,
        confirm_application_stopped=True,
        erasure_ledger=_ledger(tmp_path),
    )

    assert _event_title(restored) == "Backed up event"


def test_encrypted_backup_round_trip_and_permissions(tmp_path) -> None:
    source = tmp_path / "live.sqlite3"
    backup = tmp_path / "backups" / "gatherly-20261115.sqlite3.enc"
    restored = tmp_path / "restore" / "gatherly.sqlite3"
    key = os.urandom(32)
    _create_database(source)

    backup_database_encrypted(source, backup, key)
    assert backup.read_bytes().startswith(b"GATHERLY-ENC-BACKUP-V1\n")
    assert backup.stat().st_mode & 0o777 == 0o600

    restore_database(
        backup,
        restored,
        confirm_application_stopped=True,
        erasure_ledger=_ledger(tmp_path),
        encryption_key=key,
    )
    assert _event_title(restored) == "Backed up event"


def test_encrypted_backup_rejects_missing_or_wrong_key_without_overwriting_target(
    tmp_path,
) -> None:
    source = tmp_path / "live.sqlite3"
    backup = tmp_path / "backups" / "gatherly-20261115.sqlite3.enc"
    restored = tmp_path / "restore" / "gatherly.sqlite3"
    _create_database(source)
    backup_database_encrypted(source, backup, os.urandom(32))
    _create_database(restored, title="Keep this data")

    with pytest.raises(ValueError, match="requires the backup encryption key"):
        restore_database(
            backup,
            restored,
            confirm_application_stopped=True,
            erasure_ledger=_ledger(tmp_path),
        )

    with pytest.raises(ValueError, match="authentication failed"):
        restore_database(
            backup,
            restored,
            confirm_application_stopped=True,
            erasure_ledger=_ledger(tmp_path),
            encryption_key=os.urandom(32),
        )
    assert _event_title(restored) == "Keep this data"


def test_encrypted_backup_rejects_tampering(tmp_path) -> None:
    source = tmp_path / "live.sqlite3"
    backup = tmp_path / "backups" / "gatherly-20261115.sqlite3.enc"
    restored = tmp_path / "restore" / "gatherly.sqlite3"
    _create_database(source)
    key = os.urandom(32)
    backup_database_encrypted(source, backup, key)
    contents = bytearray(backup.read_bytes())
    contents[-20] ^= 0x01
    backup.write_bytes(contents)

    with pytest.raises(ValueError, match="authentication failed"):
        restore_database(
            backup,
            restored,
            confirm_application_stopped=True,
            erasure_ledger=_ledger(tmp_path),
            encryption_key=key,
        )


def test_restore_requires_explicit_application_stopped_confirmation(tmp_path) -> None:
    with pytest.raises(ValueError, match="Stop the application"):
        restore_database(
            tmp_path / "missing.sqlite3",
            tmp_path / "restored.sqlite3",
            confirm_application_stopped=False,
            erasure_ledger=_ledger(tmp_path),
        )


def test_backup_rejects_same_source_and_destination(tmp_path) -> None:
    source = tmp_path / "same.sqlite3"
    _create_database(source)
    with pytest.raises(ValueError, match="different files"):
        backup_database(source, source)


def test_prune_removes_only_expired_gatherly_encrypted_backups(tmp_path) -> None:
    now = datetime(2026, 11, 15, tzinfo=UTC)
    old_backup = tmp_path / "gatherly-old.sqlite3.enc"
    recent_backup = tmp_path / "gatherly-recent.sqlite3.enc"
    unrelated = tmp_path / "other.sqlite3.enc"
    old_backup.write_bytes(b"old")
    recent_backup.write_bytes(b"recent")
    unrelated.write_bytes(b"keep")
    os.utime(old_backup, (now.timestamp() - 40 * 86_400,) * 2)
    os.utime(recent_backup, (now.timestamp() - 2 * 86_400,) * 2)
    os.utime(unrelated, (now.timestamp() - 90 * 86_400,) * 2)

    removed = prune_backups(tmp_path, retention_days=30, now=now)

    assert removed == [old_backup]
    assert not old_backup.exists()
    assert recent_backup.exists()
    assert unrelated.exists()


def test_prune_rejects_zero_or_negative_retention(tmp_path) -> None:
    with pytest.raises(ValueError, match="at least one day"):
        prune_backups(tmp_path, retention_days=0)


def test_prune_requires_timezone_aware_clock(tmp_path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        prune_backups(
            tmp_path,
            retention_days=30,
            now=datetime(2026, 11, 15),
        )



def _create_database_with_foreign_key_violation(path):
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            CREATE TABLE parent (id TEXT PRIMARY KEY);
            CREATE TABLE child (
                id TEXT PRIMARY KEY,
                parent_id TEXT NOT NULL REFERENCES parent(id)
            );
            INSERT INTO child(id, parent_id) VALUES ('orphan', 'missing-parent');
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_backup_rejects_database_with_foreign_key_violations(tmp_path) -> None:
    source = tmp_path / "invalid.sqlite3"
    backup = tmp_path / "backups" / "gatherly.sqlite3"
    _create_database_with_foreign_key_violation(source)

    with pytest.raises(sqlite3.DatabaseError, match="foreign key check failed"):
        backup_database(source, backup)

    assert not backup.exists()


def test_restore_rejects_foreign_key_violations_without_overwriting_target(
    tmp_path,
) -> None:
    invalid_backup = tmp_path / "invalid.sqlite3"
    destination = tmp_path / "restore" / "gatherly.sqlite3"
    _create_database_with_foreign_key_violation(invalid_backup)
    _create_database(destination, title="Keep this data")

    with pytest.raises(sqlite3.DatabaseError, match="foreign key check failed"):
        restore_database(
            invalid_backup,
            destination,
            confirm_application_stopped=True,
            erasure_ledger=_ledger(tmp_path),
        )

    assert _event_title(destination) == "Keep this data"

def test_restore_replays_account_erasure_before_replacing_database(tmp_path) -> None:
    source = tmp_path / "live.sqlite3"
    backup = tmp_path / "backups" / "gatherly.sqlite3"
    restored = tmp_path / "restore" / "gatherly.sqlite3"
    _create_database(source)
    connection = connect(source)
    try:
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("deleted-user", "2026-11-14T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO auth_identities(id, user_id, provider, provider_subject, "
            "password_hash, verified_at, created_at) VALUES (?, ?, 'email', ?, ?, ?, ?)",
            (
                "deleted-identity",
                "deleted-user",
                "deleted@example.test",
                "password-hash",
                "2026-11-14T00:00:00Z",
                "2026-11-14T00:00:00Z",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    backup_database(source, backup)

    ledger = _ledger(tmp_path)
    ledger.record_deletion(
        user_id="deleted-user",
        deleted_at=datetime(2026, 11, 15, tzinfo=UTC),
    )
    restore_database(
        backup,
        restored,
        confirm_application_stopped=True,
        erasure_ledger=ledger,
    )

    connection = connect(restored)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM users WHERE id = ?", ("deleted-user",)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM auth_identities WHERE id = ?",
            ("deleted-identity",),
        ).fetchone()[0] == 0
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("new-user", "2026-11-16T00:00:00Z"),
        )
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) FROM users WHERE id = ?", ("new-user",)
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_restore_rejects_tampered_erasure_ledger_without_overwriting_target(
    tmp_path,
) -> None:
    source = tmp_path / "backup.sqlite3"
    destination = tmp_path / "restore.sqlite3"
    _create_database(source, title="Snapshot")
    _create_database(destination, title="Keep existing")
    ledger = _ledger(tmp_path)
    ledger.record_deletion(
        user_id="deleted-user", deleted_at=datetime.now(UTC)
    )
    event_file = next((tmp_path / "erasure-ledger").glob("*.json"))
    event_file.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(ErasureLedgerError, match="failed verification"):
        restore_database(
            source,
            destination,
            confirm_application_stopped=True,
            erasure_ledger=ledger,
        )
    assert _event_title(destination) == "Keep existing"

