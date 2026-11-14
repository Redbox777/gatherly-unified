import logging
import re

from fastapi import Request
from fastapi.testclient import TestClient

from app.interfaces.web.http_api import create_app


def test_each_response_includes_a_unique_server_generated_request_id(tmp_path) -> None:
    app = create_app(tmp_path / "request-id.sqlite3")

    with TestClient(app) as client:
        first = client.get("/health", headers={"X-Request-ID": "caller-controlled"})
        second = client.get("/health")

    first_id = first.headers["X-Request-ID"]
    second_id = second.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", first_id)
    assert re.fullmatch(r"[0-9a-f]{32}", second_id)
    assert first_id != second_id
    assert first_id != "caller-controlled"


def test_unhandled_exception_returns_safe_response_and_logs_request_id(
    tmp_path, caplog
) -> None:
    app = create_app(tmp_path / "safe-errors.sqlite3")

    @app.post("/test/unhandled")
    def unhandled(_request: Request) -> None:
        raise RuntimeError("private-payload-must-never-appear-in-logs")

    caplog.set_level(logging.INFO)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/test/unhandled?token=private-query-token",
            json={"password": "private-body-password"},
            headers={"Origin": "http://testserver"},
        )

    request_id = response.headers["X-Request-ID"]
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal server error",
        "request_id": request_id,
    }
    assert "private-payload-must-never-appear-in-logs" not in caplog.text
    assert "private-query-token" not in caplog.text
    assert "private-body-password" not in caplog.text

    failure_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http.request.exception"
    ]
    assert len(failure_records) == 1
    assert failure_records[0].request_id == request_id
    assert failure_records[0].exception_type == "RuntimeError"
    assert failure_records[0].exception_frames
