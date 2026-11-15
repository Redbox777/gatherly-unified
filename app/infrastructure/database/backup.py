"""Safe SQLite online backups, authenticated encryption, and retention."""

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.erasure_ledger import AccountErasureLedger
from app.infrastructure.database.privacy_repository import PrivacyRepository


_ENCRYPTED_BACKUP_MAGIC = b"GATHERLY-ENC-BACKUP-V1\n"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_CHUNK_BYTES = 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_BACKUP_PATTERN_PREFIX = "gatherly-"
_BACKUP_PATTERN_SUFFIX = ".sqlite3.enc"


def _temporary_path(directory: Path, prefix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
    os.close(descriptor)
    return Path(name)


def _verify_database(connection: sqlite3.Connection) -> None:
    """Reject structurally corrupt databases and broken foreign-key references."""
    integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
    if not integrity_rows or any(row[0] != "ok" for row in integrity_rows):
        raise sqlite3.DatabaseError("SQLite integrity check failed")
    foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_violations:
        raise sqlite3.DatabaseError("SQLite foreign key check failed")


def _copy_database(
    source: Path, destination: Path, *, clear_destination_sidecars: bool = False
) -> None:
    source_path = source.expanduser().resolve()
    destination_path = destination.expanduser().resolve()
    if source_path == destination_path:
        raise ValueError("Source and destination must be different files")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _temporary_path(
        destination_path.parent, "." + destination_path.name + "."
    )
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(source_path)
        destination_connection = sqlite3.connect(temporary_path)
        source_connection.backup(destination_connection)
        _verify_database(destination_connection)
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None
        os.chmod(temporary_path, 0o600)
        with temporary_path.open("rb") as stream:
            os.fsync(stream.fileno())
        if clear_destination_sidecars:
            Path(str(destination_path) + "-wal").unlink(missing_ok=True)
            Path(str(destination_path) + "-shm").unlink(missing_ok=True)
        os.replace(temporary_path, destination_path)
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        temporary_path.unlink(missing_ok=True)


def _encrypt_file(source: Path, destination: Path, key: bytes) -> None:
    if len(key) != 32:
        raise ValueError("Backup encryption key must be exactly 32 bytes")
    destination_path = destination.expanduser().resolve()
    temporary_path = _temporary_path(
        destination_path.parent, "." + destination_path.name + "."
    )
    nonce = secrets.token_bytes(_NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(_ENCRYPTED_BACKUP_MAGIC)
    try:
        with source.open("rb") as input_stream, temporary_path.open("wb") as output_stream:
            output_stream.write(_ENCRYPTED_BACKUP_MAGIC)
            output_stream.write(nonce)
            while chunk := input_stream.read(_CHUNK_BYTES):
                output_stream.write(encryptor.update(chunk))
            output_stream.write(encryptor.finalize())
            output_stream.write(encryptor.tag)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _decrypt_file(source: Path, destination: Path, key: bytes) -> None:
    if len(key) != 32:
        raise ValueError("Backup encryption key must be exactly 32 bytes")
    source_path = source.expanduser().resolve()
    destination_path = destination.expanduser().resolve()
    minimum_size = len(_ENCRYPTED_BACKUP_MAGIC) + _NONCE_BYTES + _TAG_BYTES
    if source_path.stat().st_size < minimum_size:
        raise ValueError("Encrypted backup is truncated")
    temporary_path = _temporary_path(
        destination_path.parent, "." + destination_path.name + "."
    )
    try:
        with source_path.open("rb") as input_stream:
            if input_stream.read(len(_ENCRYPTED_BACKUP_MAGIC)) != _ENCRYPTED_BACKUP_MAGIC:
                raise ValueError("Backup encryption format is not recognized")
            nonce = input_stream.read(_NONCE_BYTES)
            input_stream.seek(-_TAG_BYTES, os.SEEK_END)
            tag = input_stream.read(_TAG_BYTES)
            ciphertext_end = source_path.stat().st_size - _TAG_BYTES
            input_stream.seek(len(_ENCRYPTED_BACKUP_MAGIC) + _NONCE_BYTES)
            ciphertext_remaining = (
                ciphertext_end - len(_ENCRYPTED_BACKUP_MAGIC) - _NONCE_BYTES
            )
            decryptor = Cipher(
                algorithms.AES(key), modes.GCM(nonce, tag)
            ).decryptor()
            decryptor.authenticate_additional_data(_ENCRYPTED_BACKUP_MAGIC)
            with temporary_path.open("wb") as output_stream:
                while ciphertext_remaining:
                    chunk = input_stream.read(min(_CHUNK_BYTES, ciphertext_remaining))
                    if not chunk:
                        raise ValueError("Encrypted backup is truncated")
                    ciphertext_remaining -= len(chunk)
                    output_stream.write(decryptor.update(chunk))
                output_stream.write(decryptor.finalize())
                output_stream.flush()
                os.fsync(output_stream.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination_path)
    except InvalidTag as error:
        raise ValueError("Backup authentication failed; key or file is invalid") from error
    finally:
        temporary_path.unlink(missing_ok=True)


def backup_database(source: Path, destination: Path) -> None:
    """Create and integrity-check an atomic, unencrypted SQLite backup."""
    _copy_database(source, destination)


def backup_database_encrypted(source: Path, destination: Path, key: bytes) -> None:
    """Create an online SQLite backup and encrypt it before publishing the file."""
    source_path = source.expanduser().resolve()
    destination_path = destination.expanduser().resolve()
    if source_path == destination_path:
        raise ValueError("Source and destination must be different files")
    plaintext_path = _temporary_path(destination_path.parent, ".gatherly-plain-")
    try:
        _copy_database(source, plaintext_path)
        _encrypt_file(plaintext_path, destination_path, key)
    finally:
        plaintext_path.unlink(missing_ok=True)


def restore_database(
    backup: Path,
    destination: Path,
    *,
    confirm_application_stopped: bool,
    erasure_ledger: AccountErasureLedger,
    encryption_key: bytes | None = None,
) -> None:
    """Restore a backup, replay all durable erasures, then atomically publish it."""
    if not confirm_application_stopped:
        raise ValueError("Stop the application and pass explicit restore confirmation")
    if erasure_ledger is None:
        raise ValueError("A verified account-erasure ledger is required for restore")
    backup_path = backup.expanduser().resolve()
    destination_path = destination.expanduser().resolve()
    if backup_path == destination_path:
        raise ValueError("Backup and destination must be different files")
    with backup_path.open("rb") as stream:
        is_encrypted = stream.read(len(_ENCRYPTED_BACKUP_MAGIC)) == _ENCRYPTED_BACKUP_MAGIC
    if is_encrypted and encryption_key is None:
        raise ValueError("Encrypted backup requires the backup encryption key")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path = _temporary_path(
        destination_path.parent, ".gatherly-restore-staged-"
    )
    plaintext_path: Path | None = None
    try:
        source_path = backup_path
        if is_encrypted:
            plaintext_path = _temporary_path(
                destination_path.parent, ".gatherly-restore-plain-"
            )
            _decrypt_file(backup_path, plaintext_path, encryption_key or b"")
            source_path = plaintext_path

        # Never replace the live target until the snapshot, ledger, and replayed
        # database have all passed validation.
        _copy_database(source_path, staged_path)
        events = erasure_ledger.load_events()
        connection = connect(staged_path)
        try:
            migrate(connection)
            user_ids = [
                str(row["id"])
                for row in connection.execute("SELECT id FROM users").fetchall()
            ]
            repository = PrivacyRepository(connection)
            for user_id in erasure_ledger.matching_user_ids(user_ids, events):
                repository.apply_recorded_erasure(user_id=user_id)
            _verify_database(connection)
        finally:
            connection.close()

        os.chmod(staged_path, 0o600)
        with staged_path.open("rb") as stream:
            os.fsync(stream.fileno())
        Path(str(destination_path) + "-wal").unlink(missing_ok=True)
        Path(str(destination_path) + "-shm").unlink(missing_ok=True)
        os.replace(staged_path, destination_path)
        AccountErasureLedger._fsync_directory(destination_path.parent)
    finally:
        staged_path.unlink(missing_ok=True)
        if plaintext_path is not None:
            plaintext_path.unlink(missing_ok=True)


def prune_backups(
    directory: Path, *, retention_days: int, now: datetime | None = None
) -> list[Path]:
    """Delete only expired Gatherly encrypted backup files in one directory."""
    if retention_days < 1:
        raise ValueError("Backup retention must be at least one day")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("Current time must include a timezone")
    cutoff = current_time.timestamp() - retention_days * 86_400
    removed: list[Path] = []
    root = directory.expanduser().resolve()
    if not root.is_dir():
        return removed
    for candidate in root.iterdir():
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or not candidate.name.startswith(_BACKUP_PATTERN_PREFIX)
            or not candidate.name.endswith(_BACKUP_PATTERN_SUFFIX)
        ):
            continue
        if candidate.stat().st_mtime < cutoff:
            candidate.unlink()
            removed.append(candidate)
    return removed


def _key_from_environment(variable_name: str) -> bytes:
    import base64

    encoded = os.environ.get(variable_name, "").strip()
    if not encoded:
        raise ValueError(f"Environment variable {variable_name} is required")
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError(f"{variable_name} must contain a base64-encoded key") from error
    if len(key) != 32:
        raise ValueError(f"{variable_name} must decode to exactly 32 bytes")
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description="Gatherly SQLite backup and restore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Create an encrypted online backup")
    backup_parser.add_argument("source", type=Path)
    backup_parser.add_argument("destination", type=Path)
    backup_parser.add_argument(
        "--key-env", default="GATHERLY_BACKUP_ENCRYPTION_KEY"
    )

    restore_parser = subparsers.add_parser("restore", help="Restore a verified backup")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("destination", type=Path)
    restore_parser.add_argument("--confirm-application-stopped", action="store_true")
    restore_parser.add_argument(
        "--key-env", default="GATHERLY_BACKUP_ENCRYPTION_KEY"
    )
    restore_parser.add_argument(
        "--erasure-ledger-key-env", default="GATHERLY_ERASURE_LEDGER_KEY"
    )
    restore_parser.add_argument(
        "--erasure-ledger-dir",
        type=Path,
        default=Path(os.environ.get("GATHERLY_ERASURE_LEDGER_PATH", "var/account-erasure-ledger")),
    )

    ledger_parser = subparsers.add_parser(
        "ledger-sync", help="Replicate the account-erasure ledger to off-host storage"
    )
    ledger_parser.add_argument(
        "--erasure-ledger-key-env", default="GATHERLY_ERASURE_LEDGER_KEY"
    )
    ledger_parser.add_argument(
        "--erasure-ledger-dir",
        type=Path,
        default=Path(os.environ.get("GATHERLY_ERASURE_LEDGER_PATH", "var/account-erasure-ledger")),
    )

    prune_parser = subparsers.add_parser("prune", help="Remove expired local encrypted backups")
    prune_parser.add_argument("directory", type=Path)
    prune_parser.add_argument("--retention-days", type=int, default=30)

    arguments = parser.parse_args()
    if arguments.command == "backup":
        ledger = AccountErasureLedger.from_environment(os.environ)
        if os.environ.get("APP_ENV", "development").strip().casefold() == "production":
            if ledger is None or ledger.remote is None:
                raise ValueError("Production backups require the off-host account-erasure ledger")
        if ledger is not None:
            ledger.sync_to_remote()
        backup_database_encrypted(
            arguments.source,
            arguments.destination,
            _key_from_environment(arguments.key_env),
        )
    elif arguments.command == "restore":
        ledger_key = _key_from_environment(arguments.erasure_ledger_key_env)
        ledger = AccountErasureLedger(
            arguments.erasure_ledger_dir,
            ledger_key,
            remote=os.environ.get("GATHERLY_ERASURE_LEDGER_REMOTE"),
        )
        if os.environ.get("APP_ENV", "development").strip().casefold() == "production":
            if ledger.remote is None:
                raise ValueError("Production restore requires the off-host account-erasure ledger")
        restore_database(
            arguments.backup,
            arguments.destination,
            confirm_application_stopped=arguments.confirm_application_stopped,
            encryption_key=_key_from_environment(arguments.key_env),
            erasure_ledger=ledger,
        )
    elif arguments.command == "ledger-sync":
        ledger_key = _key_from_environment(arguments.erasure_ledger_key_env)
        ledger = AccountErasureLedger(
            arguments.erasure_ledger_dir,
            ledger_key,
            remote=os.environ.get("GATHERLY_ERASURE_LEDGER_REMOTE"),
        )
        if ledger.remote is None:
            raise ValueError("GATHERLY_ERASURE_LEDGER_REMOTE is required")
        ledger.sync_to_remote()
    else:
        prune_backups(arguments.directory, retention_days=arguments.retention_days)


if __name__ == "__main__":
    main()
