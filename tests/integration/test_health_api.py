import sqlite3

from fastapi.testclient import TestClient

from app.interfaces.web import http_api


def test_health_liveness_and_readiness_are_separate(tmp_path) -> None:
    app = http_api.create_app(tmp_path / "health.sqlite3")
    with TestClient(app) as client:
        live = client.get("/health/live")
        legacy = client.get("/health")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "alive"}
    assert legacy.status_code == 200
    assert legacy.json() == {"status": "alive"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_readiness_returns_generic_503_when_database_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    app = http_api.create_app(tmp_path / "health-unavailable.sqlite3")

    def unavailable(_database_path: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("private database path must not leak")

    with TestClient(app) as client:
        monkeypatch.setattr(http_api, "connect", unavailable)
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Service not ready"}
    assert "private database path" not in response.text
