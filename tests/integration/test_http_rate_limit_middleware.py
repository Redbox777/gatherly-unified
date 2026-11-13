from fastapi.testclient import TestClient

from app.interfaces.web.http_api import create_app


def test_auth_rate_limit_survives_app_restart(tmp_path) -> None:
    database_path = tmp_path / "persistent-http-rate-limit.sqlite3"
    payload = {"email": "unknown@example.test", "password": "wrong password"}

    first_app = create_app(database_path)
    with TestClient(first_app) as client:
        responses = [client.post("/auth/login", json=payload) for _ in range(5)]
        blocked = client.post("/auth/login", json=payload)

    assert all(response.status_code == 401 for response in responses)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"

    second_app = create_app(database_path)
    with TestClient(second_app) as client:
        after_restart = client.post("/auth/login", json=payload)

    assert after_restart.status_code == 429
    assert after_restart.headers["retry-after"] == "60"
