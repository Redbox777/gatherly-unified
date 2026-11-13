"""One-shot notification worker; schedule this command with cron or a platform scheduler."""

from datetime import UTC, datetime
import os

from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.notifications_repository import NotificationsRepository
from app.infrastructure.email.smtp_email_sender import SmtpEmailSender


def run_once(database_path: str | None = None) -> tuple[int, int]:
    """Materialize due reminders and attempt pending email deliveries."""
    path = database_path or os.environ.get(
        "GATHERLY_DATABASE_PATH", "var/gatherly.sqlite3"
    )
    connection = connect(path)
    try:
        migrate(connection)
        repository = NotificationsRepository(connection)
        now = datetime.now(UTC)
        created = repository.materialize_due_reminders(now=now)
        sender = SmtpEmailSender(
            host=os.environ.get("SMTP_HOST", "localhost"),
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=os.environ.get("SMTP_USERNAME", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            sender=os.environ.get("SMTP_SENDER", "no-reply@gatherly.local"),
            use_starttls=os.environ.get("SMTP_STARTTLS", "true").casefold() == "true",
            timeout=float(os.environ.get("SMTP_TIMEOUT", "10")),
        )
        sent = 0
        for message in repository.pending_email():
            try:
                sender.send_text(
                    recipient=message["email"],
                    subject=message["title"],
                    body=message["body"],
                )
            except Exception as error:
                repository.record_email_failure(
                    notification_id=message["id"],
                    error_name=type(error).__name__,
                )
            else:
                repository.record_email_sent(
                    notification_id=message["id"], now=datetime.now(UTC)
                )
                sent += 1
        return created, sent
    finally:
        connection.close()


def main() -> None:
    created, sent = run_once()
    print(f"notifications_created={created} emails_sent={sent}")


if __name__ == "__main__":
    main()
