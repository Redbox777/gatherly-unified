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
- Email registration, verification, login, server-managed sessions, password reset/change, and Telegram login.
- Participant invitation lifecycle: owner-only creation/revocation, one-time token hashes, verified-email acceptance, and atomic participant linking.
- Invitation email delivery through the SMTP adapter; failed delivery revokes the newly issued invitation before returning an error.
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
