import pytest

from app.interfaces.web.runtime_config import validate_runtime_configuration


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
