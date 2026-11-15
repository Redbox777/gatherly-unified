"""HTTP authentication and replay behavior for the offline command endpoint."""

from uuid import uuid4

from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.interfaces.web.http_api import create_app


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_text(self, *, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))


def _register_and_sign_in(client: TestClient, sender: FakeEmailSender) -> None:
    response = client.post(
        "/auth/register",
        json={"email": "offline-owner@example.test",
              "password": "correct horse battery staple"},
    )
    assert response.status_code == 202
    token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
    assert client.post(
        "/auth/login",
        json={"email": "offline-owner@example.test",
              "password": "correct horse battery staple"},
    ).status_code == 200


def test_offline_sync_endpoint_uses_session_and_replays_same_command(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "offline-sync-api.sqlite3")

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        unauthenticated = client.post(
            "/offline-sync/commands",
            json={
                "operation_id": str(uuid4()),
                "event_id": str(uuid4()),
                "base_revision": 1,
                "kind": "note.create",
                "payload": {"content": "No session"},
            },
        )
        assert unauthenticated.status_code == 401

        _register_and_sign_in(client, sender)
        event = client.post("/events", json={"title": "Offline test"})
        assert event.status_code == 201
        event_id = event.json()["id"]
        sync_state = client.get(f"/events/{event_id}/sync-state")
        assert sync_state.status_code == 200
        assert sync_state.json() == {"event_id": event_id, "revision": 1}
        operation_id = str(uuid4())
        command = {
            "operation_id": operation_id,
            "event_id": event_id,
            "base_revision": 1,
            "kind": "note.create",
            "payload": {"content": "Queued note", "visibility": "shared"},
        }

        first = client.post("/offline-sync/commands", json=command)
        replay = client.post("/offline-sync/commands", json=command)
        assert first.status_code == 201
        assert replay.status_code == 201
        assert replay.json() == first.json()
        assert len(client.get(f"/events/{event_id}/notes").json()) == 1

        changed = {
            **command,
            "payload": {"content": "Different command", "visibility": "shared"},
        }
        assert client.post("/offline-sync/commands", json=changed).status_code == 409

        stale = {**command, "operation_id": str(uuid4())}
        conflict = client.post("/offline-sync/commands", json=stale)
        assert conflict.status_code == 409
        assert conflict.json()["current_revision"] == 2

        forged_identity = {**command, "operation_id": str(uuid4()), "user_id": "attacker"}
        assert client.post("/offline-sync/commands", json=forged_identity).status_code == 422
