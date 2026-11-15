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
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert response.status_code == 202
    token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
    assert client.post(
        "/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    ).status_code == 200


def test_event_tools_are_isolated_to_event_members_and_owners(
    tmp_path, monkeypatch
) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "event-tools-access.sqlite3")

    with (
        TestClient(app, headers={"Origin": "http://testserver"}) as owner,
        TestClient(app, headers={"Origin": "http://testserver"}) as other,
    ):
        _register_and_sign_in(owner, sender, "tools-owner@example.test")
        _register_and_sign_in(other, sender, "tools-other@example.test")

        event = owner.post("/events", json={"title": "Private planning"})
        assert event.status_code == 201
        event_id = event.json()["id"]
        participant = owner.post(
            f"/events/{event_id}/participants",
            json={"display_name": "Guest"},
        )
        assert participant.status_code == 201
        participant_id = participant.json()["id"]

        venue_payload = {
            "venue_name": "Workshop",
            "address": "Main Street 12",
            "latitude": 60.17,
            "longitude": 24.94,
        }
        assert owner.put(
            f"/events/{event_id}/venue", json=venue_payload
        ).status_code == 200
        task_payload = {
            "title": "Bring materials",
            "description": "Wood and tools",
            "assignee_participant_id": participant_id,
        }
        task = owner.post(f"/events/{event_id}/tasks", json=task_payload)
        assert task.status_code == 201
        task_id = task.json()["id"]
        note_payload = {"content": "Private event plan", "visibility": "shared"}
        note = owner.post(f"/events/{event_id}/notes", json=note_payload)
        assert note.status_code == 201
        note_id = note.json()["id"]

        assert owner.get(f"/events/{event_id}/venue").json()["venue_name"] == "Workshop"
        assert owner.get(f"/events/{event_id}/tasks").json()[0]["id"] == task_id
        assert owner.get(f"/events/{event_id}/notes").json()[0]["id"] == note_id

        assert other.get(f"/events/{event_id}/venue").status_code == 404
        assert other.get(f"/events/{event_id}/tasks").status_code == 404
        assert other.get(f"/events/{event_id}/notes").status_code == 404
        assert other.put(
            f"/events/{event_id}/venue", json=venue_payload
        ).status_code == 404
        assert other.delete(f"/events/{event_id}/venue").status_code == 404
        assert other.post(
            f"/events/{event_id}/tasks", json=task_payload
        ).status_code == 404
        assert other.put(
            f"/events/{event_id}/tasks/{task_id}", json=task_payload
        ).status_code == 404
        assert other.patch(
            f"/events/{event_id}/tasks/{task_id}/completion",
            json={"completed": True},
        ).status_code == 404
        assert other.post(
            f"/events/{event_id}/notes", json=note_payload
        ).status_code == 404
        assert other.put(
            f"/events/{event_id}/notes/{note_id}",
            json={"content": "Tampered", "visibility": "shared"},
        ).status_code == 404
        assert other.delete(f"/events/{event_id}/notes/{note_id}").status_code == 404

        assert owner.get(f"/events/{event_id}/venue").json()["venue_name"] == "Workshop"
        assert owner.get(f"/events/{event_id}/tasks").json()[0]["title"] == "Bring materials"
        assert owner.get(f"/events/{event_id}/notes").json()[0]["content"] == "Private event plan"
