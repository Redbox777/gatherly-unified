"""Atomic, account-scoped event sync command application.

This handler is deliberately not wired to an HTTP route or the PWA yet. It is
the server-side transaction boundary that a future authenticated adapter may
call after parsing the strict command envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import sqlite3
from typing import Any
from uuid import UUID, uuid4

from app.domain.auth_tokens import hash_token
from app.infrastructure.database.connection import transaction


MAX_COMMAND_BYTES = 16_384
ALLOWED_KINDS = frozenset(
    {
        "note.create",
        "note.update",
        "note.delete",
        "task.create",
        "task.update",
        "task.complete",
        "task.delete",
        "venue.set",
        "venue.delete",
    }
)
OWNER_ONLY_KINDS = frozenset(
    {
        "task.create",
        "task.update",
        "task.complete",
        "task.delete",
        "venue.set",
        "venue.delete",
    }
)


@dataclass(frozen=True, slots=True)
class SyncCommandResult:
    """Stable HTTP-shaped result persisted for idempotent replay."""

    status_code: int
    body: dict[str, Any]
    replayed: bool = False


class EventSyncCommandHandler:
    """Apply an allowlisted event command in one SQLite write transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(
        self,
        *,
        raw_session_token: str,
        operation_id: str,
        event_id: str,
        base_revision: int,
        kind: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> SyncCommandResult:
        """Authenticate, authorize, compare revision, mutate, and record atomically."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")
        try:
            normalized_operation_id = str(UUID(operation_id))
            normalized_event_id = str(UUID(event_id))
        except (ValueError, TypeError, AttributeError):
            return self._error(422, "invalid_identifier")
        if normalized_operation_id != operation_id.lower():
            return self._error(422, "invalid_operation_id")
        if not isinstance(base_revision, int) or isinstance(base_revision, bool):
            return self._error(422, "invalid_base_revision")
        if base_revision < 0:
            return self._error(422, "invalid_base_revision")
        if kind not in ALLOWED_KINDS:
            return self._error(422, "unsupported_command")
        if not isinstance(payload, dict):
            return self._error(422, "invalid_payload")

        envelope = {
            "event_id": normalized_event_id,
            "base_revision": base_revision,
            "kind": kind,
            "payload": payload,
        }
        try:
            canonical = json.dumps(
                envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return self._error(422, "invalid_payload")
        if len(canonical.encode("utf-8")) > MAX_COMMAND_BYTES:
            return self._error(413, "command_too_large")
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        timestamp = self._format(now)

        if not raw_session_token or not raw_session_token.strip():
            return self._error(401, "authentication_required")

        with transaction(self._connection):
            session = self._connection.execute(
                """SELECT sessions.user_id
                   FROM auth_sessions AS sessions
                   JOIN users ON users.id = sessions.user_id
                   WHERE sessions.token_hash = ?
                     AND sessions.revoked_at IS NULL
                     AND sessions.expires_at > ?""",
                (hash_token(raw_session_token), timestamp),
            ).fetchone()
            if session is None:
                return self._error(401, "authentication_required")
            user_id = str(session["user_id"])

            existing = self._connection.execute(
                """SELECT event_id, command_kind, payload_hash,
                          response_status, response_json
                   FROM sync_idempotency_records
                   WHERE user_id = ? AND operation_id = ?""",
                (user_id, normalized_operation_id),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    return self._error(409, "idempotency_key_reused")
                if str(existing["event_id"]) != normalized_event_id:
                    return self._error(409, "idempotency_key_reused")
                if not self._can_access(
                    event_id=normalized_event_id,
                    user_id=user_id,
                    owner_only=kind in OWNER_ONLY_KINDS,
                ):
                    return self._error(404, "event_not_found")
                return SyncCommandResult(
                    status_code=int(existing["response_status"]),
                    body=json.loads(str(existing["response_json"])),
                    replayed=True,
                )

            owner = self._connection.execute(
                "SELECT owner_id FROM events WHERE id = ?", (normalized_event_id,)
            ).fetchone()
            if owner is None:
                return self._error(404, "event_not_found")
            is_owner = str(owner["owner_id"]) == user_id
            if not is_owner and not self._is_linked_member(
                event_id=normalized_event_id, user_id=user_id
            ):
                return self._error(404, "event_not_found")
            if kind in OWNER_ONLY_KINDS and not is_owner:
                return self._error(404, "event_not_found")

            state = self._connection.execute(
                "SELECT revision FROM event_sync_state WHERE event_id = ?",
                (normalized_event_id,),
            ).fetchone()
            if state is None:
                return self._error(404, "event_not_found")
            current_revision = int(state["revision"])
            if current_revision != base_revision:
                return SyncCommandResult(
                    status_code=409,
                    body={
                        "error": "revision_conflict",
                        "current_revision": current_revision,
                    },
                )

            try:
                status_code, body = self._apply_command(
                    event_id=normalized_event_id,
                    user_id=user_id,
                    owner_id=str(owner["owner_id"]),
                    kind=kind,
                    payload=payload,
                    now=now,
                )
            except ValueError as error:
                return self._error(422, str(error))
            if status_code >= 400:
                return SyncCommandResult(status_code=status_code, body=body)

            revision_row = self._connection.execute(
                "SELECT revision FROM event_sync_state WHERE event_id = ?",
                (normalized_event_id,),
            ).fetchone()
            if revision_row is None:
                raise RuntimeError("Event sync state disappeared during command")
            body["revision"] = int(revision_row["revision"])
            self._connection.execute(
                """INSERT INTO sync_idempotency_records(
                    user_id, operation_id, event_id, command_kind, payload_hash,
                    response_status, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    normalized_operation_id,
                    normalized_event_id,
                    kind,
                    payload_hash,
                    status_code,
                    json.dumps(
                        body,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    timestamp,
                ),
            )
            return SyncCommandResult(status_code=status_code, body=body)

    def _apply_command(
        self,
        *,
        event_id: str,
        user_id: str,
        owner_id: str,
        kind: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> tuple[int, dict[str, Any]]:
        timestamp = self._format(now)
        if kind == "note.create":
            self._keys(payload, {"content", "visibility"})
            content = self._text(payload, "content", 1, 5000)
            visibility = self._choice(payload, "visibility", {"private", "shared"})
            note_id = str(uuid4())
            self._connection.execute(
                """INSERT INTO event_notes(
                    id, event_id, author_id, content, visibility, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (note_id, event_id, user_id, content, visibility, timestamp, timestamp),
            )
            return 201, {
                "id": note_id,
                "event_id": event_id,
                "author_id": user_id,
                "content": content,
                "visibility": visibility,
                "created_at": timestamp,
                "updated_at": timestamp,
                "can_edit": True,
                "can_delete": True,
            }

        if kind in {"note.update", "note.delete"}:
            expected = {"note_id"} if kind == "note.delete" else {
                "note_id", "content", "visibility"
            }
            self._keys(payload, expected)
            note_id = self._uuid_text(payload, "note_id")
            note = self._connection.execute(
                """SELECT id, author_id, visibility FROM event_notes
                   WHERE id = ? AND event_id = ?""",
                (note_id, event_id),
            ).fetchone()
            if note is None:
                return 404, {"error": "note_not_found"}
            author_id = str(note["author_id"])
            visibility = str(note["visibility"])
            if kind == "note.update":
                if author_id != user_id:
                    return 404, {"error": "note_not_found"}
                content = self._text(payload, "content", 1, 5000)
                new_visibility = self._choice(
                    payload, "visibility", {"private", "shared"}
                )
                self._connection.execute(
                    """UPDATE event_notes SET content = ?, visibility = ?, updated_at = ?
                       WHERE id = ? AND event_id = ? AND author_id = ?""",
                    (content, new_visibility, timestamp, note_id, event_id, user_id),
                )
                return 200, {
                    "id": note_id,
                    "event_id": event_id,
                    "author_id": user_id,
                    "content": content,
                    "visibility": new_visibility,
                    "updated_at": timestamp,
                    "can_edit": True,
                    "can_delete": True,
                }
            if author_id != user_id and not (
                visibility == "shared" and owner_id == user_id
            ):
                return 404, {"error": "note_not_found"}
            self._connection.execute(
                "DELETE FROM event_notes WHERE id = ? AND event_id = ?",
                (note_id, event_id),
            )
            return 200, {"deleted": True, "id": note_id}

        if kind == "task.create":
            self._keys(
                payload,
                {"title", "description", "due_at", "assignee_participant_id"},
            )
            title = self._text(payload, "title", 1, 160)
            description = self._text(payload, "description", 0, 2000)
            due_at = self._optional_datetime(payload, "due_at")
            assignee = self._optional_uuid(payload, "assignee_participant_id")
            self._validate_assignee(event_id, assignee)
            task_id = str(uuid4())
            self._connection.execute(
                """INSERT INTO event_tasks(
                    id, event_id, title, description, due_at,
                    assignee_participant_id, completed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (task_id, event_id, title, description, due_at, assignee, timestamp, timestamp),
            )
            return 201, {
                "id": task_id,
                "event_id": event_id,
                "title": title,
                "description": description,
                "due_at": due_at,
                "assignee_participant_id": assignee,
                "completed": False,
            }

        if kind in {"task.update", "task.complete", "task.delete"}:
            task_id = self._uuid_text(payload, "task_id")
            if kind == "task.delete":
                self._keys(payload, {"task_id"})
                cursor = self._connection.execute(
                    "DELETE FROM event_tasks WHERE id = ? AND event_id = ?",
                    (task_id, event_id),
                )
                if cursor.rowcount != 1:
                    return 404, {"error": "task_not_found"}
                return 200, {"deleted": True, "id": task_id}
            if kind == "task.complete":
                self._keys(payload, {"task_id", "completed"})
                completed = payload.get("completed")
                if not isinstance(completed, bool):
                    raise ValueError("completed must be a boolean")
                cursor = self._connection.execute(
                    """UPDATE event_tasks SET completed = ?, updated_at = ?
                       WHERE id = ? AND event_id = ?""",
                    (int(completed), timestamp, task_id, event_id),
                )
                if cursor.rowcount != 1:
                    return 404, {"error": "task_not_found"}
            else:
                self._keys(
                    payload,
                    {
                        "task_id", "title", "description", "due_at",
                        "assignee_participant_id",
                    },
                )
                title = self._text(payload, "title", 1, 160)
                description = self._text(payload, "description", 0, 2000)
                due_at = self._optional_datetime(payload, "due_at")
                assignee = self._optional_uuid(payload, "assignee_participant_id")
                self._validate_assignee(event_id, assignee)
                cursor = self._connection.execute(
                    """UPDATE event_tasks SET title = ?, description = ?, due_at = ?,
                       assignee_participant_id = ?, updated_at = ?
                       WHERE id = ? AND event_id = ?""",
                    (title, description, due_at, assignee, timestamp, task_id, event_id),
                )
                if cursor.rowcount != 1:
                    return 404, {"error": "task_not_found"}
            row = self._connection.execute(
                """SELECT id, event_id, title, description, due_at,
                          assignee_participant_id, completed
                   FROM event_tasks WHERE id = ? AND event_id = ?""",
                (task_id, event_id),
            ).fetchone()
            result = dict(row)
            result["completed"] = bool(result["completed"])
            return 200, result

        if kind == "venue.set":
            self._keys(payload, {"venue_name", "address", "latitude", "longitude"})
            name = self._text(payload, "venue_name", 1, 160)
            address = self._text(payload, "address", 1, 500)
            latitude = self._coordinate(payload, "latitude", -90, 90)
            longitude = self._coordinate(payload, "longitude", -180, 180)
            if (latitude is None) != (longitude is None):
                raise ValueError("coordinates must be provided together")
            self._connection.execute(
                """INSERT INTO event_venues(
                    event_id, venue_name, address, latitude, longitude
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    venue_name = excluded.venue_name,
                    address = excluded.address,
                    latitude = excluded.latitude,
                    longitude = excluded.longitude""",
                (event_id, name, address, latitude, longitude),
            )
            return 200, {
                "event_id": event_id,
                "venue_name": name,
                "address": address,
                "latitude": latitude,
                "longitude": longitude,
            }

        if kind == "venue.delete":
            self._keys(payload, set())
            cursor = self._connection.execute(
                "DELETE FROM event_venues WHERE event_id = ?", (event_id,)
            )
            if cursor.rowcount != 1:
                return 404, {"error": "venue_not_found"}
            return 200, {"deleted": True, "event_id": event_id}

        raise ValueError("Unsupported command")

    def _can_access(self, *, event_id: str, user_id: str, owner_only: bool) -> bool:
        row = self._connection.execute(
            "SELECT owner_id FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return False
        if str(row["owner_id"]) == user_id:
            return True
        if owner_only:
            return False
        return self._is_linked_member(event_id=event_id, user_id=user_id)

    def _is_linked_member(self, *, event_id: str, user_id: str) -> bool:
        return self._connection.execute(
            """SELECT 1 FROM event_participants
               WHERE event_id = ? AND linked_user_id = ?""",
            (event_id, user_id),
        ).fetchone() is not None

    def _validate_assignee(self, event_id: str, participant_id: str | None) -> None:
        if participant_id is None:
            return
        if self._connection.execute(
            "SELECT 1 FROM event_participants WHERE id = ? AND event_id = ?",
            (participant_id, event_id),
        ).fetchone() is None:
            raise ValueError("Assignee must belong to this event")

    @staticmethod
    def _keys(payload: dict[str, Any], expected: set[str]) -> None:
        if set(payload) != expected:
            raise ValueError("Payload fields do not match command schema")

    @staticmethod
    def _text(
        payload: dict[str, Any], key: str, minimum: int, maximum: int
    ) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        normalized = value.strip()
        if not minimum <= len(normalized) <= maximum:
            raise ValueError(f"{key} has invalid length")
        return normalized

    @staticmethod
    def _choice(payload: dict[str, Any], key: str, choices: set[str]) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"{key} has an unsupported value")
        return value

    @staticmethod
    def _uuid_text(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a UUID")
        try:
            normalized = str(UUID(value))
        except ValueError as error:
            raise ValueError(f"{key} must be a UUID") from error
        if normalized != value.lower():
            raise ValueError(f"{key} must be a canonical UUID")
        return normalized

    @classmethod
    def _optional_uuid(cls, payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        return cls._uuid_text(payload, key)

    @staticmethod
    def _optional_datetime(payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{key} must be an ISO datetime or null")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{key} must be an ISO datetime or null") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{key} must include a timezone")
        return parsed.isoformat()

    @staticmethod
    def _coordinate(
        payload: dict[str, Any], key: str, minimum: float, maximum: float
    ) -> float | None:
        value = payload.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric or null")
        numeric = float(value)
        if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
            raise ValueError(f"{key} is out of range")
        return numeric

    @staticmethod
    def _format(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

    @staticmethod
    def _error(status_code: int, error: str) -> SyncCommandResult:
        return SyncCommandResult(status_code=status_code, body={"error": error})
