"""Verify legacy authenticated HTTP mutations advance the event revision."""

from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.infrastructure.database.connection import connect
from app.interfaces.web.http_api import create_app


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_text(self, *, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


def _register_and_sign_in(client: TestClient, sender: FakeEmailSender) -> None:
    response = client.post(
        "/auth/register",
        json={"email": "revision-owner@example.test",
              "password": "correct horse battery staple"},
    )
    assert response.status_code == 202
    token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
    assert client.post(
        "/auth/login",
        json={"email": "revision-owner@example.test",
              "password": "correct horse battery staple"},
    ).status_code == 200


def test_existing_online_mutations_all_advance_event_revision(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    database_path = tmp_path / "online-revisions.sqlite3"
    app = create_app(database_path)
    connection = connect(database_path)

    def revision(event_id: str) -> int:
        row = connection.execute(
            "SELECT revision FROM event_sync_state WHERE event_id = ?", (event_id,)
        ).fetchone()
        assert row is not None
        return int(row["revision"])

    try:
        with TestClient(app, headers={"Origin": "http://testserver"}) as client:
            _register_and_sign_in(client, sender)
            event = client.post("/events", json={"title": "Revision audit"})
            assert event.status_code == 201
            event_id = event.json()["id"]
            assert revision(event_id) == 1

            participant = client.post(
                f"/events/{event_id}/participants", json={"display_name": "Guest"}
            )
            assert participant.status_code == 201
            participant_id = participant.json()["id"]
            assert revision(event_id) == 2

            venue_payload = {"venue_name": "Workshop", "address": "Main Street"}
            assert client.put(
                f"/events/{event_id}/venue", json=venue_payload
            ).status_code == 200
            assert revision(event_id) == 3

            task = client.post(
                f"/events/{event_id}/tasks",
                json={"title": "Prepare", "description": ""},
            )
            assert task.status_code == 201
            task_id = task.json()["id"]
            assert revision(event_id) == 4

            note = client.post(
                f"/events/{event_id}/notes",
                json={"content": "Plan", "visibility": "shared"},
            )
            assert note.status_code == 201
            note_id = note.json()["id"]
            assert revision(event_id) == 5

            assert client.put(
                f"/events/{event_id}/notes/{note_id}",
                json={"content": "Updated plan", "visibility": "shared"},
            ).status_code == 200
            assert revision(event_id) == 6

            assert client.put(
                f"/events/{event_id}/tasks/{task_id}",
                json={"title": "Prepare materials", "description": ""},
            ).status_code == 200
            assert revision(event_id) == 7

            assert client.patch(
                f"/events/{event_id}/tasks/{task_id}/completion",
                json={"completed": True},
            ).status_code == 200
            assert revision(event_id) == 8

            assert client.put(
                f"/events/{event_id}/venue",
                json={"venue_name": "New workshop", "address": "Second Street"},
            ).status_code == 200
            assert revision(event_id) == 9

            assert client.delete(f"/events/{event_id}/venue").status_code == 204
            assert revision(event_id) == 10

            assert client.delete(f"/events/{event_id}/tasks/{task_id}").status_code == 204
            assert revision(event_id) == 11

            assert client.delete(f"/events/{event_id}/notes/{note_id}").status_code == 204
            assert revision(event_id) == 12

            assert client.delete(
                f"/events/{event_id}/participants/{participant_id}"
            ).status_code == 204
            assert revision(event_id) == 13

            assert client.patch(
                f"/events/{event_id}", json={"title": "Final title"}
            ).status_code == 200
            assert revision(event_id) == 14
    finally:
        connection.close()
