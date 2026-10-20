from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.interfaces.web.http_api import create_app


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

    with TestClient(app) as client:
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

        participant_row = app.state.auth_dependencies.connection.execute(
            "SELECT linked_user_id FROM event_participants WHERE id = ?",
            (participant_id,),
        ).fetchone()
        assert participant_row["linked_user_id"] is not None


def test_delivery_failure_revokes_new_invitation(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "participant-invitation-delivery-failure.sqlite3")

    with TestClient(app) as client:
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
        row = app.state.auth_dependencies.connection.execute(
            "SELECT accepted_at, revoked_at FROM participant_invitations"
        ).fetchone()
        assert row["accepted_at"] is None
        assert row["revoked_at"] is not None



def test_invitation_acceptance_page_is_served_without_reflecting_token(tmp_path) -> None:
    app = create_app(tmp_path / "participant-invitation-page.sqlite3")
    with TestClient(app) as client:
        response = client.get("/accept-invitation?token=example-secret-token")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "example-secret-token" not in response.text
    assert "history.replaceState" in response.text
    assert 'fetch("/participant-invitations/accept"' in response.text
    assert "fetch(\"/auth/login\"" in response.text
