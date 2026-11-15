"""Atomic, account-scoped server processing for offline-safe event commands.

The handler deliberately owns one BEGIN IMMEDIATE transaction. It does not call
repositories that start their own transactions, so authorization, revision
checks, domain writes and the idempotency receipt commit or roll back together.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.auth_tokens import hash_token
from app.infrastructure.database.connection import transaction


class OfflineSyncError(Exception):
    """A deterministic command rejection suitable for an HTTP response."""

    def __init__(
        self, status_code: int, detail: str, *, current_revision: int | None = None
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.current_revision = current_revision


class OfflineSyncRepository:
    """Apply allowlisted commands with transactional idempotency and revisions."""

    _MAX_CANONICAL_BYTES = 16_384

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def apply(
        self,
        *,
        session_token: str,
        operation_id: str,
        event_id: str,
        base_revision: int,
        kind: str,
        payload: dict[str, object],
        now: datetime | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not session_token or not session_token.strip():
            raise OfflineSyncError(401, "Authentication required")
        session_digest = hash_token(session_token)
        try:
            normalized_operation_id = str(UUID(operation_id))
            normalized_event_id = str(UUID(event_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise OfflineSyncError(422, "Invalid operation or event identifier") from error
        if base_revision < 1:
            raise OfflineSyncError(422, "base_revision must be positive")
        if not isinstance(payload, dict):
            raise OfflineSyncError(422, "Command payload must be an object")

        canonical = self._canonical_envelope(
            event_id=normalized_event_id,
            base_revision=base_revision,
            kind=kind,
            payload=payload,
        )
        if len(canonical.encode("utf-8")) > self._MAX_CANONICAL_BYTES:
            raise OfflineSyncError(413, "Command payload is too large")
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        timestamp = self._timestamp(now or datetime.now(UTC))

        with transaction(self._connection):
            session = self._connection.execute(
                """SELECT user_id FROM auth_sessions
                   WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?""",
                (session_digest, timestamp),
            ).fetchone()
            if session is None:
                raise OfflineSyncError(401, "Authentication required")
            user_id = str(session["user_id"])

            authorization = self._connection.execute(
                """SELECT events.owner_id, state.revision
                   FROM events
                   JOIN event_sync_state AS state ON state.event_id = events.id
                   WHERE events.id = ?""",
                (normalized_event_id,),
            ).fetchone()
            if authorization is None:
                raise OfflineSyncError(404, "Event not found")

            owner_id = str(authorization["owner_id"])
            is_owner = owner_id == user_id
            is_member = is_owner or self._connection.execute(
                """SELECT 1 FROM event_participants
                   WHERE event_id = ? AND linked_user_id = ?""",
                (normalized_event_id, user_id),
            ).fetchone() is not None

            if kind.startswith(("task.", "venue.")) and not is_owner:
                raise OfflineSyncError(404, "Event not found")
            if kind.startswith("note.") and not is_member:
                raise OfflineSyncError(404, "Event not found")
            if not kind.startswith(("note.", "task.", "venue.")):
                raise OfflineSyncError(422, "Unsupported command kind")

            previous = self._connection.execute(
                """SELECT payload_hash, response_status, response_json
                   FROM offline_sync_idempotency
                   WHERE user_id = ? AND operation_id = ?""",
                (user_id, normalized_operation_id),
            ).fetchone()
            if previous is not None:
                if previous["payload_hash"] != payload_hash:
                    raise OfflineSyncError(
                        409, "Idempotency key was already used with another payload"
                    )
                return int(previous["response_status"]), json.loads(
                    str(previous["response_json"])
                )

            current_revision = int(authorization["revision"])
            if current_revision != base_revision:
                raise OfflineSyncError(
                    409,
                    "Event revision conflict",
                    current_revision=current_revision,
                )

            status_code, result = self._apply_domain_command(
                user_id=user_id,
                owner_id=owner_id,
                event_id=normalized_event_id,
                kind=kind,
                payload=payload,
                timestamp=timestamp,
            )
            updated_revision = self._connection.execute(
                "SELECT revision FROM event_sync_state WHERE event_id = ?",
                (normalized_event_id,),
            ).fetchone()
            if updated_revision is None:
                raise OfflineSyncError(404, "Event not found")
            response: dict[str, object] = {
                "status": "applied",
                "result": result,
                "revision": int(updated_revision["revision"]),
            }
            response_json = json.dumps(
                response, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            self._connection.execute(
                """INSERT INTO offline_sync_idempotency(
                    user_id, operation_id, event_id, payload_hash,
                    response_status, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    normalized_operation_id,
                    normalized_event_id,
                    payload_hash,
                    status_code,
                    response_json,
                    timestamp,
                ),
            )
        return status_code, response

    def get_revision(self, *, event_id: str, user_id: str) -> int | None:
        """Return an event revision only when the caller is still a member."""
        row = self._connection.execute(
            """SELECT events.owner_id, state.revision
               FROM events
               JOIN event_sync_state AS state ON state.event_id = events.id
               WHERE events.id = ?""",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        if str(row["owner_id"]) != user_id:
            member = self._connection.execute(
                """SELECT 1 FROM event_participants
                   WHERE event_id = ? AND linked_user_id = ?""",
                (event_id, user_id),
            ).fetchone()
            if member is None:
                return None
        return int(row["revision"])

    @staticmethod
    def _canonical_envelope(
        *, event_id: str, base_revision: int, kind: str, payload: dict[str, object]
    ) -> str:
        try:
            return json.dumps(
                {
                    "event_id": event_id,
                    "base_revision": base_revision,
                    "kind": kind,
                    "payload": payload,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise OfflineSyncError(422, "Command payload is not valid JSON") from error

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise OfflineSyncError(422, "Server timestamp must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _keys(
        payload: dict[str, object], required: set[str], optional: set[str] | None = None
    ) -> None:
        allowed = required | (optional or set())
        if not required <= payload.keys() or not payload.keys() <= allowed:
            raise OfflineSyncError(422, "Invalid command payload fields")

    def _apply_domain_command(
        self,
        *,
        user_id: str,
        owner_id: str,
        event_id: str,
        kind: str,
        payload: dict[str, object],
        timestamp: str,
    ) -> tuple[int, dict[str, object]]:
        if kind == "note.create":
            self._keys(payload, {"content"}, {"visibility"})
            content = self._text(payload, "content", 5000)
            visibility = self._visibility(payload.get("visibility", "private"))
            note_id = str(uuid4())
            self._connection.execute(
                """INSERT INTO event_notes(
                    id, event_id, author_id, content, visibility, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (note_id, event_id, user_id, content, visibility, timestamp, timestamp),
            )
            return 201, self._note_result(note_id, user_id)

        if kind in {"note.update", "note.delete"}:
            self._keys(payload, {"note_id"} if kind == "note.delete" else {
                "note_id", "content", "visibility"
            })
            note_id = self._text(payload, "note_id", 80)
            note = self._connection.execute(
                """SELECT author_id, visibility FROM event_notes
                   WHERE id = ? AND event_id = ?""",
                (note_id, event_id),
            ).fetchone()
            if note is None:
                raise OfflineSyncError(404, "Note not found")
            can_delete = str(note["author_id"]) == user_id or (
                str(note["visibility"]) == "shared" and owner_id == user_id
            )
            if kind == "note.update":
                if str(note["author_id"]) != user_id:
                    raise OfflineSyncError(404, "Note not found")
                content = self._text(payload, "content", 5000)
                visibility = self._visibility(payload.get("visibility"))
                self._connection.execute(
                    """UPDATE event_notes SET content = ?, visibility = ?, updated_at = ?
                       WHERE id = ? AND event_id = ? AND author_id = ?""",
                    (content, visibility, timestamp, note_id, event_id, user_id),
                )
                return 200, self._note_result(note_id, user_id)
            if not can_delete:
                raise OfflineSyncError(404, "Note not found")
            self._connection.execute(
                "DELETE FROM event_notes WHERE id = ? AND event_id = ?",
                (note_id, event_id),
            )
            return 200, {"deleted": True, "note_id": note_id}

        if kind == "task.create":
            self._keys(
                payload, {"title"}, {"description", "due_at", "assignee_participant_id"}
            )
            title = self._text(payload, "title", 160)
            description = self._text(payload, "description", 2000, default="", allow_empty=True)
            due_at = self._due_at(payload.get("due_at"))
            assignee = self._optional_text(payload.get("assignee_participant_id"), 80)
            self._validate_assignee(event_id, assignee)
            task_id = str(uuid4())
            self._connection.execute(
                """INSERT INTO event_tasks(
                    id, event_id, title, description, due_at, assignee_participant_id,
                    completed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (task_id, event_id, title, description, due_at, assignee, timestamp, timestamp),
            )
            return 201, self._task_result(task_id, event_id)

        if kind == "task.update":
            self._keys(
                payload,
                {"task_id", "title"},
                {"description", "due_at", "assignee_participant_id"},
            )
            task_id = self._text(payload, "task_id", 80)
            title = self._text(payload, "title", 160)
            description = self._text(payload, "description", 2000, default="")
            due_at = self._due_at(payload.get("due_at"))
            assignee = self._optional_text(payload.get("assignee_participant_id"), 80)
            self._validate_assignee(event_id, assignee)
            cursor = self._connection.execute(
                """UPDATE event_tasks SET title = ?, description = ?, due_at = ?,
                       assignee_participant_id = ?, updated_at = ?
                   WHERE id = ? AND event_id = ?""",
                (title, description, due_at, assignee, timestamp, task_id, event_id),
            )
            if cursor.rowcount != 1:
                raise OfflineSyncError(404, "Task not found")
            return 200, self._task_result(task_id, event_id)

        if kind == "task.complete":
            self._keys(payload, {"task_id", "completed"})
            task_id = self._text(payload, "task_id", 80)
            completed = payload["completed"]
            if not isinstance(completed, bool):
                raise OfflineSyncError(422, "completed must be a boolean")
            cursor = self._connection.execute(
                """UPDATE event_tasks SET completed = ?, updated_at = ?
                   WHERE id = ? AND event_id = ?""",
                (int(completed), timestamp, task_id, event_id),
            )
            if cursor.rowcount != 1:
                raise OfflineSyncError(404, "Task not found")
            return 200, self._task_result(task_id, event_id)

        if kind == "task.delete":
            self._keys(payload, {"task_id"})
            task_id = self._text(payload, "task_id", 80)
            cursor = self._connection.execute(
                "DELETE FROM event_tasks WHERE id = ? AND event_id = ?",
                (task_id, event_id),
            )
            if cursor.rowcount != 1:
                raise OfflineSyncError(404, "Task not found")
            return 200, {"deleted": True, "task_id": task_id}

        if kind == "venue.set":
            self._keys(
                payload,
                {"venue_name", "address"},
                {"latitude", "longitude"},
            )
            name = self._text(payload, "venue_name", 160)
            address = self._text(payload, "address", 500)
            latitude = payload.get("latitude")
            longitude = payload.get("longitude")
            if (latitude is None) != (longitude is None):
                raise OfflineSyncError(422, "Coordinates must be provided together")
            if latitude is not None and (
                isinstance(latitude, bool) or not isinstance(latitude, (int, float))
                or not -90 <= latitude <= 90
            ):
                raise OfflineSyncError(422, "Latitude is out of range")
            if longitude is not None and (
                isinstance(longitude, bool) or not isinstance(longitude, (int, float))
                or not -180 <= longitude <= 180
            ):
                raise OfflineSyncError(422, "Longitude is out of range")
            self._connection.execute(
                """INSERT INTO event_venues(event_id, venue_name, address, latitude, longitude)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET
                     venue_name = excluded.venue_name, address = excluded.address,
                     latitude = excluded.latitude, longitude = excluded.longitude""",
                (event_id, name, address, latitude, longitude),
            )
            return 200, {
                "venue_name": name,
                "address": address,
                "latitude": latitude,
                "longitude": longitude,
            }

        if kind == "venue.delete":
            self._keys(payload, set())
            self._connection.execute(
                "DELETE FROM event_venues WHERE event_id = ?", (event_id,)
            )
            return 200, {"deleted": True}

        raise OfflineSyncError(422, "Unsupported command kind")

    def _note_result(self, note_id: str, user_id: str) -> dict[str, object]:
        row = self._connection.execute(
            """SELECT note.id, note.event_id, note.author_id, note.content,
                      note.visibility, note.created_at, note.updated_at,
                      (note.author_id = ?) AS can_edit,
                      (note.author_id = ? OR (note.visibility = 'shared'
                        AND events.owner_id = ?)) AS can_delete
               FROM event_notes AS note
               JOIN events ON events.id = note.event_id
               WHERE note.id = ?""",
            (user_id, user_id, user_id, note_id),
        ).fetchone()
        if row is None:
            raise OfflineSyncError(404, "Note not found")
        return dict(row)

    def _task_result(self, task_id: str, event_id: str) -> dict[str, object]:
        row = self._connection.execute(
            """SELECT task.id, task.event_id, task.title, task.description,
                      task.due_at, task.assignee_participant_id, participant.display_name,
                      task.completed
               FROM event_tasks AS task
               LEFT JOIN event_participants AS participant
                 ON participant.id = task.assignee_participant_id
               WHERE task.id = ? AND task.event_id = ?""",
            (task_id, event_id),
        ).fetchone()
        if row is None:
            raise OfflineSyncError(404, "Task not found")
        result = dict(row)
        result["completed"] = bool(result["completed"])
        return result

    def _validate_assignee(self, event_id: str, participant_id: str | None) -> None:
        if participant_id is not None and self._connection.execute(
            "SELECT 1 FROM event_participants WHERE id = ? AND event_id = ?",
            (participant_id, event_id),
        ).fetchone() is None:
            raise OfflineSyncError(422, "Assignee must belong to this event")

    @staticmethod
    def _text(
        payload: dict[str, object], key: str, maximum: int, *, default: str | None = None,
        allow_empty: bool = False,
    ) -> str:
        value = payload.get(key, default)
        if not isinstance(value, str):
            raise OfflineSyncError(422, f"{key} must be text")
        normalized = value.strip()
        if (not normalized and not allow_empty) or len(normalized) > maximum:
            minimum = 0 if allow_empty else 1
            raise OfflineSyncError(422, f"{key} must contain {minimum} to {maximum} characters")
        return normalized

    @staticmethod
    def _optional_text(value: object, maximum: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise OfflineSyncError(422, "Invalid optional identifier")
        try:
            return str(UUID(value.strip()))
        except ValueError as error:
            raise OfflineSyncError(422, "Invalid optional identifier") from error

    @staticmethod
    def _visibility(value: object) -> str:
        if value not in {"private", "shared"}:
            raise OfflineSyncError(422, "Unsupported note visibility")
        return str(value)

    @staticmethod
    def _due_at(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise OfflineSyncError(422, "due_at must be an ISO datetime or null")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise OfflineSyncError(422, "Invalid due_at datetime") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise OfflineSyncError(422, "due_at must include a timezone")
        return parsed.isoformat()
