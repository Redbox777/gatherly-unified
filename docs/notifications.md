# Notification delivery

Gatherly stores notification inbox items in SQLite. The first delivery adapter is
email; Telegram and browser push can be added as adapters without changing the
notification records or event-level preferences.

## Event reminders

Owners can configure reminders for 24 hours, 1 hour, and/or 15 minutes before
an event. The event must have a timezone-aware start timestamp. Reminder times are
derived from the current event start time, so editing the event reschedules pending
reminders. Reminders are materialized once per recipient and offset; linked event
members and the owner receive an inbox item. Unlinked guest records do not have a
Gatherly account to notify.

## Worker scheduling

The worker is a one-shot process and is intentionally not started inside the web
server. Run it once per minute with a platform scheduler or cron:

```sh
python -m app.infrastructure.notifications_worker
```

It uses `GATHERLY_DATABASE_PATH` (default `var/gatherly.sqlite3`) and the existing
SMTP settings: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_SENDER`, and optional `SMTP_STARTTLS` / `SMTP_TIMEOUT`. If SMTP host or
sender is not configured, inbox reminders are still created and email delivery
remains queued.

The worker applies the shared SMTP recipient/requester quotas. Failed delivery
attempts are recorded by exception type only and retried on a later worker run,
up to five attempts. A successful SMTP response is marked as sent. A process crash
after the SMTP server accepts a message but before SQLite records success can cause
a duplicate on the next run; SMTP does not provide an idempotency key, so delivery
is at-least-once rather than mathematically exactly-once.

## Notification preferences

Users can disable email delivery, event-change notices, participant-added notices,
or reminders independently. The in-app inbox remains available when email is
disabled. New notification channels should add their own delivery preference and
adapter rather than duplicating notification generation logic.

## Current scope

The inbox API is authenticated and user-scoped. Event owners configure reminders.
Event changes and newly added participant records notify already-linked members
who have that notification type enabled. Guest participants without an account
continue to receive the existing invitation email flow. Telegram chat linking and
Web Push subscription management are separate follow-up stages.
