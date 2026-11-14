# Gatherly Unified

A modular backend foundation for Gatherly with shared application logic and web/Telegram authentication adapters.

## Project principles

- New database; no legacy database or data import.
- Modular monolith with explicit domain, application, infrastructure, and interface layers.
- Telegram and web interfaces use the same application services.
- Configuration through environment variables; never commit secrets.
- Versioned database migrations and automated tests from the beginning.

## Implemented foundation

- Event creation and owner-scoped event management.
- Event-scoped participant records that remain stable when linked to an account.
- Owner-managed event tasks and optional venue details.
- Private and shared event notes with membership- and author-scoped access.
- Exact event expenses, equal/fixed/weighted splits, per-currency balances, and records of completed settlements.
- Email registration, verification, login, server-managed sessions, password reset/change, and Telegram login.
- Participant invitation lifecycle: owner-only creation/revocation, one-time token hashes, verified-email acceptance, and atomic participant linking.
- Invitation email delivery through the SMTP adapter; failed delivery revokes the newly issued invitation before returning an error.
- Authenticated notification inbox, per-user notification preferences, and event reminders at 24 hours, 1 hour, or 15 minutes.
- One-shot notification worker for scheduled email delivery, with shared SMTP quotas and bounded retries.
- SQLite persistence, versioned migrations, and integration tests.
- CI checks for linting, typing, and tests.

## Email authentication links

The backend serves same-origin pages at `GET /verify-email` and `GET /reset-password`.
Set `EMAIL_VERIFICATION_URL` and `PASSWORD_RESET_URL` to these pages unless a separate
web client implements the same fragment-token contract. Email links put the one-time
bearer token after `#token=`; the browser fragment is not sent in the HTTP request.
Each page removes the fragment from browser history and submits the token in a JSON
POST body to the corresponding authentication endpoint. The pages use no-store caching
and a no-referrer policy.

## Email token delivery and rotation

Verification and password-reset tokens are staged as inactive before an email is sent.
Only after the SMTP adapter reports successful delivery is the new token activated and
the previous active token retired. If delivery fails, the staged token is discarded
and the previously delivered link remains usable. Existing active tokens are preserved
when the database is upgraded to the staged-token schema.

## Outbound email rate limits

Outbound email attempts are limited in shared SQLite storage, so quotas apply across
application processes that use the same database. Defaults allow at most 3 messages
per normalized recipient in 15 minutes and 5 outbound email attempts per source IP
in one minute across registration, verification, password reset, and participant
invitations. These values can be adjusted with
`SMTP_RATE_LIMIT_RECIPIENT_MAX`,
`SMTP_RATE_LIMIT_RECIPIENT_WINDOW_SECONDS`,
`SMTP_RATE_LIMIT_REQUESTER_MAX`, and
`SMTP_RATE_LIMIT_REQUESTER_WINDOW_SECONDS`. Limits must be positive integers; windows
cannot exceed 24 hours. The limiter stores SHA-256 hashes of recipient and requester
keys rather than raw email addresses or IP addresses. Rate-limited password-reset and
verification-resend requests keep their generic public response.

## Sensitive HTTP request throttling

Sensitive POST routes (authentication, password changes, verification, and invitation
acceptance) are limited to 5 requests per source address per route per 60 seconds.
Counters are stored as SHA-256 hashes in SQLite, so independent application processes
using the same database enforce one shared quota. Old counter records are pruned after
24 hours. This is a single-host/shared-database control, not a substitute for an edge
rate limiter when deploying multiple hosts; configure trusted proxy handling before
relying on source-address quotas behind a reverse proxy.

For deployments behind a reverse proxy, optionally set `TRUSTED_PROXY_CIDRS` to a
comma-separated list of the proxy IP addresses or CIDR ranges that connect directly to
the app. Run Uvicorn with `--no-proxy-headers` so the ASGI `request.client` remains the
raw TCP peer; Gatherly performs the trusted-proxy resolution itself. Only when that
immediate peer matches this allowlist does Gatherly inspect
`X-Forwarded-For`, walking the chain from right to left and skipping trusted proxies.
Malformed forwarding chains fall back to the direct peer address. Without this setting,
forwarding headers are ignored. Restrict network access so clients cannot bypass the
trusted proxy and connect directly to the app; configure every proxy hop that may
appear in the forwarded chain. Do not trust all networks (for example, `0.0.0.0/0`
or `::/0`).

## Participant invitation configuration

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_SENDER` for real email delivery. Set `PARTICIPANT_INVITATION_URL` to the web client's invitation-acceptance page. The example value is a local development placeholder, not a page served by this backend.

The backend serves a minimal same-origin acceptance page at `GET /accept-invitation`. Invitation links place the bearer token in the URL fragment, which is not sent in the HTTP request; the page removes it from browser history and submits it to `POST /participant-invitations/accept` after sign-in. The API derives the account ID from the session; clients must not submit a user ID. Invitation creation is available at `POST /events/{event_id}/participants/{participant_id}/invitation`; owner revocation is `DELETE /events/{event_id}/participant-invitations/{invitation_id}`.

## Development

Install development dependencies and run the quality checks:

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy app
pytest
```

See [participant invitation lifecycle](docs/participant-invitations.md) and [architecture decisions](docs/architecture.md) for security and design constraints.

## Notifications and reminders

See [notification delivery and worker scheduling](docs/notifications.md). Run the one-shot worker every minute using your hosting scheduler:

```sh
python -m app.infrastructure.notifications_worker
```


## Event notes

Event notes support private entries and shared entries. Private notes are visible only to their author; shared notes are visible to the event owner and linked Gatherly participants. Notes are scoped to event membership, and edits are limited to the author. Event owners can remove shared notes for moderation. Unlinked guest records cannot access the application or notes.
