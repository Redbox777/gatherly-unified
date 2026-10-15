"""Verify Telegram Login Widget payloads before trusting their identity fields."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import hmac


def verify_telegram_login(
    *,
    payload: Mapping[str, str],
    bot_token: str,
    now: datetime,
    max_age: timedelta = timedelta(minutes=5),
) -> bool:
    """Validate Telegram HMAC signature and freshness; never create accounts here.

    The received Login Widget fields must be passed as strings, including hash.
    """
    if not bot_token.strip() or not payload or max_age <= timedelta(0):
        return False
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    supplied_hash = payload.get("hash", "")
    if len(supplied_hash) != 64:
        return False

    try:
        auth_timestamp = int(payload.get("auth_date", ""))
        auth_time = datetime.fromtimestamp(auth_timestamp, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return False

    current_time = now.astimezone(UTC)
    age = current_time - auth_time
    if age < timedelta(0) or age > max_age:
        return False

    check_string = "\\n".join(
        f"{key}={value}"
        for key, value in sorted(payload.items())
        if key != "hash"
    )
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected_hash = hmac.new(
        secret_key, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_hash, supplied_hash)
