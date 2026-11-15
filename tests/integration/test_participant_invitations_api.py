from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.domain.auth_tokens import hash_token
from app.domain.participant_invitations import ParticipantInvitation
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.participant_invitations_repository import (
    ParticipantInvitationsRepository,
)
from app.interfaces.web.http_api import create_app


def _read_rows(app, query: str, parameters: tuple = ()):
    connection = connect(app.state.database_path)
    try:
        return connection.execute(query, parameters).fetchall()
    finally:
        connection.close()


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.fail = False

    def send_text(self, *, recipient: str, subject: str, body: str) -> None:
        if self.fail:
            raise OSError("SMTP unavailable")
        self.messages.append((recipient, subject, body))


def _register_and_sign_in(client: TestClient, sender: FakeEmailSender, email: str) -> None:
    registration = client.post(
        "/auth/register",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert registration.status_code == 202
    token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
    login = client.post(
        "/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert login.status_code == 200


def test_invitation_api_requires_owner_and_accepts_only_authenticated_session(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-invitations-api.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        assert client.post(
            "/events/00000000-0000-0000-0000-000000000001/participants/"
            "00000000-0000-0000-0000-000000000002/invitation",
            json={"email": "invitee@example.test"},
        ).status_code == 401

        _register_and_sign_in(client, sender, "owner@example.test")
        event = client.post("/events", json={"title": "Game night"})
        assert event.status_code == 201
        event_id = event.json()["id"]
        participant = client.post(
            f"/events/{event_id}/participants", json={"display_name": "Alice"}
        )
        assert participant.status_code == 201
        participant_id = participant.json()["id"]

        created = client.post(
            f"/events/{event_id}/participants/{participant_id}/invitation",
            json={"email": "owner@example.test"},
        )
        assert created.status_code == 201
        assert "token" not in created.json()
        assert sender.messages[-1][0] == "owner@example.test"
        invitation_token = (
            sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        )
        accepted = client.post(
            "/participant-invitations/accept", json={"token": invitation_token}
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"message": "Invitation accepted"}
        replayed = client.post(
            "/participant-invitations/accept", json={"token": invitation_token}
        )
        assert replayed.status_code == 400

        participant_row = _read_rows(
            app,
            "SELECT linked_user_id FROM event_participants WHERE id = ?",
            (participant_id,),
        )[0]
        assert participant_row["linked_user_id"] is not None


def test_delivery_failure_revokes_new_invitation(tmp_path, monkeypatch, caplog) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-invitation-delivery-failure.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_sign_in(client, sender, "owner@example.test")
        event = client.post("/events", json={"title": "Game night"}).json()
        participant = client.post(
            f"/events/{event['id']}/participants", json={"display_name": "Alice"}
        ).json()
        sender.fail = True
        response = client.post(
            f"/events/{event['id']}/participants/{participant['id']}/invitation",
            json={"email": "invitee@example.test"},
        )
        assert response.status_code == 503
        assert "Invitation email delivery failed (OSError)" in caplog.text
        assert "invitee@example.test" not in caplog.text
        assert "SMTP unavailable" not in caplog.text
        row = _read_rows(
            app, "SELECT accepted_at, revoked_at FROM participant_invitations"
        )[0]
        assert row["accepted_at"] is None
        assert row["revoked_at"] is not None



def test_invitation_acceptance_page_is_served_without_reflecting_token(tmp_path) -> None:
    app = create_app(tmp_path / "participant-invitation-page.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        response = client.get("/accept-invitation")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "example-secret-token" not in response.text
    assert "window.location.hash.slice(1)" in response.text
    assert "history.replaceState" in response.text
    assert 'fetch("/participant-invitations/accept"' in response.text
    assert "fetch(\"/auth/login\"" in response.text


def test_invitation_rejects_wrong_signed_in_account_and_ignores_client_user_id(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-invitation-account-scope.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_sign_in(client, sender, "owner@example.test")
        event = client.post("/events", json={"title": "Game night"}).json()
        participant = client.post(
            f"/events/{event['id']}/participants", json={"display_name": "Alice"}
        ).json()
        created = client.post(
            f"/events/{event['id']}/participants/{participant['id']}/invitation",
            json={"email": "invitee@example.test"},
        )
        assert created.status_code == 201
        invitation_token = (
            sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        )

        wrong_account = client.post(
            "/participant-invitations/accept",
            json={"token": invitation_token, "user_id": "invitee-user"},
        )
        assert wrong_account.status_code == 400
        row = _read_rows(
            app, "SELECT accepted_at FROM participant_invitations"
        )[0]
        assert row["accepted_at"] is None

        _register_and_sign_in(client, sender, "invitee@example.test")
        accepted = client.post(
            "/participant-invitations/accept",
            json={"token": invitation_token, "user_id": "owner-user"},
        )
        assert accepted.status_code == 200
        linked = _read_rows(
            app,
            "SELECT linked_user_id FROM event_participants WHERE id = ?",
            (participant["id"],),
        )[0]["linked_user_id"]
        assert linked is not None
        identity = _read_rows(
            app,
            "SELECT provider_subject FROM auth_identities WHERE user_id = ?",
            (linked,),
        )[0]
        assert identity["provider_subject"] == "invitee@example.test"


def test_invitation_cannot_target_participant_from_another_event(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-invitation-cross-event.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_sign_in(client, sender, "owner@example.test")
        first_event = client.post("/events", json={"title": "First"}).json()
        participant = client.post(
            f"/events/{first_event['id']}/participants",
            json={"display_name": "Alice"},
        ).json()
        second_event = client.post("/events", json={"title": "Second"}).json()

        response = client.post(
            f"/events/{second_event['id']}/participants/{participant['id']}/invitation",
            json={"email": "invitee@example.test"},
        )

        assert response.status_code == 404
        assert _read_rows(
            app, "SELECT COUNT(*) FROM participant_invitations"
        )[0][0] == 0




def test_resend_invalidates_old_token_and_owner_can_revoke_new_invitation(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-invitation-lifecycle.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_sign_in(client, sender, "owner@example.test")
        event = client.post("/events", json={"title": "Game night"}).json()
        participant = client.post(
            f"/events/{event['id']}/participants", json={"display_name": "Alice"}
        ).json()
        endpoint = (
            f"/events/{event['id']}/participants/{participant['id']}/invitation"
        )

        first = client.post(endpoint, json={"email": "invitee@example.test"})
        assert first.status_code == 201
        first_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]

        second = client.post(endpoint, json={"email": "invitee@example.test"})
        assert second.status_code == 201
        second_token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        assert first_token != second_token
        assert client.post(
            "/participant-invitations/accept", json={"token": first_token}
        ).status_code == 400

        revoked = client.delete(
            f"/events/{event['id']}/participant-invitations/{second.json()['id']}"
        )
        assert revoked.status_code == 204
        assert client.post(
            "/participant-invitations/accept", json={"token": second_token}
        ).status_code == 400

        rows = _read_rows(
            app,
            "SELECT accepted_at, revoked_at FROM participant_invitations ORDER BY created_at",
        )
        assert len(rows) == 2
        assert all(row["accepted_at"] is None for row in rows)
        assert all(row["revoked_at"] is not None for row in rows)


def test_expired_invitation_cannot_be_accepted(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timedelta

    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-invitation-expired.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_sign_in(client, sender, "owner@example.test")
        event = client.post("/events", json={"title": "Game night"}).json()
        participant = client.post(
            f"/events/{event['id']}/participants", json={"display_name": "Alice"}
        ).json()
        created = client.post(
            f"/events/{event['id']}/participants/{participant['id']}/invitation",
            json={"email": "invitee@example.test"},
        )
        assert created.status_code == 201
        token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]

        connection = connect(app.state.database_path)
        try:
            created_at = connection.execute(
                "SELECT created_at FROM participant_invitations WHERE id = ?",
                (created.json()["id"],),
            ).fetchone()["created_at"]
            expired_at = datetime.fromisoformat(created_at) + timedelta(microseconds=1)
            connection.execute(
                "UPDATE participant_invitations SET expires_at = ? WHERE id = ?",
                (expired_at.isoformat(), created.json()["id"]),
            )
            connection.commit()
        finally:
            connection.close()

        _register_and_sign_in(client, sender, "invitee@example.test")
        rejected = client.post("/participant-invitations/accept", json={"token": token})
        assert rejected.status_code == 400
        assert _read_rows(
            app, "SELECT accepted_at FROM participant_invitations WHERE id = ?",
            (created.json()["id"],),
        )[0]["accepted_at"] is None


def test_removing_participant_invalidates_its_pending_invitation(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-invitation-participant-delete.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _register_and_sign_in(client, sender, "owner@example.test")
        event = client.post("/events", json={"title": "Game night"}).json()
        participant = client.post(
            f"/events/{event['id']}/participants", json={"display_name": "Alice"}
        ).json()
        created = client.post(
            f"/events/{event['id']}/participants/{participant['id']}/invitation",
            json={"email": "invitee@example.test"},
        )
        assert created.status_code == 201

        removed = client.delete(
            f"/events/{event['id']}/participants/{participant['id']}"
        )
        assert removed.status_code == 204
        assert _read_rows(
            app, "SELECT COUNT(*) FROM participant_invitations WHERE id = ?",
            (created.json()["id"],),
        )[0][0] == 0


def test_invitation_acceptance_requires_verified_matching_email(tmp_path) -> None:
    database_path = tmp_path / "invitation-identity-proof.sqlite3"
    connection = connect(database_path)
    try:
        migrate(connection)
        now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        for user_id, email, verified_at in (
            ("unverified", "invitee@example.test", None),
            ("other", "other@example.test", now.isoformat()),
        ):
            connection.execute(
                "INSERT INTO users(id, created_at) VALUES (?, ?)",
                (user_id, now.isoformat()),
            )
            connection.execute(
                """INSERT INTO auth_identities(
                    id, user_id, provider, provider_subject, password_hash,
                    verified_at, created_at
                ) VALUES (?, ?, 'email', ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    user_id,
                    email,
                    "argon2id-test-hash",
                    verified_at,
                    now.isoformat(),
                ),
            )
        event_id = uuid4()
        participant_id = uuid4()
        other_participant_id = uuid4()
        connection.execute(
            """INSERT INTO events(id, owner_id, title, starts_at, created_at)
               VALUES (?, ?, ?, NULL, ?)""",
            (str(event_id), "owner", "Identity proof", now.isoformat()),
        )
        connection.executemany(
            """INSERT INTO event_participants(
                id, event_id, display_name, added_at
            ) VALUES (?, ?, ?, ?)""",
            [
                (str(participant_id), str(event_id), "Invitee", now.isoformat()),
                (str(other_participant_id), str(event_id), "Other", now.isoformat()),
            ],
        )
        connection.commit()

        raw_token = "one-time-invitation-token"
        invitation = ParticipantInvitation.create(
            participant_id=participant_id,
            target_email="Invitee@Example.Test",
            token_hash=hash_token(raw_token),
            created_by_owner_id="owner",
            now=now,
        )
        repository = ParticipantInvitationsRepository(connection)
        assert repository.create_for_owner(
            invitation,
            owner_id="owner",
            event_id=event_id,
            now=now,
        )

        assert not repository.accept_for_verified_user(
            token_hash=hash_token(raw_token), user_id="unverified", now=now
        )
        assert not repository.accept_for_verified_user(
            token_hash=hash_token(raw_token), user_id="other", now=now
        )
        other_token = "verified-other-invitation-token"
        other_invitation = ParticipantInvitation.create(
            participant_id=other_participant_id,
            target_email="Other@Example.Test",
            token_hash=hash_token(other_token),
            created_by_owner_id="owner",
            now=now,
        )
        assert repository.create_for_owner(
            other_invitation,
            owner_id="owner",
            event_id=event_id,
            now=now,
        )
        assert repository.accept_for_verified_user(
            token_hash=hash_token(other_token), user_id="other", now=now
        )
        linked = connection.execute(
            "SELECT linked_user_id FROM event_participants WHERE id = ?",
            (str(other_participant_id),),
        ).fetchone()
        assert linked["linked_user_id"] == "other"
        unverified_participant = connection.execute(
            "SELECT linked_user_id FROM event_participants WHERE id = ?",
            (str(participant_id),),
        ).fetchone()
        assert unverified_participant["linked_user_id"] is None
    finally:
        connection.close()


def test_concurrent_invitation_replay_links_participant_exactly_once(tmp_path) -> None:
    database_path = tmp_path / "invitation-concurrent-acceptance.sqlite3"
    connection = connect(database_path)
    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    event_id = uuid4()
    participant_id = uuid4()
    raw_token = "concurrent-one-time-invitation"
    try:
        migrate(connection)
        connection.execute(
            "INSERT INTO users(id, created_at) VALUES (?, ?)",
            ("invitee", now.isoformat()),
        )
        connection.execute(
            """INSERT INTO auth_identities(
                id, user_id, provider, provider_subject, password_hash,
                verified_at, created_at
            ) VALUES (?, 'invitee', 'email', ?, ?, ?, ?)""",
            (
                str(uuid4()),
                "invitee@example.test",
                "argon2id-test-hash",
                now.isoformat(),
                now.isoformat(),
            ),
        )
        connection.execute(
            """INSERT INTO events(id, owner_id, title, starts_at, created_at)
               VALUES (?, ?, ?, NULL, ?)""",
            (str(event_id), "owner", "Concurrent acceptance", now.isoformat()),
        )
        connection.execute(
            """INSERT INTO event_participants(
                id, event_id, display_name, added_at
            ) VALUES (?, ?, ?, ?)""",
            (str(participant_id), str(event_id), "Invitee", now.isoformat()),
        )
        connection.commit()
        invitation = ParticipantInvitation.create(
            participant_id=participant_id,
            target_email="invitee@example.test",
            token_hash=hash_token(raw_token),
            created_by_owner_id="owner",
            now=now,
        )
        assert ParticipantInvitationsRepository(connection).create_for_owner(
            invitation,
            owner_id="owner",
            event_id=event_id,
            now=now,
        )
    finally:
        connection.close()

    barrier = Barrier(2)

    def accept() -> bool:
        worker_connection = connect(database_path)
        try:
            barrier.wait(timeout=5)
            return ParticipantInvitationsRepository(
                worker_connection
            ).accept_for_verified_user(
                token_hash=hash_token(raw_token),
                user_id="invitee",
                now=now + timedelta(seconds=1),
            )
        finally:
            worker_connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: accept(), range(2)))

    assert sorted(results) == [False, True]
    verification = connect(database_path)
    try:
        invitation_row = verification.execute(
            "SELECT accepted_at FROM participant_invitations WHERE id = ?",
            (str(invitation.id),),
        ).fetchone()
        participant_row = verification.execute(
            "SELECT linked_user_id FROM event_participants WHERE id = ?",
            (str(participant_id),),
        ).fetchone()
        assert invitation_row["accepted_at"] is not None
        assert participant_row["linked_user_id"] == "invitee"
    finally:
        verification.close()
