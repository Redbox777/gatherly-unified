from fastapi.testclient import TestClient

from app.interfaces.web.http_api import create_app


def test_health_endpoint_uses_temporary_database(tmp_path) -> None:
    app = create_app(tmp_path / "http-auth.sqlite3")
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_password_reset_request_has_generic_response(tmp_path) -> None:
    app = create_app(tmp_path / "reset-http.sqlite3")
    with TestClient(app) as client:
        first = client.post(
            "/auth/password-reset/request", json={"email": "unknown@example.test"}
        )
        second = client.post(
            "/auth/password-reset/request", json={"email": "missing@example.test"}
        )
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
