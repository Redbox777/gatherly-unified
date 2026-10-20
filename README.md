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

## Participant invitation configuration

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_SENDER` for real email delivery. Set `PARTICIPANT_INVITATION_URL` to the web client's invitation-acceptance page. The example value is a local development placeholder, not a page served by this backend.

The web client must require the invitee to sign in and then submit the token to `POST /participant-invitations/accept`. The API derives the account ID from the session; clients must not submit a user ID. Invitation creation is available at `POST /events/{event_id}/participants/{participant_id}/invitation`; owner revocation is `DELETE /events/{event_id}/participant-invitations/{invitation_id}`.

## Development

Install development dependencies and run the quality checks:

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy app
pytest
```

See [participant invitation lifecycle](docs/participant-invitations.md) and [architecture decisions](docs/architecture.md) for security and design constraints.
