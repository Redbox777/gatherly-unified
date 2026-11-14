"""Fail-fast validation for deployment-sensitive runtime configuration."""

from collections.abc import Mapping
from dataclasses import dataclass
import math
from urllib.parse import urlsplit


_ALLOWED_ENVIRONMENTS = {"development", "test", "production"}
_PUBLIC_URL_SETTINGS = (
    "EMAIL_VERIFICATION_URL",
    "PASSWORD_RESET_URL",
    "PARTICIPANT_INVITATION_URL",
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_MAX_RATE_LIMIT = 1_000_000
_MAX_RATE_WINDOW_SECONDS = 86_400


@dataclass(frozen=True)
class SmtpRateLimitSettings:
    """Validated shared quotas for outbound email attempts."""

    recipient_limit: int = 3
    recipient_window_seconds: int = 900
    requester_limit: int = 5
    requester_window_seconds: int = 60


def load_smtp_rate_limit_settings(
    environ: Mapping[str, str],
) -> SmtpRateLimitSettings:
    """Load positive, bounded SMTP quotas from environment variables."""
    setting_names = {
        "recipient_limit": "SMTP_RATE_LIMIT_RECIPIENT_MAX",
        "recipient_window_seconds": "SMTP_RATE_LIMIT_RECIPIENT_WINDOW_SECONDS",
        "requester_limit": "SMTP_RATE_LIMIT_REQUESTER_MAX",
        "requester_window_seconds": "SMTP_RATE_LIMIT_REQUESTER_WINDOW_SECONDS",
    }
    defaults = SmtpRateLimitSettings()
    values: dict[str, int] = {}
    for field, setting in setting_names.items():
        default = getattr(defaults, field)
        try:
            value = int(environ.get(setting, str(default)))
        except ValueError as error:
            raise ValueError(f"{setting} must be a positive integer") from error
        maximum = (
            _MAX_RATE_WINDOW_SECONDS
            if field.endswith("_window_seconds")
            else _MAX_RATE_LIMIT
        )
        if not 1 <= value <= maximum:
            raise ValueError(f"{setting} must be between 1 and {maximum}")
        values[field] = value
    return SmtpRateLimitSettings(**values)


def validate_runtime_configuration(environ: Mapping[str, str]) -> None:
    """Reject unsafe or incomplete settings before a production app starts."""
    load_smtp_rate_limit_settings(environ)

    try:
        smtp_port = int(environ.get("SMTP_PORT", "587"))
    except ValueError as error:
        raise ValueError("SMTP_PORT must be an integer") from error
    if not 1 <= smtp_port <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65535")

    try:
        smtp_timeout = float(environ.get("SMTP_TIMEOUT", "10"))
    except ValueError as error:
        raise ValueError("SMTP_TIMEOUT must be a finite number") from error
    if not math.isfinite(smtp_timeout) or not 0 < smtp_timeout <= 300:
        raise ValueError("SMTP_TIMEOUT must be greater than 0 and at most 300 seconds")

    smtp_starttls_value = environ.get("SMTP_STARTTLS", "true").strip().casefold()
    if smtp_starttls_value not in {"true", "false"}:
        raise ValueError("SMTP_STARTTLS must be true or false")
    smtp_starttls = smtp_starttls_value == "true"

    smtp_username = environ.get("SMTP_USERNAME", "")
    smtp_password = environ.get("SMTP_PASSWORD", "")
    if bool(smtp_username) != bool(smtp_password):
        raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be configured together")
    if smtp_username and not smtp_starttls:
        raise ValueError("SMTP authentication requires SMTP_STARTTLS=true")

    environment = environ.get("APP_ENV", "development").strip().casefold()
    if environment not in _ALLOWED_ENVIRONMENTS:
        raise ValueError("APP_ENV must be development, test, or production")

    public_origin = environ.get("PUBLIC_ORIGIN", "").strip()
    if public_origin:
        parsed_origin = urlsplit(public_origin)
        try:
            parsed_origin.port
        except ValueError as error:
            raise ValueError("PUBLIC_ORIGIN must contain a valid port") from error
        if (
            parsed_origin.scheme.casefold() not in {"http", "https"}
            or not parsed_origin.hostname
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise ValueError("PUBLIC_ORIGIN must be an absolute origin without a path")
    if environment != "production":
        return

    if (
        not public_origin
        or urlsplit(public_origin).scheme.casefold() != "https"
        or urlsplit(public_origin).hostname is None
        or urlsplit(public_origin).hostname.casefold() in _LOCAL_HOSTS
    ):
        raise ValueError("PUBLIC_ORIGIN must be a configured public HTTPS origin")

    if environ.get("COOKIE_SECURE", "true").strip().casefold() != "true":
        raise ValueError("COOKIE_SECURE must be true in production")

    if not smtp_starttls:
        raise ValueError("SMTP_STARTTLS must be true in production")

    smtp_host = environ.get("SMTP_HOST", "").strip()
    if not smtp_host or smtp_host.casefold() in _LOCAL_HOSTS:
        raise ValueError("SMTP_HOST must name a configured production mail server")

    smtp_sender = environ.get("SMTP_SENDER", "").strip()
    if not smtp_sender or smtp_sender.casefold().endswith(
        (".local", ".test", "@localhost")
    ):
        raise ValueError("SMTP_SENDER must be a real sender address in production")

    for setting in _PUBLIC_URL_SETTINGS:
        value = environ.get(setting, "").strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.casefold() in _LOCAL_HOSTS
        ):
            raise ValueError(f"{setting} must be an absolute HTTPS URL in production")
