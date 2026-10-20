"""Transactional SQLite persistence for participant invitations."""

from datetime import UTC, datetime
import sqlite3
from uuid import UUID

from app.domain.participant_invitations import ParticipantInvitation
from app.infrastructure.database.connection import transaction


class _AcceptanceRejected(Exception):
    """Abort an acceptance transaction without exposing why it was rejected."""


class ParticipantInvitationsRepository:
    """Persist invitation digests and perform guarded lifecycle transitions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def create_for_owner(
        self,
        invitation: ParticipantInvitation,
        *,
        owner_id: str,
        now: datetime,
    ) -> bool:
        """Replace prior pending invitations and create one in a single transaction."""
        self._require_aware(now)
        normalized_owner = owner_id.strip()
        if not normalized_owner:
            raise ValueError("Owner ID must not be blank")
        if invitation.created_by_owner_id != normalized_owner:
            raise ValueError("Invitation creator must match the event owner")
        if not invitation.is_usable(now=now):
            raise ValueError("Only a usable invitation can be created")
        with transaction(self._connection):
            participant = self._connection.execute(
                """SELECT 1
                   FROM event_participants AS p
                   JOIN events AS e ON e.id = p.event_id
                   WHERE p.id = ? AND e.owner_id = ?""",
                (str(invitation.participant_id), normalized_owner),
            ).fetchone()
            if participant is None:
                return False
            self._connection.execute(
                """UPDATE participant_invitations
                   SET revoked_at = ?
                   WHERE participant_id = ? AND accepted_at IS NULL
                     AND revoked_at IS NULL AND expires_at > ?""",
                (
                    self._format(now),
                    str(invitation.participant_id),
                    self._format(now),
                ),
            )
            self._connection.execute(
                """INSERT INTO participant_invitations(
                    id, participant_id, target_email, token_hash,
                    created_by_owner_id, created_at, expires_at, accepted_at,
                    revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)""",
                (
                    str(invitation.id),
                    str(invitation.participant_id),
                    invitation.target_email,
                    invitation.token_hash,
                    normalized_owner,
                    self._format(invitation.created_at),
                    self._format(invitation.expires_at),
                ),
            )
        return True

    def get_by_token_hash(self, token_hash: str) -> ParticipantInvitation | None:
        """Look up an invitation by its digest, never by a raw bearer token."""
        digest = token_hash.strip()
        if not digest:
            return None
        row = self._connection.execute(
            """SELECT id, participant_id, target_email, token_hash,
                      created_by_owner_id, created_at, expires_at, accepted_at,
                      revoked_at
               FROM participant_invitations WHERE token_hash = ?""",
            (digest,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def revoke_for_owner(
        self,
        *,
        invitation_id: UUID,
        owner_id: str,
        now: datetime,
    ) -> bool:
        """Revoke a pending invitation only when the caller owns its event."""
        self._require_aware(now)
        normalized_owner = owner_id.strip()
        if not normalized_owner:
            return False
        cursor = self._connection.execute(
            """UPDATE participant_invitations SET revoked_at = ?
               WHERE id = ? AND accepted_at IS NULL AND revoked_at IS NULL
                 AND EXISTS (
                     SELECT 1 FROM event_participants AS p
                     JOIN events AS e ON e.id = p.event_id
                     WHERE p.id = participant_invitations.participant_id
                       AND e.owner_id = ?
                 )""",
            (self._format(now), str(invitation_id), normalized_owner),
        )
        return cursor.rowcount == 1

    def accept_for_verified_user(
        self,
        *,
        token_hash: str,
        user_id: str,
        now: datetime,
    ) -> bool:
        """Atomically consume an invite and link its participant to a verified email user.

        The user ID is accepted only if the database contains a verified email
        identity for that user whose normalized address matches the invitation.
        """
        self._require_aware(now)
        digest = token_hash.strip()
        normalized_user_id = user_id.strip()
        if not digest or not normalized_user_id:
            return False
        try:
            with transaction(self._connection):
                row = self._connection.execute(
                    """SELECT i.id, i.participant_id
                       FROM participant_invitations AS i
                       JOIN auth_identities AS a
                         ON a.user_id = ? AND a.provider = 'email'
                        AND a.verified_at IS NOT NULL
                        AND lower(trim(a.provider_subject)) = i.target_email
                       JOIN event_participants AS p
                         ON p.id = i.participant_id
                       WHERE i.token_hash = ? AND i.accepted_at IS NULL
                         AND i.revoked_at IS NULL AND i.expires_at > ?
                         AND p.linked_user_id IS NULL""",
                    (normalized_user_id, digest, self._format(now)),
                ).fetchone()
                if row is None:
                    raise _AcceptanceRejected
                accepted = self._connection.execute(
                    """UPDATE participant_invitations SET accepted_at = ?
                       WHERE id = ? AND accepted_at IS NULL
                         AND revoked_at IS NULL AND expires_at > ?""",
                    (self._format(now), str(row["id"]), self._format(now)),
                )
                if accepted.rowcount != 1:
                    raise _AcceptanceRejected
                linked = self._connection.execute(
                    """UPDATE event_participants SET linked_user_id = ?
                       WHERE id = ? AND linked_user_id IS NULL""",
                    (normalized_user_id, str(row["participant_id"])),
                )
                if linked.rowcount != 1:
                    raise _AcceptanceRejected
        except _AcceptanceRejected:
            return False
        return True

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> ParticipantInvitation:
        return ParticipantInvitation(
            id=UUID(row["id"]),
            participant_id=UUID(row["participant_id"]),
            target_email=str(row["target_email"]),
            token_hash=str(row["token_hash"]),
            created_by_owner_id=str(row["created_by_owner_id"]),
            created_at=cls._parse_required(row["created_at"]),
            expires_at=cls._parse_required(row["expires_at"]),
            accepted_at=cls._parse(row["accepted_at"]),
            revoked_at=cls._parse(row["revoked_at"]),
        )

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")

    @classmethod
    def _format(cls, value: datetime) -> str:
        cls._require_aware(value)
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )

    @classmethod
    def _parse_required(cls, value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        cls._require_aware(parsed)
        return parsed

    @classmethod
    def _parse(cls, value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        cls._require_aware(parsed)
        return parsed
