import json

import pytest

from app.infrastructure import telegram_bot_sender


class FakeResponse:
    status = 200

    def __init__(self, payload: bytes = b'{"ok":true}') -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_telegram_sender_posts_chat_id_and_plain_text(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(telegram_bot_sender, "urlopen", fake_urlopen)
    sender = telegram_bot_sender.TelegramBotSender(
        bot_token="secret-bot-token",
        timeout=7,
    )

    sender.send_text(chat_id="123456", text="Gatherly reminder")

    assert captured["url"] == (
        "https://api.telegram.org/botsecret-bot-token/sendMessage"
    )
    assert captured["body"]["chat_id"] == "123456"
    assert captured["body"]["text"] == "Gatherly reminder"
    assert captured["timeout"] == 7


def test_telegram_sender_rejects_provider_failure_without_exposing_token(
    monkeypatch,
) -> None:
    def fake_urlopen(_request, _timeout):
        return FakeResponse(b'{"ok":false,"description":"private provider response"}')

    monkeypatch.setattr(telegram_bot_sender, "urlopen", fake_urlopen)
    sender = telegram_bot_sender.TelegramBotSender(bot_token="secret-bot-token")

    with pytest.raises(RuntimeError, match="rejected") as error:
        sender.send_text(chat_id="123456", text="Reminder")

    assert "secret-bot-token" not in str(error.value)
    assert "private provider response" not in str(error.value)


@pytest.mark.parametrize(
    ("bot_token", "timeout"),
    [("", 10), ("token", 0)],
)
def test_telegram_sender_rejects_invalid_configuration(bot_token, timeout) -> None:
    with pytest.raises(ValueError):
        telegram_bot_sender.TelegramBotSender(
            bot_token=bot_token,
            timeout=timeout,
        )


def test_telegram_sender_rejects_empty_chat_or_message() -> None:
    sender = telegram_bot_sender.TelegramBotSender(bot_token="test-token")

    with pytest.raises(ValueError):
        sender.send_text(chat_id="", text="Reminder")
    with pytest.raises(ValueError):
        sender.send_text(chat_id="123456", text=" ")
