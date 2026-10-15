from datetime import UTC, datetime, timedelta
import hashlib
import hmac

import pytest

from app.interfaces.telegram.login_verification import verify_telegram_login


NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
BOT_TOKEN = "123456:example-test-token"


def signed_payload(*, auth_date: int) -> dict[str, str]:
    payload = {
        "id": "123456789",
        "first_name": "Example",
        "auth_date": str(auth_date),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(payload.items()))
    secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
    payload["hash"] = hmac.new(
        secret_key, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return payload


def test_accepts_valid_recent_telegram_login_payload() -> None:
    payload = signed_payload(auth_date=int(NOW.timestamp()))

    assert verify_telegram_login(payload=payload, bot_token=BOT_TOKEN, now=NOW)


def test_rejects_payload_when_signed_fields_are_changed() -> None:
    payload = signed_payload(auth_date=int(NOW.timestamp()))
    payload["first_name"] = "Forged"

    assert not verify_telegram_login(payload=payload, bot_token=BOT_TOKEN, now=NOW)


def test_rejects_missing_or_malformed_signature() -> None:
    payload = signed_payload(auth_date=int(NOW.timestamp()))
    payload.pop("hash")

    assert not verify_telegram_login(payload=payload, bot_token=BOT_TOKEN, now=NOW)

    payload["hash"] = "not-a-signature"
    assert not verify_telegram_login(payload=payload, bot_token=BOT_TOKEN, now=NOW)


@pytest.mark.parametrize("age", [timedelta(minutes=6), timedelta(seconds=-1)])
def test_rejects_expired_or_future_auth_date(age: timedelta) -> None:
    payload = signed_payload(auth_date=int((NOW - age).timestamp()))

    assert not verify_telegram_login(payload=payload, bot_token=BOT_TOKEN, now=NOW)


def test_rejects_empty_bot_token_and_nonpositive_age_window() -> None:
    payload = signed_payload(auth_date=int(NOW.timestamp()))

    assert not verify_telegram_login(payload=payload, bot_token=" ", now=NOW)
    assert not verify_telegram_login(
        payload=payload, bot_token=BOT_TOKEN, now=NOW, max_age=timedelta(0)
    )


def test_requires_timezone_aware_current_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        verify_telegram_login(
            payload=signed_payload(auth_date=int(NOW.timestamp())),
            bot_token=BOT_TOKEN,
            now=datetime(2026, 10, 15, 12, 0),
        )
