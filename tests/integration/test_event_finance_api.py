from fastapi.testclient import TestClient

import app.interfaces.web.http_api as http_api
from app.interfaces.web.http_api import create_app


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send_text(self, recipient: str, subject: str, body: str) -> None:
        self.messages.append((recipient, subject, body))




def test_event_balances_and_settlements_api(tmp_path, monkeypatch) -> None:
    sender = FakeEmailSender()
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setattr(http_api, "_email_sender", lambda: sender)
    app = create_app(tmp_path / "event-finance-http.sqlite3")

    with (
        TestClient(app, headers={"Origin": "http://testserver"}) as owner,
        TestClient(app, headers={"Origin": "http://testserver"}) as other,
    ):
        for client, email, password in (
            (owner, "finance-owner@example.test", "owner password long enough"),
            (other, "finance-other@example.test", "other password long enough"),
        ):
            assert client.post(
                "/auth/register", json={"email": email, "password": password}
            ).status_code == 202
            token = sender.messages[-1][2].split("token=", maxsplit=1)[1].splitlines()[0]
            assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
            assert client.post(
                "/auth/login", json={"email": email, "password": password}
            ).status_code == 200

        event_id = owner.post("/events", json={"title": "Finance dinner"}).json()["id"]
        participant_ids = []
        for name in ("Alex", "Boris", "Casey"):
            response = owner.post(
                f"/events/{event_id}/participants", json={"display_name": name}
            )
            assert response.status_code == 201
            participant_ids.append(response.json()["id"])

        expense = owner.post(
            f"/events/{event_id}/expenses",
            json={
                "title": "Dinner",
                "amount": "30.00",
                "currency": "EUR",
                "paid_by_participant_id": participant_ids[0],
            },
        )
        assert expense.status_code == 201
        expense_id = expense.json()["id"]
        split = owner.put(
            f"/events/{event_id}/expenses/{expense_id}/split",
            json={
                "mode": "equal",
                "allocations": [
                    {"participant_id": participant_id}
                    for participant_id in participant_ids
                ],
            },
        )
        assert split.status_code == 200

        balances_url = f"/events/{event_id}/balances"
        settlements_url = f"/events/{event_id}/settlements"
        balances = owner.get(balances_url)
        assert balances.status_code == 200
        initial = {
            item["participant_id"]: item["balance_minor_units"]
            for item in balances.json()["balances"]
        }
        assert initial == {
            participant_ids[0]: 2000,
            participant_ids[1]: -1000,
            participant_ids[2]: -1000,
        }
        assert other.get(balances_url).status_code == 404
        assert other.get(settlements_url).status_code == 404

        settlement = owner.post(
            settlements_url,
            json={
                "from_participant_id": participant_ids[1],
                "to_participant_id": participant_ids[0],
                "amount": "5.00",
                "currency": "EUR",
                "settled_at": "2026-11-14T18:30:00+00:00",
                "note": "Cash",
            },
        )
        assert settlement.status_code == 201
        assert settlement.json()["amount"] == "5.00"
        assert settlement.json()["from_participant_name"] == "Boris"
        assert len(owner.get(settlements_url).json()) == 1

        updated = owner.get(balances_url).json()["balances"]
        by_id = {item["participant_id"]: item["balance_minor_units"] for item in updated}
        assert by_id[participant_ids[0]] == 1500
        assert by_id[participant_ids[1]] == -500

        excessive = owner.post(
            settlements_url,
            json={
                "from_participant_id": participant_ids[1],
                "to_participant_id": participant_ids[0],
                "amount": "6.00",
                "currency": "EUR",
            },
        )
        assert excessive.status_code == 422
        assert len(owner.get(settlements_url).json()) == 1


def test_event_finance_endpoints_require_authentication(tmp_path) -> None:
    app = create_app(tmp_path / "event-finance-auth.sqlite3")
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        event_id = "00000000-0000-0000-0000-000000000001"
        assert client.get(f"/events/{event_id}/balances").status_code == 401
        assert client.get(f"/events/{event_id}/settlements").status_code == 401
        assert client.post(
            f"/events/{event_id}/settlements",
            json={
                "from_participant_id": "00000000-0000-0000-0000-000000000002",
                "to_participant_id": "00000000-0000-0000-0000-000000000003",
                "amount": "1.00",
                "currency": "EUR",
            },
        ).status_code == 401
