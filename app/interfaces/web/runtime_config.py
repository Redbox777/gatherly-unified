"""Fail-fast validation for deployment-sensitive runtime configuration."""

from collections.abc import Mapping
from urllib.parse import urlsplit


_ALLOWED_ENVIRONMENTS = {"development", "test", "production"}
_PUBLIC_URL_SETTINGS = (
    "EMAIL_VERIFICATION_URL",
    "PASSWORD_RESET_URL",
    "PARTICIPANT_INVITATION_URL",
)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def validate_runtime_configuration(environ: Mapping[str, str]) -> None:
    """Reject unsafe or incomplete settings before a production app starts."""
    environment = environ.get("APP_ENV", "development").strip().casefold()
    if environment not in _ALLOWED_ENVIRONMENTS:
        raise ValueError("APP_ENV must be development, test, or production")
    if environment != "production":
        return

    if environ.get("COOKIE_SECURE", "true").strip().casefold() != "true":
        raise ValueError("COOKIE_SECURE must be true in production")

    if environ.get("SMTP_STARTTLS", "true").strip().casefold() != "true":
        raise ValueError("SMTP_STARTTLS must be true in production")

    smtp_host = environ.get("SMTP_HOST", "").strip()
    if not smtp_host or smtp_host.casefold() in _LOCAL_HOSTS:
        raise ValueError("SMTP_HOST must name a configured production mail server")

    smtp_sender = environ.get("SMTP_SENDER", "").strip()
    if not smtp_sender or smtp_sender.casefold().endswith(
        (".local", ".test", "@localhost")
    ):
        raise ValueError("SMTP_SENDER must be a real sender address in production")

    try:
        smtp_port = int(environ.get("SMTP_PORT", "587"))
        smtp_timeout = float(environ.get("SMTP_TIMEOUT", "10"))
    except ValueError as error:
        raise ValueError("SMTP_PORT and SMTP_TIMEOUT must be valid numbers") from error
    if not 1 <= smtp_port <= 65535 or smtp_timeout <= 0:
        raise ValueError("SMTP_PORT or SMTP_TIMEOUT is outside its valid range")

    for setting in _PUBLIC_URL_SETTINGS:
        value = environ.get(setting, "").strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.hostname.casefold() in _LOCAL_HOSTS
        ):
            raise ValueError(f"{setting} must be an absolute HTTPS URL in production")
