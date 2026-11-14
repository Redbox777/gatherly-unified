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
- Web authentication decision: support both Telegram sign-in and separate email/password accounts, linked to one internal Gatherly user ID. See [Web authentication and authorization boundary](web-auth-boundary.md).

## 3. Data

- Start with a new, empty database. Do not import or copy legacy data.
- Use versioned migrations; do not rely on `CREATE TABLE IF NOT EXISTS` as a migration strategy.
- Store monetary values as integer minor units (for example, cents/kopecks) or use a rigorously defined decimal representation; do not use binary floating point for money.
- Make ownership and authorization checks explicit in application services and repository queries.
- Use UTC for persisted timestamps and convert to the user's timezone at interface boundaries.

## 4. Security baseline

- Read secrets only from environment or a dedicated secret store.
- Never log bot tokens, session secrets, authentication codes, or sensitive payloads.
- SMTP authentication must use STARTTLS; do not disable TLS when credentials are configured.
- Set `COOKIE_SECURE=true` in production behind HTTPS. The development example also defaults to secure cookies; use `false` only for local plain-HTTP testing.
- Set `PUBLIC_ORIGIN` to the public HTTPS origin (scheme + host + optional port, no path) in production. CSRF Origin/Referer checks use this explicit value because TLS-terminating proxies may leave the app seeing an internal HTTP URL. Do not derive the public origin from untrusted forwarded headers.
- Sensitive HTTP POST routes use a SQLite-backed rate limiter shared by processes using the same local database. It is suitable for a single-host deployment only; use an edge/shared network limiter and reassess the database before deploying multiple hosts. Source-address quotas also depend on correct trusted-proxy configuration.
- Client IP resolution ignores `X-Forwarded-For` unless the direct peer belongs to the explicit `TRUSTED_PROXY_CIDRS` allowlist. Run Uvicorn with `--no-proxy-headers` so `request.client` exposes the raw TCP peer; Gatherly resolves the forwarded chain itself. Configure only actual proxy networks, ensure the app cannot be reached directly around the proxy, and use proxies that append the connecting peer to the forwarded chain. Malformed chains fall back to the direct peer.
- Use parameterized database queries.
- Apply authorization on every object-level read/write, not just in the UI.
- Validate input at interface boundaries and enforce business invariants in the application/domain layer.
- Keep backups, databases, logs, and `.env` out of version control.
- Add security tests alongside feature tests.

## 5. First vertical slice

Implement a minimal, testable path: create an event, persist it, then retrieve it under the correct owner. Add participant management only after event ownership and authorization behavior are covered by tests.

## 6. Participant identity model

Use a hybrid model for event participants:

- An event participant may be a named guest without a Gatherly account.
- A participant may optionally be linked to an internal Gatherly user ID after that identity is verified and the user accepts or otherwise authorizes the link.
- Keep the event-scoped participant record distinct from the user account. A display name is not proof of identity and must never be used to auto-link accounts.
- Invitations and invitation acceptance are a separate future workflow; do not imply that adding a guest grants that person account access.
- Expense records should reference the event participant record, so named guests and registered users can both take part in a split. Linking a participant to a user must preserve the participant's existing event and expense history.
- Define uniqueness, permissions, invitation expiry/revocation, and account-linking behavior before implementing invitations or expenses.

## 7. Decisions still open

- Deployment target and production database: start with SQLite for the initial single-instance foundation; reassess if concurrent multi-user deployment requires PostgreSQL.
- SQLite is currently a single-instance deployment choice. Keep the database on a local persistent disk, provide tested backups, and avoid sharing the SQLite file across multiple application hosts. Reassess PostgreSQL before horizontal scaling or sustained write concurrency; `busy_timeout` only reduces transient lock errors and is not a scaling strategy.
- Expense split modes, rounding rules, and invitation lifecycle must be specified before those modules are implemented.
