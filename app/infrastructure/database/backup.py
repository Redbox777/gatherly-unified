"""Safe SQLite online backup and controlled restore helpers.

Backups use SQLite's online backup API so committed WAL content is included.
Restore is intentionally gated because the application must be stopped first.
"""

import argparse
import os
from pathlib import Path
import sqlite3
import tempfile


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
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="." + destination_path.name + ".",
        suffix=".tmp",
        dir=destination_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(source_path)
        destination_connection = sqlite3.connect(temporary_path)
        source_connection.backup(destination_connection)
        integrity = destination_connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()
        if integrity is None or integrity[0] != "ok":
            raise sqlite3.DatabaseError("SQLite integrity check failed")
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None
        os.chmod(temporary_path, 0o600)
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


def backup_database(source: Path, destination: Path) -> None:
    """Create and integrity-check an atomic SQLite backup."""
    _copy_database(source, destination)


def restore_database(
    backup: Path,
    destination: Path,
    *,
    confirm_application_stopped: bool,
) -> None:
    """Restore atomically; caller must stop every app process first."""
    if not confirm_application_stopped:
        raise ValueError("Stop the application and pass explicit restore confirmation")
    _copy_database(backup, destination, clear_destination_sidecars=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gatherly SQLite backup and restore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Create an online backup")
    backup_parser.add_argument("source", type=Path)
    backup_parser.add_argument("destination", type=Path)

    restore_parser = subparsers.add_parser("restore", help="Restore a verified backup")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("destination", type=Path)
    restore_parser.add_argument("--confirm-application-stopped", action="store_true")

    arguments = parser.parse_args()
    if arguments.command == "backup":
        backup_database(arguments.source, arguments.destination)
    else:
        restore_database(
            arguments.backup,
            arguments.destination,
            confirm_application_stopped=arguments.confirm_application_stopped,
        )


if __name__ == "__main__":
    main()
