"""Durable, independently stored tombstones for account erasure."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import tempfile
from collections.abc import Mapping
from uuid import UUID


class ErasureLedgerError(RuntimeError):
    """The erasure ledger could not be safely written, synchronized, or verified."""


@dataclass(frozen=True)
class ErasureEvent:
    """A signed tombstone containing no raw account identifier."""

    event_id: str
    user_id_hash: str
    deleted_at: str


class AccountErasureLedger:
    """Store signed per-account tombstones outside SQLite backups.

    Production deployments must configure an immutable off-host rclone remote.
    The local directory is a durable write-ahead copy; remote synchronization is
    completed before the account-deletion transaction is allowed to commit.
    """

    def __init__(
        self,
        directory: Path,
        key: bytes,
        *,
        remote: str | None = None,
        command_timeout_seconds: int = 120,
    ) -> None:
        if len(key) != 32:
            raise ValueError("Erasure ledger key must be exactly 32 bytes")
        if command_timeout_seconds < 1:
            raise ValueError("Command timeout must be positive")
        self.directory = directory.expanduser().resolve()
        self.key = key
        self.remote = remote.strip() if remote and remote.strip() else None
        self.command_timeout_seconds = command_timeout_seconds

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str]
    ) -> AccountErasureLedger | None:
        """Build the ledger from environment settings; return None if unconfigured."""
        encoded = environ.get("GATHERLY_ERASURE_LEDGER_KEY", "").strip()
        if not encoded:
            return None
        try:
            key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        except ValueError as error:
            raise ValueError(
                "GATHERLY_ERASURE_LEDGER_KEY must be a base64-encoded key"
            ) from error
        if len(key) != 32:
            raise ValueError(
                "GATHERLY_ERASURE_LEDGER_KEY must decode to exactly 32 bytes"
            )
        directory_value = environ.get(
            "GATHERLY_ERASURE_LEDGER_PATH",
            "var/account-erasure-ledger",
        ).strip()
        if not directory_value:
            raise ValueError("GATHERLY_ERASURE_LEDGER_PATH must not be blank")
        directory = Path(directory_value)
        return cls(
            directory,
            key,
            remote=environ.get("GATHERLY_ERASURE_LEDGER_REMOTE"),
        )

    def _ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    def _user_id_hash(self, user_id: str) -> str:
        return hmac.new(
            self.key,
            b"gatherly-erasure-subject-v1:" + user_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _signature(self, payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hmac.new(
            self.key, b"gatherly-erasure-event-v1:" + canonical, hashlib.sha256
        ).hexdigest()

    def record_deletion(self, *, user_id: str, deleted_at: datetime) -> ErasureEvent:
        """Persist a tombstone and confirm off-host replication before returning."""
        if not user_id.strip():
            raise ValueError("User ID must not be blank")
        if deleted_at.tzinfo is None or deleted_at.utcoffset() is None:
            raise ValueError("Deletion timestamp must include a timezone")
        self._ensure_directory()
        event_id = UUID(bytes=os.urandom(16), version=4).hex
        timestamp = deleted_at.astimezone(UTC).isoformat()
        payload: dict[str, object] = {
            "schema_version": 1,
            "event_id": event_id,
            "user_id_hash": self._user_id_hash(user_id),
            "deleted_at": timestamp,
        }
        document = {**payload, "signature": self._signature(payload)}
        encoded = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        path = self.directory / f"{event_id}.json"
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._fsync_directory(self.directory)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self.sync_to_remote()
        return ErasureEvent(event_id, str(payload["user_id_hash"]), timestamp)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def sync_to_remote(self) -> None:
        """Copy local tombstones to immutable off-host storage; never prune remote."""
        if self.remote is None:
            return
        self._ensure_directory()
        try:
            subprocess.run(
                [
                    "rclone",
                    "copy",
                    str(self.directory),
                    self.remote,
                    "--include",
                    "*.json",
                    "--immutable",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ErasureLedgerError(
                "Could not confirm off-host account-erasure ledger replication"
            ) from error

    def _read_event(self, path: Path) -> ErasureEvent:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
            raise ErasureLedgerError("Erasure ledger contains an unsafe event file")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(document, dict) or set(document) != {
                "schema_version",
                "event_id",
                "user_id_hash",
                "deleted_at",
                "signature",
            }:
                raise ValueError("Unexpected event fields")
            payload = {
                "schema_version": document["schema_version"],
                "event_id": document["event_id"],
                "user_id_hash": document["user_id_hash"],
                "deleted_at": document["deleted_at"],
            }
            if payload["schema_version"] != 1:
                raise ValueError("Unsupported event version")
            event_id = str(payload["event_id"])
            if UUID(hex=event_id).hex != event_id or path.stem != event_id:
                raise ValueError("Invalid event identifier")
            user_id_hash = str(payload["user_id_hash"])
            if len(user_id_hash) != 64 or any(
                character not in "0123456789abcdef" for character in user_id_hash
            ):
                raise ValueError("Invalid subject digest")
            timestamp = str(payload["deleted_at"])
            parsed_timestamp = datetime.fromisoformat(timestamp)
            if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
                raise ValueError("Timestamp has no timezone")
            expected = self._signature(payload)
            supplied = str(document["signature"])
            if not hmac.compare_digest(expected, supplied):
                raise ValueError("Invalid event signature")
            return ErasureEvent(event_id, user_id_hash, timestamp)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ErasureLedgerError(
                f"Erasure ledger event {path.name} failed verification"
            ) from error

    def load_events(self) -> tuple[ErasureEvent, ...]:
        """Synchronize, verify, and return the union of local and remote tombstones."""
        self._ensure_directory()
        self.sync_to_remote()
        with tempfile.TemporaryDirectory(prefix="gatherly-erasure-remote-") as temporary:
            remote_directory = Path(temporary)
            if self.remote is not None:
                try:
                    subprocess.run(
                        [
                            "rclone",
                            "copy",
                            self.remote,
                            str(remote_directory),
                            "--include",
                            "*.json",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=self.command_timeout_seconds,
                    )
                except (OSError, subprocess.SubprocessError) as error:
                    raise ErasureLedgerError(
                        "Could not retrieve the off-host account-erasure ledger"
                    ) from error
            events: dict[str, ErasureEvent] = {}
            for directory in (self.directory, remote_directory):
                for path in sorted(directory.glob("*.json")):
                    event = self._read_event(path)
                    existing = events.get(event.event_id)
                    if existing is not None and existing != event:
                        raise ErasureLedgerError(
                            "Conflicting copies of an account-erasure event were found"
                        )
                    events[event.event_id] = event
            return tuple(
                sorted(events.values(), key=lambda item: (item.deleted_at, item.event_id))
            )

    def matching_user_ids(
        self, user_ids: list[str], events: tuple[ErasureEvent, ...]
    ) -> tuple[str, ...]:
        """Resolve pseudonymous tombstones against IDs in a restored database."""
        erased_hashes = {event.user_id_hash for event in events}
        return tuple(
            user_id
            for user_id in user_ids
            if self._user_id_hash(user_id) in erased_hashes
        )
