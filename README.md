# Gatherly Unified

A clean, modular foundation for Gatherly with shared application logic and separate Telegram and responsive web interfaces.

## Project principles

- New database; no legacy database or data import.
- Modular monolith with explicit domain, application, infrastructure, and interface layers.
- Responsive web interface for desktop and mobile browsers; PWA can be added later.
- Telegram and web interfaces use the same application services.
- Configuration through environment variables; never commit secrets.
- Versioned database migrations and automated tests from the beginning.

## Status

Initial architecture foundation. Implementation has not started yet.
