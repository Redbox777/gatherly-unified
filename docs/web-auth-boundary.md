# Web authentication and authorization boundary

## Current implementation status

Gatherly has a working authentication baseline and an explicit account-linking workflow. It is not yet a complete production identity platform; deployment, recovery, privacy, and operational controls remain separate release gates.

Implemented:
- Email/password registration with email verification, login, password change, and single-use password reset.
- Telegram Login Widget HMAC verification, freshness checks, and replay protection.
- Server-managed HttpOnly session cookies with expiry and server-side revocation.
- Hashed session, verification, password-reset, invitation, and Telegram replay-token storage.
- Rate limits for sensitive authentication and email-delivery flows.
- CSRF Origin/Referer checks for state-changing browser requests.
- Explicit Email ↔ Telegram account merging when the user independently proves control of both existing accounts.
- Event-participant invitation acceptance using a verified matching Email identity; this links a participant record, not two Gatherly login accounts.

## Identity and credential rules

- Every account has one internal, stable Gatherly user ID.
- Email and Telegram identities are stored as separate provider identities.
- Never merge accounts because an email address, display name, or Telegram username happens to match.
- Cross-provider account merging is a separate authenticated workflow. The destination account must have a recently created active session (within 10 minutes), and the source identity must be independently proven.
- Email source proof requires the current password for the verified Email identity. Telegram source proof requires a valid signed Login Widget payload no older than five minutes; the payload is single-use and replay-protected.
- If both accounts already have an identity from the same provider, reject the merge. Do not replace or guess which identity should win.
- The merge is transactional: identities and supported account-owned records move to the destination, existing sessions for both accounts are revoked, and a fresh destination session is issued. The source user is deleted only after its references have been transferred.
- Account merging does not attach a brand-new, unregistered provider identity and does not support unlinking. These are separate product/security workflows and must not be implied by the current UI.
- Participant invitations are distinct: acceptance links one event-scoped participant record to the authenticated user only when that user's verified Email matches the invitation. It does not merge login identities or grant event ownership.
- Account recovery and any future unlinking must not leave an account without a usable login method.
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

- Session tokens are random bearer values; only their SHA-256 digests are stored.
- The default session lifetime is 14 days. Expired or revoked sessions do not resolve to a user.
- Session cookies are HttpOnly and use SameSite=Lax; Secure must be enabled in production.
- Logout revokes the supplied session. Password changes and password resets revoke the user's active sessions. Account merging revokes sessions for both source and destination accounts before issuing a fresh destination session.
- Recent authentication for account merging is based on the active session's creation time and is limited to 10 minutes; signing in again creates a fresh session.
- Verification and reset tokens are single-use, expiring bearer tokens; only their hashes are stored. Email token delivery is staged so a failed send does not retire the previously delivered active token.
- State-changing browser requests validate Origin or Referer.
- Set PUBLIC_ORIGIN to the public HTTPS origin in production. This is required when TLS terminates at a reverse proxy and the app sees an internal HTTP URL. Do not trust arbitrary forwarded host/proto headers.
- Sensitive authentication and email routes are rate-limited. The SQLite-backed limiter is a single-host control, not a multi-host edge limiter.
- Do not log authorization headers, cookies, login codes, passwords, bot tokens, session secrets, raw invitation tokens, or email-token values.

## Authorization boundary

- Every object read and mutation must be scoped to the authenticated owner or the explicit event membership policy.
- Owner-scoped objects return a generic 404 when the current user does not own them, avoiding disclosure of another user's object existence.
- Notes have separate visibility rules: private notes are author-only; shared notes are visible to the event owner and linked event participants. Only authors can edit; the owner can moderate shared notes.
- Tasks, venues, expenses, expense splits, balances, settlements, and reminders are owner-managed.
- Unlinked guest participant records do not receive account access.
- Invitation acceptance and participant linking occur in one database transaction. Concurrent attempts with the same invitation cannot link one participant to two accounts; the competing-account regression test asserts that exactly one authenticated user wins.
- Authorization must be enforced in repository queries or application services, not only in the frontend.

## Production readiness still required

Before production launch:
1. Configure APP_ENV=production, PUBLIC_ORIGIN, COOKIE_SECURE=true, HTTPS public URLs, SMTP, Telegram credentials where enabled, VAPID secrets where Web Push is enabled, and actual trusted proxy CIDRs.
2. Verify the reverse-proxy network path and ensure clients cannot bypass the trusted proxy.
3. Reassess the SQLite-backed rate limiter and database choice before multi-host deployment.
4. Benchmark Argon2id latency and memory use under expected login concurrency on the intended server.
5. Perform and record a tested backup/restore drill, readiness checks, operational monitoring, and a retention schedule.
6. Review privacy notice, data retention, and consent flows against the actual hosting and processing setup. Technical export/deletion controls do not themselves establish GDPR compliance.
7. Verify Email/Telegram account merging, provider-collision rejection, data transfer, and old-session revocation on the deployed HTTPS origin using test accounts.
8. Verify real email, Telegram, and Web Push delivery only after the corresponding production credentials and infrastructure are configured.
