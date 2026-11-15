"""Minimal Telegram Bot API text sender with secret-safe errors."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramBotSender:
    """Send one text message using the Bot API without logging bot credentials."""

    def __init__(self, *, bot_token: str, timeout: float = 10.0) -> None:
        if not bot_token.strip():
            raise ValueError("Telegram bot token must not be blank")
        if timeout <= 0:
            raise ValueError("Telegram timeout must be positive")
        self._endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._timeout = timeout

    def send_text(self, *, chat_id: str, text: str) -> None:
        """Send a plain-text message or raise a generic error without response secrets."""
        if not chat_id.strip() or not text.strip():
            raise ValueError("Telegram chat ID and message must not be blank")
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": text[:4096],
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        request = Request(
            self._endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise RuntimeError("Telegram delivery failed")
                result = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            raise RuntimeError("Telegram delivery failed") from error
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("Telegram rejected the message")
