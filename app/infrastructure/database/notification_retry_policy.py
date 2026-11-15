"""Shared bounded exponential backoff policy for outbound notification delivery."""

from datetime import UTC, datetime, timedelta


_RETRY_DELAYS = {
    1: timedelta(minutes=1),
    2: timedelta(minutes=5),
    3: timedelta(minutes=15),
    4: timedelta(hours=1),
}
MAX_DELIVERY_ATTEMPTS = 5


def next_retry_at(*, now: datetime, attempt_number: int) -> datetime | None:
    """Return the next eligible retry time; None means the attempt cap is reached."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Retry timestamps must be timezone-aware")
    if attempt_number < 1:
        raise ValueError("Attempt number must be positive")
    delay = _RETRY_DELAYS.get(attempt_number)
    if delay is None:
        return None
    return now.astimezone(UTC) + delay
