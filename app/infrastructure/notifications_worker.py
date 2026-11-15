"""One-shot notification worker; schedule this command with cron or a platform scheduler."""

from datetime import UTC, datetime, timedelta
import logging
import os

from app.infrastructure.database.bug_report_retention import load_bug_report_retention_days
from app.infrastructure.database.bug_reports_repository import BugReportsRepository
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.notifications_repository import NotificationsRepository
from app.infrastructure.database.smtp_rate_limits_repository import SmtpRateLimitsRepository
from app.infrastructure.email.smtp_email_sender import SmtpEmailSender
from app.infrastructure.observability import configure_application_logging
from app.interfaces.web.runtime_config import validate_runtime_configuration


logger = logging.getLogger(__name__)


def run_once(database_path: str | None = None) -> tuple[int, int]:
    """Materialize reminders, expire old diagnostics, and attempt pending email delivery."""
    validate_runtime_configuration(os.environ)
    retention_days = load_bug_report_retention_days(os.environ)
    path = database_path or os.environ.get(
        "GATHERLY_DATABASE_PATH", "var/gatherly.sqlite3"
    )
    connection = connect(path)
    try:
        migrate(connection)
        now = datetime.now(UTC)
        deleted_reports = BugReportsRepository(connection).delete_older_than(
            created_before=now - timedelta(days=retention_days)
        )
        if deleted_reports:
            logger.info(
                "Expired diagnostic reports deleted",
                extra={
                    "event": "diagnostics.retention.completed",
                    "component": "notifications_worker",
                    "deleted_reports": deleted_reports,
                    "retention_days": retention_days,
                },
            )
        repository = NotificationsRepository(connection)
        rate_limits = SmtpRateLimitsRepository(connection)
        created = repository.materialize_due_reminders(now=now)
        if not os.environ.get("SMTP_HOST") or not os.environ.get("SMTP_SENDER"):
            return created, 0
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
            attempt_time = datetime.now(UTC)
            retry_after = rate_limits.consume(
                recipient=message["email"],
                requester="notification-worker",
                now=attempt_time,
            )
            if retry_after is not None:
                continue
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
                logger.warning(
                    "Notification email delivery failed",
                    extra={
                        "event": "notification.email.failed",
                        "exception_type": type(error).__name__,
                        "component": "notifications_worker",
                    },
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
    configure_application_logging()
    created, sent = run_once()
    logger.info(
        "Notification worker completed",
        extra={
            "event": "notification.worker.completed",
            "component": "notifications_worker",
            "notifications_created": created,
            "emails_sent": sent,
        },
    )


if __name__ == "__main__":
    main()
