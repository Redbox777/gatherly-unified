from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.interfaces.web.http_api import create_app


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_text(self, *, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


def _register_and_sign_in(
    client: TestClient, sender: FakeEmailSender, email: str
) -> None:
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


def test_linked_participant_can_read_venue_but_only_owner_can_write(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "shared-event-venue.sqlite3")

    with (
        TestClient(app, headers={"Origin": "http://testserver"}) as owner,
        TestClient(app, headers={"Origin": "http://testserver"}) as participant,
    ):
        _register_and_sign_in(owner, sender, "owner@example.test")
        event = owner.post("/events", json={"title": "Planning session"}).json()
        event_id = event["id"]
        guest = owner.post(
            f"/events/{event_id}/participants", json={"display_name": "Member"}
        ).json()
        invitation = owner.post(
            f"/events/{event_id}/participants/{guest['id']}/invitation",
            json={"email": "member@example.test"},
        )
        assert invitation.status_code == 201
        invitation_token = (
            sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
        )

        venue = {
            "venue_name": "City Park",
            "address": "Main Street 1",
            "latitude": 50.1,
            "longitude": 6.1,
        }
        assert owner.put(f"/events/{event_id}/venue", json=venue).status_code == 200

        _register_and_sign_in(participant, sender, "member@example.test")
        accepted = participant.post(
            "/participant-invitations/accept", json={"token": invitation_token}
        )
        assert accepted.status_code == 200

        visible = participant.get(f"/events/{event_id}/venue")
        assert visible.status_code == 200
        assert visible.json() == venue

        denied_update = participant.put(
            f"/events/{event_id}/venue",
            json={**venue, "venue_name": "Changed by participant"},
        )
        denied_delete = participant.delete(f"/events/{event_id}/venue")
        assert denied_update.status_code == 404
        assert denied_delete.status_code == 404

        owner_view = owner.get(f"/events/{event_id}/venue")
        assert owner_view.status_code == 200
        assert owner_view.json() == venue
