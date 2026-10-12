# Architecture decisions

## 1. Shape

Start as a modular monolith. Keep business rules and use cases independent from Telegram handlers, HTTP routes, and database details. Avoid premature microservices.

```text
Telegram adapter ─┐
                   ├─> Application use cases ─> Domain rules
Web adapter ───────┘              │
                                  v
                         Repository interfaces
                                  │
                                  v
                         SQLite implementation
```

## 2. Interfaces

- Telegram bot is an interface, not the place for business rules.
- Web interface must work on desktop and mobile screens.
- Both interfaces call the same application layer.
- PWA support is a later enhancement, not a requirement for the first vertical slice.

## 3. Data

- Start with a new, empty database. Do not import or copy legacy data.
- Use versioned migrations; do not rely on `CREATE TABLE IF NOT EXISTS` as a migration strategy.
- Store monetary values as integer minor units (for example, cents/kopecks) or use a rigorously defined decimal representation; do not use binary floating point for money.
- Make ownership and authorization checks explicit in application services and repository queries.
- Use UTC for persisted timestamps and convert to the user's timezone at interface boundaries.

## 4. Security baseline

- Read secrets only from environment or a dedicated secret store.
- Never log bot tokens, session secrets, authentication codes, or sensitive payloads.
- Use parameterized database queries.
- Apply authorization on every object-level read/write, not just in the UI.
- Validate input at interface boundaries and enforce business invariants in the application/domain layer.
- Keep backups, databases, logs, and `.env` out of version control.
- Add security tests alongside feature tests.

## 5. First vertical slice

Implement a minimal, testable path: create an event, persist it, then retrieve it under the correct owner. Add participant management only after event ownership and authorization behavior are covered by tests.

## 6. Decisions still open

- Web authentication: compare Telegram-based sign-in with a separate account/session model before implementation.
- Deployment target and production database: start with SQLite for the initial single-instance foundation; reassess if concurrent multi-user deployment requires PostgreSQL.
- Expense split modes and invitation/participant identity model must be specified before those modules are implemented.
