import pytest

from app.infrastructure.email.smtp_email_sender import SmtpEmailSender


def test_smtp_authentication_requires_starttls() -> None:
    with pytest.raises(ValueError, match="SMTP authentication requires STARTTLS"):
        SmtpEmailSender(
            host="smtp.example.com",
            port=25,
            username="user",
            password="password",
            sender="sender@example.com",
            use_starttls=False,
        )


def test_smtp_without_authentication_can_disable_starttls() -> None:
    sender = SmtpEmailSender(
        host="localhost",
        port=25,
        username="",
        password="",
        sender="sender@example.com",
        use_starttls=False,
    )

    assert sender is not None
