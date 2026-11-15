"""One-shot notification worker; schedule this command with cron or a platform scheduler."""

from datetime import UTC, datetime, timedelta
import logging
import os

from app.infrastructure.database.bug_report_retention import load_bug_report_retention_days
from app.infrastructure.database.bug_reports_repository import BugReportsRepository
from app.infrastructure.database.connection import connect, migrate
from app.infrastructure.database.notifications_repository import NotificationsRepository
from app.infrastructure.database.push_subscriptions_repository import (
    PushSubscriptionsRepository,
    decode_subscription_encryption_key,
)
from app.infrastructure.database.smtp_rate_limits_repository import SmtpRateLimitsRepository
from app.infrastructure.email.smtp_email_sender import SmtpEmailSender
from app.infrastructure.observability import configure_application_logging
from app.infrastructure.telegram_bot_sender import TelegramBotSender
from app.infrastructure.web_push_sender import WebPushDeliveryError, WebPushSender
from app.interfaces.web.runtime_config import validate_runtime_configuration


logger = logging.getLogger(__name__)


def run_once(database_path: str | None = None) -> tuple[int, int, int, int]:
    """Materialize reminders, expire diagnostics, and deliver opted-in channels."""
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
        sent = 0
        if os.environ.get("SMTP_HOST") and os.environ.get("SMTP_SENDER"):
            sender = SmtpEmailSender(
                host=os.environ.get("SMTP_HOST", "localhost"),
                port=int(os.environ.get("SMTP_PORT", "587")),
                username=os.environ.get("SMTP_USERNAME", ""),
                password=os.environ.get("SMTP_PASSWORD", ""),
                sender=os.environ.get("SMTP_SENDER", "no-reply@gatherly.local"),
                use_starttls=os.environ.get("SMTP_STARTTLS", "true").casefold() == "true",
                timeout=float(os.environ.get("SMTP_TIMEOUT", "10")),
            )
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

        telegram_sent = 0
        telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if telegram_token:
            telegram_sender = TelegramBotSender(
                bot_token=telegram_token,
                timeout=float(os.environ.get("TELEGRAM_TIMEOUT", "10")),
            )
            for message in repository.pending_telegram(limit=20):
                try:
                    telegram_sender.send_text(
                        chat_id=message["chat_id"],
                        text=f"{message['title']}\n\n{message['body']}",
                    )
                except Exception as error:
                    repository.record_telegram_failure(
                        notification_id=message["id"],
                        error_name=type(error).__name__,
                    )
                    logger.warning(
                        "Notification Telegram delivery failed",
                        extra={
                            "event": "notification.telegram.failed",
                            "exception_type": type(error).__name__,
                            "component": "notifications_worker",
                        },
                    )
                else:
                    repository.record_telegram_sent(
                        notification_id=message["id"], now=datetime.now(UTC)
                    )
                    telegram_sent += 1
        push_sent = 0
        push_settings = (
            os.environ.get("WEB_PUSH_VAPID_PRIVATE_KEY", "").strip(),
            os.environ.get("WEB_PUSH_VAPID_SUBJECT", "").strip(),
            os.environ.get("WEB_PUSH_SUBSCRIPTION_ENCRYPTION_KEY", "").strip(),
        )
        if all(push_settings):
            private_key, subject, encoded_encryption_key = push_settings
            encryption_key = decode_subscription_encryption_key(
                encoded_encryption_key
            )
            push_sender = WebPushSender(
                vapid_private_key=private_key,
                vapid_subject=subject,
                timeout=float(os.environ.get("WEB_PUSH_TIMEOUT", "10")),
            )
            push_repository = PushSubscriptionsRepository(connection)
            deliveries = push_repository.pending_deliveries(
                encryption_key=encryption_key,
                now=datetime.now(UTC),
                limit=100,
            )
            for delivery in deliveries:
                subscription_info = delivery["subscription_info"]
                if not isinstance(subscription_info, dict):
                    continue
                try:
                    push_sender.send(
                        subscription_info=subscription_info,
                        payload={
                            "title": "Gatherly",
                            "body": "У вас новое уведомление. Откройте приложение.",
                            "url": "/",
                        },
                    )
                except WebPushDeliveryError as error:
                    delivery_id = str(delivery["delivery_id"])
                    subscription_id = str(delivery["subscription_id"])
                    push_repository.record_failure(
                        delivery_id=delivery_id,
                        error_name=type(error).__name__,
                        now=datetime.now(UTC),
                    )
                    if error.status_code in {404, 410}:
                        push_repository.delete_subscription(
                            subscription_id=subscription_id,
                            now=datetime.now(UTC),
                        )
                    logger.warning(
                        "Browser push delivery failed",
                        extra={
                            "event": "notification.push.failed",
                            "exception_type": type(error).__name__,
                            "provider_status": error.status_code,
                            "component": "notifications_worker",
                        },
                    )
                except Exception as error:
                    push_repository.record_failure(
                        delivery_id=str(delivery["delivery_id"]),
                        error_name=type(error).__name__,
                        now=datetime.now(UTC),
                    )
                    logger.warning(
                        "Browser push delivery failed",
                        extra={
                            "event": "notification.push.failed",
                            "exception_type": type(error).__name__,
                            "component": "notifications_worker",
                        },
                    )
                else:
                    push_repository.record_sent(
                        delivery_id=str(delivery["delivery_id"]),
                        now=datetime.now(UTC),
                    )
                    push_sent += 1
        return created, sent, telegram_sent, push_sent
    finally:
        connection.close()


def main() -> None:
    configure_application_logging()
    created, sent, telegram_sent, push_sent = run_once()
    logger.info(
        "Notification worker completed",
        extra={
            "event": "notification.worker.completed",
            "component": "notifications_worker",
            "notifications_created": created,
            "emails_sent": sent,
            "telegram_messages_sent": telegram_sent,
            "push_messages_sent": push_sent,
        },
    )


if __name__ == "__main__":
    main()
