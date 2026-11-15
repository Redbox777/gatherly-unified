from fastapi.testclient import TestClient

from app.interfaces.web.http_api import create_app


def test_frontend_homepage_is_served_with_security_headers(tmp_path) -> None:
    app = create_app(tmp_path / "frontend.sqlite3")
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'lang="ru"' in response.text
    assert "Рады видеть вас" in response.text
    assert 'rel="manifest"' in response.text
    assert 'id="balances-list"' in response.text
    assert 'id="settlement-form"' in response.text
    assert 'id="meeting-grid"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_frontend_assets_are_served_by_fastapi(tmp_path) -> None:
    app = create_app(tmp_path / "frontend-assets.sqlite3")
    with TestClient(app) as client:
        homepage = client.get("/")
        stylesheet = client.get("/styles.css")
        script = client.get("/app.js")
        account_linking = client.get("/account-linking-ui.js")
        notifications = client.get("/notifications-ui.js")
        finance = client.get("/finance-ui.js")
        mark = client.get("/magpie.svg")
        manifest = client.get("/manifest.webmanifest")
        service_worker = client.get("/service-worker.js")
        pwa_script = client.get("/pwa.js")
        offline = client.get("/offline.html")
        pwa_icon = client.get("/icon.svg")

    assert stylesheet.status_code == 200
    assert "grid-template-columns:1fr" in stylesheet.text
    assert ".finance-panel" in stylesheet.text
    assert script.status_code == 200
    assert account_linking.status_code == 200
    assert "GatherlyAccountLink" in account_linking.text
    assert 'id="account-settings-open"' in homepage.text
    assert notifications.status_code == 200
    assert finance.status_code == 200
    assert "GatherlyFinanceUI" in finance.text
    assert "GatherlyNotificationsUI" in notifications.text
    assert "aria-expanded" in script.text
    assert "const requestedEventId = event.id;" in script.text
    assert "activeEvent?.id !== requestedEventId" in script.text
    assert "activeEvent?.id !== eventId" in script.text
    assert "loadEventFinance" in finance.text
    assert "settlements" in finance.text
    assert "currentFinanceEventId = null;" in finance.text
    assert "getActiveEvent()?.id !== eventId" in finance.text
    assert "await loadEventFinance(eventId);" in finance.text
    assert "renderExpenseSplitEditor" in finance.text
    assert "Распределить" in finance.text
    assert "weighted" in finance.text
    assert ".split-participant" in stylesheet.text
    assert mark.status_code == 200
    assert "<svg" in mark.text
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["display"] == "standalone"
    assert manifest.json()["start_url"] == "/"
    assert service_worker.status_code == 200
    assert "gatherly-shell-v1" in service_worker.text
    assert "application/json" in service_worker.text
    assert 'addEventListener("push"' in service_worker.text
    assert 'addEventListener("notificationclick"' in service_worker.text
    assert pwa_script.status_code == 200
    assert "serviceWorker.register" in pwa_script.text
    assert offline.status_code == 200
    assert "Нет подключения" in offline.text
    assert "onclick=" not in offline.text
    assert pwa_icon.status_code == 200
    assert "<svg" in pwa_icon.text
