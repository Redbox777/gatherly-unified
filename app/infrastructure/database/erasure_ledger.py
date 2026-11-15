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
    event_type: str = "account_deletion"
    target_user_id_hash: str | None = None
    proof_identity_id_hash: str | None = None
    merge_intent_id: str | None = None


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
            key = base64.b64decode(
                encoded.encode("ascii"), altchars=b"-_", validate=True
            )
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

    def _identity_id_hash(self, identity_id: str) -> str:
        return hmac.new(
            self.key,
            b"gatherly-erasure-identity-v1:" + identity_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _signature(self, payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hmac.new(
            self.key, b"gatherly-erasure-event-v1:" + canonical, hashlib.sha256
        ).hexdigest()

    def _persist_payload(self, payload: dict[str, object], *, require_remote: bool = True) -> bool:
        self._ensure_directory()
        event_id = str(payload["event_id"])
        document = {**payload, "signature": self._signature(payload)}
        encoded = (
            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        path = self.directory / f"{event_id}.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._fsync_directory(self.directory)
        except OSError as error:
            path.unlink(missing_ok=True)
            raise ErasureLedgerError(
                "Could not persist the account-erasure ledger event"
            ) from error
        try:
            self.sync_to_remote()
        except ErasureLedgerError:
            if require_remote:
                raise
            return False
        return True

    def record_deletion(self, *, user_id: str, deleted_at: datetime) -> ErasureEvent:
        """Persist a tombstone and confirm off-host replication before returning."""
        if not user_id.strip():
            raise ValueError("User ID must not be blank")
        if deleted_at.tzinfo is None or deleted_at.utcoffset() is None:
            raise ValueError("Deletion timestamp must include a timezone")
        event_id = UUID(bytes=os.urandom(16), version=4).hex
        timestamp = deleted_at.astimezone(UTC).isoformat()
        payload: dict[str, object] = {
            "schema_version": 1,
            "event_id": event_id,
            "user_id_hash": self._user_id_hash(user_id),
            "deleted_at": timestamp,
        }
        self._persist_payload(payload)
        return ErasureEvent(event_id, str(payload["user_id_hash"]), timestamp)

    def record_merge_intent(
        self,
        *,
        source_user_id: str,
        target_user_id: str,
        proof_identity_id: str,
        created_at: datetime,
    ) -> ErasureEvent:
        """Replicate a merge intent before moving identities or deleting its source."""
        if not source_user_id.strip() or not target_user_id.strip():
            raise ValueError("Merge user IDs must not be blank")
        if source_user_id == target_user_id:
            raise ValueError("A merge intent must identify two different accounts")
        if not proof_identity_id.strip():
            raise ValueError("Proof identity ID must not be blank")
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("Merge timestamp must include a timezone")
        event_id = UUID(bytes=os.urandom(16), version=4).hex
        timestamp = created_at.astimezone(UTC).isoformat()
        payload: dict[str, object] = {
            "schema_version": 2,
            "event_type": "account_merge_intent",
            "event_id": event_id,
            "source_user_id_hash": self._user_id_hash(source_user_id),
            "target_user_id_hash": self._user_id_hash(target_user_id),
            "proof_identity_id_hash": self._identity_id_hash(proof_identity_id),
            "merge_intent_id": None,
            "created_at": timestamp,
        }
        self._persist_payload(payload)
        return ErasureEvent(
            event_id=event_id,
            user_id_hash=str(payload["source_user_id_hash"]),
            deleted_at=timestamp,
            event_type="account_merge_intent",
            target_user_id_hash=str(payload["target_user_id_hash"]),
            proof_identity_id_hash=str(payload["proof_identity_id_hash"]),
        )

    def record_merge_resolution(
        self, *, intent: ErasureEvent, committed: bool
    ) -> bool:
        """Append a commit/abort marker; local durability survives remote outages."""
        if (
            intent.event_type != "account_merge_intent"
            or intent.target_user_id_hash is None
            or intent.proof_identity_id_hash is None
        ):
            raise ValueError("A valid account-merge intent is required")
        event_id = UUID(bytes=os.urandom(16), version=4).hex
        timestamp = datetime.now(UTC).isoformat()
        payload: dict[str, object] = {
            "schema_version": 2,
            "event_type": (
                "account_merge_committed" if committed else "account_merge_aborted"
            ),
            "event_id": event_id,
            "source_user_id_hash": intent.user_id_hash,
            "target_user_id_hash": intent.target_user_id_hash,
            "proof_identity_id_hash": intent.proof_identity_id_hash,
            "merge_intent_id": intent.event_id,
            "created_at": timestamp,
        }
        return self._persist_payload(payload, require_remote=False)

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
            if not isinstance(document, dict):
                raise ValueError("Event must be an object")
            schema_version = document.get("schema_version")
            if schema_version == 1:
                if set(document) != {
                    "schema_version", "event_id", "user_id_hash", "deleted_at", "signature"
                }:
                    raise ValueError("Unexpected deletion event fields")
                payload = {
                    "schema_version": document["schema_version"],
                    "event_id": document["event_id"],
                    "user_id_hash": document["user_id_hash"],
                    "deleted_at": document["deleted_at"],
                }
                event_type = "account_deletion"
                source_hash = str(payload["user_id_hash"])
                target_hash = None
                proof_hash = None
                intent_id = None
                timestamp = str(payload["deleted_at"])
            elif schema_version == 2:
                expected_fields = {
                    "schema_version", "event_type", "event_id",
                    "source_user_id_hash", "target_user_id_hash",
                    "proof_identity_id_hash", "merge_intent_id",
                    "created_at", "signature",
                }
                if set(document) != expected_fields:
                    raise ValueError("Unexpected account-merge event fields")
                event_type = str(document["event_type"])
                if event_type not in {
                    "account_merge_intent",
                    "account_merge_committed",
                    "account_merge_aborted",
                }:
                    raise ValueError("Unsupported account-merge event type")
                payload = {
                    key: document[key]
                    for key in expected_fields - {"signature"}
                }
                source_hash = str(payload["source_user_id_hash"])
                target_hash = str(payload["target_user_id_hash"])
                proof_hash = str(payload["proof_identity_id_hash"])
                intent_id = (
                    str(payload["merge_intent_id"])
                    if payload["merge_intent_id"] is not None
                    else None
                )
                timestamp = str(payload["created_at"])
                if event_type == "account_merge_intent" and intent_id is not None:
                    raise ValueError("Merge intent cannot reference another intent")
                if event_type != "account_merge_intent" and intent_id is None:
                    raise ValueError("Merge resolution must reference its intent")
            else:
                raise ValueError("Unsupported event version")
            event_id = str(payload["event_id"])
            if UUID(hex=event_id).hex != event_id or path.stem != event_id:
                raise ValueError("Invalid event identifier")
            for digest in (source_hash, target_hash, proof_hash):
                if digest is not None and (
                    len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise ValueError("Invalid account-merge digest")
            parsed_timestamp = datetime.fromisoformat(timestamp)
            if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
                raise ValueError("Timestamp has no timezone")
            expected = self._signature(payload)
            supplied = str(document["signature"])
            if not hmac.compare_digest(expected, supplied):
                raise ValueError("Invalid event signature")
            return ErasureEvent(
                event_id=event_id,
                user_id_hash=source_hash,
                deleted_at=timestamp,
                event_type=event_type,
                target_user_id_hash=target_hash,
                proof_identity_id_hash=proof_hash,
                merge_intent_id=intent_id,
            )
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
        erased_hashes = {
            event.user_id_hash
            for event in events
            if event.event_type == "account_deletion"
        }
        return tuple(
            user_id
            for user_id in user_ids
            if self._user_id_hash(user_id) in erased_hashes
        )

    def pending_merge_intents(
        self, events: tuple[ErasureEvent, ...]
    ) -> tuple[ErasureEvent, ...]:
        """Return merge intents that have no durable commit or abort marker."""
        resolutions: dict[str, set[str]] = {}
        for event in events:
            if event.event_type in {
                "account_merge_committed", "account_merge_aborted"
            } and event.merge_intent_id is not None:
                resolutions.setdefault(event.merge_intent_id, set()).add(event.event_type)
        if any(len(states) > 1 for states in resolutions.values()):
            raise ErasureLedgerError(
                "Conflicting commit and abort markers exist for an account merge"
            )
        resolved = set(resolutions)
        return tuple(
            event
            for event in events
            if event.event_type == "account_merge_intent"
            and event.event_id not in resolved
        )

    def resolve_pending_merge_intents(
        self,
        *,
        identity_owners: dict[str, str],
        events: tuple[ErasureEvent, ...],
    ) -> None:
        """Resolve interrupted merges using the live database, never a stale snapshot."""
        for intent in self.pending_merge_intents(events):
            proof_owner = next(
                (
                    owner_id
                    for identity_id, owner_id in identity_owners.items()
                    if self._identity_id_hash(identity_id)
                    == intent.proof_identity_id_hash
                ),
                None,
            )
            if proof_owner is None:
                raise ErasureLedgerError(
                    "An account-merge intent cannot be reconciled with the live database"
                )
            owner_hash = self._user_id_hash(proof_owner)
            if owner_hash == intent.target_user_id_hash:
                committed = True
            elif owner_hash == intent.user_id_hash:
                committed = False
            else:
                raise ErasureLedgerError(
                    "An account-merge intent has an unexpected proof-identity owner"
                )
            if not self.record_merge_resolution(intent=intent, committed=committed):
                raise ErasureLedgerError(
                    "Could not replicate an account-merge recovery decision"
                )

    def matching_merged_source_user_ids(
        self, user_ids: list[str], events: tuple[ErasureEvent, ...]
    ) -> tuple[str, ...]:
        """Select source accounts only for merges with a durable commit marker."""
        intents = {
            event.event_id: event
            for event in events
            if event.event_type == "account_merge_intent"
        }
        committed = {
            event.merge_intent_id
            for event in events
            if event.event_type == "account_merge_committed"
        }
        source_hashes = {
            intents[intent_id].user_id_hash
            for intent_id in committed
            if intent_id in intents
        }
        return tuple(
            user_id
            for user_id in user_ids
            if self._user_id_hash(user_id) in source_hashes
        )
