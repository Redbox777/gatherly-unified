# Web authentication and authorization boundary

## Current implementation status

The repository now contains a working authentication baseline, but it is not yet a complete production identity platform.

Implemented:
- Email/password registration with email verification.
- Password login, password change, and single-use password reset.
- Telegram Login Widget HMAC verification, freshness checks, and replay protection.
- Server-managed HttpOnly session cookies with expiry and server-side revocation.
- Hashed session and email-token storage.
- Rate limits for sensitive authentication and email-delivery flows.
- CSRF Origin/Referer checks for state-changing browser requests.

**Important account-model limitation:** email and Telegram identities currently create or resolve separate internal Gatherly users. Cross-provider account linking and unlinking are not implemented. The participant invitation flow links a verified account to an event participant record; it does not merge email and Telegram login identities.

## Identity and credential rules

- Every account has one internal, stable Gatherly user ID.
- Email and Telegram identities are stored as separate provider identities.
- Do not automatically merge accounts because a Telegram profile contains a matching email or display name.
- Cross-provider linking must be a separate authenticated workflow requiring proof of control of both identities and recent re-authentication.
- Account recovery and unlinking must not leave an account without a usable login method.
- Never accept owner_id, user_id, or an equivalent identity field from request JSON, query parameters, or URL paths as proof of identity. The server derives the principal from the active session.

## Password hashing

New passwords are hashed with Argon2id using versioned PHC strings and fixed resource limits:
- Memory: 19,456 KiB (19 MiB).
- Time cost: 2 iterations.
- Parallelism: 1.
- Salt: 16 bytes; derived hash: 32 bytes.
- Password input is UTF-8 and limited to 1,024 bytes.
- Verification rejects unexpected Argon2id parameters before performing the expensive hash operation.

Existing scrypt hashes remain verifiable using their original fixed parameters. After a successful login, the service upgrades the stored hash to Argon2id with a compare-and-swap update; it does not change the user's password or revoke sessions. Failed logins never trigger a hash upgrade. No bulk hash rewrite or plaintext password storage is used.

## Session and browser protections

- Session cookies are HttpOnly, use SameSite=Lax, and must be Secure in production.
- Session tokens are random bearer values; only their hashes are stored.
- Password changes and resets revoke existing sessions.
- Verification and reset tokens are single-use, expiring bearer tokens; only their hashes are stored.
- State-changing browser requests validate Origin or Referer.
- Set PUBLIC_ORIGIN to the public HTTPS origin in production. This is required when TLS terminates at a reverse proxy and the app sees an internal HTTP URL. Do not trust arbitrary forwarded host/proto headers.
- Sensitive authentication and email routes are rate-limited. The SQLite-backed limiter is a single-host control, not a multi-host edge limiter.
- Do not log authorization headers, cookies, login codes, passwords, bot tokens, session secrets, or email-token values.

## Authorization boundary

- Every object read and mutation must be scoped to the authenticated owner or the explicit event membership policy.
- Owner-scoped objects return a generic 404 when the current user does not own them, avoiding disclosure of another user's object existence.
- Notes have separate visibility rules: private notes are author-only; shared notes are visible to the event owner and linked event participants. Only authors can edit; the owner can moderate shared notes.
- Tasks, venues, expenses, expense splits, balances, settlements, and reminders are owner-managed.
- Unlinked guest participant records do not receive account access.
- Authorization must be enforced in repository queries or application services, not only in the frontend.

## Production readiness still required

Before production launch:
1. Configure APP_ENV=production, PUBLIC_ORIGIN, COOKIE_SECURE=true, HTTPS public URLs, SMTP, and actual trusted proxy CIDRs.
2. Verify the reverse-proxy network path and ensure clients cannot bypass the trusted proxy.
3. Reassess the SQLite-backed rate limiter and database choice before multi-host deployment.
4. Benchmark Argon2id latency and memory use under expected login concurrency on the intended server.
5. Add tested backup/restore procedures, readiness checks, operational monitoring, and retention policies.
6. Design cross-provider account linking only if the product requires a single account to use both login methods.
7. Review privacy notice, data retention, and consent flows against the actual hosting and processing setup.
