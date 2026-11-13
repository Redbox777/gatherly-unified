import pytest

from app.interfaces.web.runtime_config import (
    load_smtp_rate_limit_settings,
    validate_runtime_configuration,
)


def production_settings() -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "COOKIE_SECURE": "true",
        "SMTP_STARTTLS": "true",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_TIMEOUT": "10",
        "SMTP_SENDER": "no-reply@example.com",
        "EMAIL_VERIFICATION_URL": "https://gatherly.example/auth/verify-email",
        "PASSWORD_RESET_URL": "https://gatherly.example/reset-password",
        "PARTICIPANT_INVITATION_URL": "https://gatherly.example/accept-invitation",
    }


def test_development_environment_keeps_local_defaults_available() -> None:
    validate_runtime_configuration({"APP_ENV": "development"})


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValueError, match="APP_ENV"):
        validate_runtime_configuration({"APP_ENV": "staging-ish"})


def test_smtp_rate_limit_defaults_preserve_current_policy() -> None:
    settings = load_smtp_rate_limit_settings({})
    assert settings.recipient_limit == 3
    assert settings.recipient_window_seconds == 900
    assert settings.requester_limit == 5
    assert settings.requester_window_seconds == 60


def test_smtp_rate_limits_can_be_overridden() -> None:
    settings = load_smtp_rate_limit_settings(
        {
            "SMTP_RATE_LIMIT_RECIPIENT_MAX": "8",
            "SMTP_RATE_LIMIT_RECIPIENT_WINDOW_SECONDS": "1200",
            "SMTP_RATE_LIMIT_REQUESTER_MAX": "12",
            "SMTP_RATE_LIMIT_REQUESTER_WINDOW_SECONDS": "90",
        }
    )
    assert settings.recipient_limit == 8
    assert settings.recipient_window_seconds == 1200
    assert settings.requester_limit == 12
    assert settings.requester_window_seconds == 90


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("SMTP_RATE_LIMIT_RECIPIENT_MAX", "0"),
        ("SMTP_RATE_LIMIT_RECIPIENT_WINDOW_SECONDS", "86401"),
        ("SMTP_RATE_LIMIT_REQUESTER_MAX", "not-a-number"),
        ("SMTP_RATE_LIMIT_REQUESTER_WINDOW_SECONDS", "-1"),
    ],
)
def test_invalid_smtp_rate_limit_configuration_is_rejected(
    setting: str, value: str
) -> None:
    with pytest.raises(ValueError, match=setting):
        validate_runtime_configuration({setting: value})


def test_production_rejects_insecure_session_cookie() -> None:
    settings = production_settings()
    settings["COOKIE_SECURE"] = "false"

    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        validate_runtime_configuration(settings)


def test_production_requires_starttls() -> None:
    settings = production_settings()
    settings["SMTP_STARTTLS"] = "false"

    with pytest.raises(ValueError, match="SMTP_STARTTLS"):
        validate_runtime_configuration(settings)


@pytest.mark.parametrize(
    "setting",
    [
        "EMAIL_VERIFICATION_URL",
        "PASSWORD_RESET_URL",
        "PARTICIPANT_INVITATION_URL",
    ],
)
def test_production_requires_https_public_urls(setting: str) -> None:
    settings = production_settings()
    settings[setting] = "http://gatherly.example/path"

    with pytest.raises(ValueError, match=setting):
        validate_runtime_configuration(settings)


def test_production_rejects_local_mail_server_and_sender() -> None:
    settings = production_settings()
    settings["SMTP_HOST"] = "localhost"

    with pytest.raises(ValueError, match="SMTP_HOST"):
        validate_runtime_configuration(settings)

    settings = production_settings()
    settings["SMTP_SENDER"] = "no-reply@gatherly.local"

    with pytest.raises(ValueError, match="SMTP_SENDER"):
        validate_runtime_configuration(settings)


def test_complete_production_configuration_is_accepted() -> None:
    validate_runtime_configuration(production_settings())


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("SMTP_PORT", "not-a-number"),
        ("SMTP_PORT", "0"),
        ("SMTP_PORT", "65536"),
        ("SMTP_TIMEOUT", "not-a-number"),
        ("SMTP_TIMEOUT", "0"),
        ("SMTP_TIMEOUT", "-1"),
        ("SMTP_TIMEOUT", "nan"),
        ("SMTP_TIMEOUT", "inf"),
        ("SMTP_TIMEOUT", "-inf"),
        ("SMTP_TIMEOUT", "301"),
    ],
)
def test_invalid_smtp_transport_settings_are_rejected(
    setting: str, value: str
) -> None:
    with pytest.raises(ValueError, match=setting):
        validate_runtime_configuration({setting: value})


@pytest.mark.parametrize("value", ["yes", "no", "1", "0", "treu", ""])
def test_smtp_starttls_requires_an_explicit_boolean_value(value: str) -> None:
    with pytest.raises(ValueError, match="SMTP_STARTTLS"):
        validate_runtime_configuration({"SMTP_STARTTLS": value})


def test_smtp_authentication_requires_both_credentials() -> None:
    with pytest.raises(ValueError, match="SMTP_USERNAME and SMTP_PASSWORD"):
        validate_runtime_configuration({"SMTP_USERNAME": "mailer"})

    with pytest.raises(ValueError, match="SMTP_USERNAME and SMTP_PASSWORD"):
        validate_runtime_configuration({"SMTP_PASSWORD": "secret"})


def test_smtp_authentication_requires_starttls_in_all_environments() -> None:
    with pytest.raises(ValueError, match="SMTP authentication requires"):
        validate_runtime_configuration(
            {
                "SMTP_USERNAME": "mailer",
                "SMTP_PASSWORD": "secret",
                "SMTP_STARTTLS": "false",
            }
        )


def test_smtp_starttls_can_be_disabled_without_credentials_in_development() -> None:
    validate_runtime_configuration({"APP_ENV": "development", "SMTP_STARTTLS": "false"})
