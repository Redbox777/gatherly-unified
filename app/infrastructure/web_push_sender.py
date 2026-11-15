"""Web Push adapter using VAPID and the pywebpush implementation."""

import json

from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]


class WebPushDeliveryError(RuntimeError):
    """Safe delivery failure exposing only an optional HTTP status code."""

    def __init__(self, status_code: int | None) -> None:
        super().__init__("Web Push delivery failed")
        self.status_code = status_code


class WebPushSender:
    """Send authenticated encrypted browser push messages."""

    def __init__(
        self,
        *,
        vapid_private_key: str,
        vapid_subject: str,
        timeout: float = 10.0,
    ) -> None:
        if not vapid_private_key.strip():
            raise ValueError("VAPID private key must not be blank")
        if not (
            vapid_subject.startswith("mailto:")
            or vapid_subject.startswith("https://")
        ):
            raise ValueError("VAPID subject must be a mailto: or HTTPS URL")
        if timeout <= 0:
            raise ValueError("Web Push timeout must be positive")
        self._private_key = vapid_private_key
        self._subject = vapid_subject
        self._timeout = timeout

    def send(self, *, subscription_info: dict[str, object], payload: dict[str, str]) -> None:
        """Send one JSON notification without exposing endpoint or VAPID details."""
        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._subject},
                ttl=3600,
                timeout=self._timeout,
            )
        except WebPushException as error:
            response = getattr(error, "response", None)
            status_code = getattr(response, "status_code", None)
            raise WebPushDeliveryError(
                status_code if isinstance(status_code, int) else None
            ) from None
