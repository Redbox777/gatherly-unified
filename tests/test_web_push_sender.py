import json

import pytest

from app.infrastructure import web_push_sender


def test_web_push_sender_passes_subscription_payload_and_vapid_claims(monkeypatch) -> None:
    captured = {}

    def fake_webpush(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(web_push_sender, "webpush", fake_webpush)
    sender = web_push_sender.WebPushSender(
        vapid_private_key="private-vapid-pem",
        vapid_subject="mailto:ops@example.test",
        timeout=8,
    )
    subscription = {
        "endpoint": "https://push.example.test/send/secret",
        "keys": {"p256dh": "public-key", "auth": "auth-secret"},
    }

    sender.send(
        subscription_info=subscription,
        payload={"title": "Meeting", "body": "Starts soon", "url": "/"},
    )

    assert captured["subscription_info"] == subscription
    assert json.loads(captured["data"])["title"] == "Meeting"
    assert captured["vapid_private_key"] == "private-vapid-pem"
    assert captured["vapid_claims"] == {"sub": "mailto:ops@example.test"}
    assert captured["ttl"] == 3600
    assert captured["timeout"] == 8


@pytest.mark.parametrize(
    ("private_key", "subject", "timeout"),
    [
        ("", "mailto:ops@example.test", 10),
        ("private", "invalid-subject", 10),
        ("private", "mailto:ops@example.test", 0),
    ],
)
def test_web_push_sender_rejects_invalid_configuration(
    private_key, subject, timeout
) -> None:
    with pytest.raises(ValueError):
        web_push_sender.WebPushSender(
            vapid_private_key=private_key,
            vapid_subject=subject,
            timeout=timeout,
        )
