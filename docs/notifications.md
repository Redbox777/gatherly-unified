# Notification delivery

Gatherly stores notification inbox items in SQLite. Email and opt-in Telegram
delivery use the same notification records; browser push can be added as another
adapter without changing event-level notification generation.

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
`SMTP_SENDER`, and optional `SMTP_STARTTLS` / `SMTP_TIMEOUT`. In development,
if SMTP host or sender is not configured, inbox reminders are still created and
email delivery remains queued. In production, the worker validates the same
runtime security configuration as the web application before opening the database;
HTTPS origin, secure cookies, a real SMTP host/sender, and STARTTLS are required.

The worker applies the shared SMTP recipient/requester quotas. Failed delivery
attempts are recorded by exception type only and retried on a later worker run,
up to five attempts. A successful SMTP response is marked as sent. A process crash
after the SMTP server accepts a message but before SQLite records success can cause
a duplicate on the next run; SMTP does not provide an idempotency key, so delivery
is at-least-once rather than mathematically exactly-once.

## Telegram delivery

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME`, and optionally
`TELEGRAM_TIMEOUT` in the worker and web process environments. Keep the bot token
in the secret manager; never commit it or log the Bot API URL. Configure the bot
username so the settings panel can link to the correct bot.

A user must have a verified Telegram identity on the current Gatherly account,
open the configured bot, press **Start**, and explicitly enable Telegram in
notification settings. Telegram is disabled by default. The API rejects enabling
the channel if the account has no verified Telegram identity or the server has
no bot token. The worker processes at most 20 Telegram messages per run and retries
failures up to five attempts, recording only the exception type. Telegram may
reject messages if the user has not started the bot, blocked it, or changed access.

Telegram login identities and email identities are still separate accounts at
this stage. An email-only account cannot receive Telegram notifications until
cross-provider account linking is implemented. Do not auto-link accounts by email
or display name.

As with email, a process crash after Telegram accepts a message but before the
worker marks it sent can produce a duplicate. The delivery guarantee is
at-least-once, not exactly-once.

## Notification preferences

Users can disable email delivery, event-change notices, participant-added notices,
or reminders independently. The in-app inbox remains available when email is
disabled. New notification channels should add their own delivery preference and
adapter rather than duplicating notification generation logic.

## Current scope

The inbox API is authenticated and user-scoped. Event owners configure reminders.
Event changes and newly added participant records notify already-linked members
who have that notification type enabled. Guest participants without an account
continue to receive the existing invitation email flow. Cross-provider account
linking and Web Push subscription management are separate follow-up stages.


## Production worker configuration

The worker is a separate process and does not inherit FastAPI startup validation. It therefore validates production settings itself before opening SQLite or attempting delivery. Configure the same production environment variables for both the web process and the worker. A correctly configured web process does not compensate for a worker launched with a different or incomplete environment.
