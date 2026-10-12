# Gatherly Unified

A clean, modular foundation for Gatherly with shared application logic and separate Telegram and responsive web interfaces.

## Project principles

- New database; no legacy database or data import.
- Modular monolith with explicit domain, application, infrastructure, and interface layers.
- Responsive web interface for desktop and mobile browsers; PWA can be added later.
- Telegram and web interfaces use the same application services.
- Configuration through environment variables; never commit secrets.
- Versioned database migrations and automated tests from the beginning.

## Implemented foundation

- Event domain model with input validation and UTC-normalized start times.
- SQLite persistence and versioned migrations for events and event participants.
- Event creation and owner-scoped retrieval.
- Participant add/list operations with event ownership checks.
- Atomic application-level transactions and database integrity constraints.
- Unit and integration tests, plus CI checks for linting, typing, and tests.

## Current scope

This is the backend foundation, not a finished user-facing application. Telegram handlers, web routes, authentication, and the remaining product modules are not implemented yet.

## Development

Install development dependencies and run the quality checks:

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy app
pytest
```

See [architecture decisions](docs/architecture.md) for design constraints and open decisions.
