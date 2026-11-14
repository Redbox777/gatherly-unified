from fastapi.testclient import TestClient

from app.infrastructure.database.connection import connect
from app.interfaces.web.http_api import create_app


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "consent": True,
        "app_version": "0.1.0",
        "browser": "Chrome",
        "operating_system": "Linux",
        "module": "frontend",
        "error_code": "window_error",
        "stack": "at line 42, column 8\nprivate/path?token=must-not-be-stored\nat line 58, column 3",
    }
    payload.update(overrides)
    return payload


def test_bug_report_requires_consent_and_stores_only_allowlisted_diagnostics(tmp_path) -> None:
    database_path = tmp_path / "bug-reports.sqlite3"
    app = create_app(database_path)
    with TestClient(app) as client:
        denied = client.post(
            "/diagnostics/bug-reports", json=_payload(consent=False),
            headers={"Origin": "http://testserver"},
        )
        assert denied.status_code == 422
        accepted = client.post(
            "/diagnostics/bug-reports", json=_payload(),
            headers={"Origin": "http://testserver"},
        )
    assert accepted.status_code == 201
    report_id = accepted.json()["report_id"]
    assert len(report_id) == 32

    connection = connect(database_path)
    try:
        row = connection.execute(
            "SELECT * FROM bug_reports WHERE id = ?", (report_id,)
        ).fetchone()
        assert row["browser"] == "Chrome"
        assert row["operating_system"] == "Linux"
        assert row["stack"] == "at line 42, column 8\nat line 58, column 3"
        assert "token" not in row["stack"]
        assert "email" not in row.keys()
        assert "user_id" not in row.keys()
    finally:
        connection.close()


def test_bug_report_rejects_unexpected_personal_fields(tmp_path) -> None:
    app = create_app(tmp_path / "bug-report-extra.sqlite3")
    payload = _payload(email="person@example.com", user_id="private-user")
    with TestClient(app) as client:
        response = client.post(
            "/diagnostics/bug-reports", json=payload,
            headers={"Origin": "http://testserver"},
        )
    assert response.status_code == 422


def test_bug_report_rate_limit_applies_to_public_endpoint(tmp_path) -> None:
    app = create_app(tmp_path / "bug-report-limit.sqlite3")
    with TestClient(app) as client:
        responses = [
            client.post(
                "/diagnostics/bug-reports", json=_payload(),
                headers={"Origin": "http://testserver"},
            )
            for _ in range(6)
        ]
    assert [response.status_code for response in responses] == [201, 201, 201, 201, 201, 429]
