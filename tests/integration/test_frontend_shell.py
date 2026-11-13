from fastapi.testclient import TestClient

from app.interfaces.web.http_api import create_app


def test_frontend_homepage_is_served_with_security_headers(tmp_path) -> None:
    app = create_app(tmp_path / "frontend.sqlite3")
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'lang="ru"' in response.text
    assert "Встречи, люди." in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_frontend_assets_are_served_by_fastapi(tmp_path) -> None:
    app = create_app(tmp_path / "frontend-assets.sqlite3")
    with TestClient(app) as client:
        stylesheet = client.get("/styles.css")
        script = client.get("/app.js")
        mark = client.get("/magpie.svg")

    assert stylesheet.status_code == 200
    assert "grid-template-columns:1fr" in stylesheet.text
    assert script.status_code == 200
    assert "aria-expanded" in script.text
    assert mark.status_code == 200
    assert "<svg" in mark.text
