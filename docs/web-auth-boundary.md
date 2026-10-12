# Web authentication and authorization boundary

## Status

**Decision: support both Telegram sign-in and separate Gatherly accounts.** Both methods must resolve to the same internal Gatherly user account. This is a design decision, not an implemented login system.

The separate-account method will use verified email plus password, with secure server-managed sessions and a password-reset flow. Telegram sign-in will use Telegram's supported identity-verification flow; the server must verify the returned identity and must never trust client-provided Telegram IDs or profile fields on their own.

## Account model

- Every person has one internal, stable Gatherly user ID.
- A verified email/password identity and a verified Telegram identity are login methods linked to that internal account; they are not separate copies of the person's data.
- Do not automatically merge accounts just because a Telegram profile contains an email or display name that matches another account.
- Linking a new login method requires the user to be authenticated to the existing account and to prove control of the new method.
- Account recovery and unlinking must not leave an account without a usable login method. High-risk linking/unlinking actions should require recent re-authentication.
- Keep credentials and external identities in separate records from profile/business data. Store only what is required.

## Request flow

```text
Telegram sign-in ─┐
                  ├─> Verify identity ─> Internal Gatherly user ID
Email/password ───┘                              |
                                                 v
                                      Server-managed session
                                                 |
                                                 v
HTTP request -> Authentication adapter -> Authenticated Principal
                                                 |
                                                 v
                                      Web handler (input only)
                                                 |
                                                 v
                                  Application service / ownership
                                                 |
                                                 v
                                            Repository
```

### Identity rules

- Never accept `owner_id`, `user_id`, or an equivalent identity field from request JSON, query parameters, or URL paths as proof of identity.
- The authentication adapter must derive the principal from a verified session. A client-supplied user ID is not a principal.
- Web handlers pass the verified principal's internal user ID to application services. They must not duplicate business rules or bypass service-level ownership checks.
- Every object read and mutation must be scoped to the authenticated owner. Continue using generic not-found behavior for objects the principal does not own, so the API does not reveal whether another user's object exists.
- Missing, expired, malformed, or invalid credentials must be rejected before calling application services. No fallback to a default user, development identity, or unauthenticated write path is allowed.

## HTTP behavior

- Return `401 Unauthorized` when authentication is absent or invalid.
- Return `403 Forbidden` only when the resource's existence may safely be disclosed and the principal is authenticated but lacks a specific permission. For owner-scoped objects, prefer the application's generic not-found result.
- Return `400` or `422` for invalid transport input, consistently with the chosen web framework.
- Do not return stack traces, database errors, tokens, session identifiers, or internal secrets to clients.

## Session and browser protections

- Use server-managed sessions in `HttpOnly` cookies; set `Secure` in production, use an appropriate `SameSite` policy, expire and rotate sessions, and protect state-changing requests against CSRF.
- Store password hashes using a current password-hashing algorithm designed for passwords (prefer Argon2id through a maintained library); never store plaintext or reversibly encrypted passwords.
- Rate-limit login, password-reset, verification, and account-linking attempts; avoid revealing whether an email/account exists.
- Use single-use, expiring, securely generated reset/verification tokens; store token hashes rather than raw tokens.
- Require HTTPS in production. Never log authorization headers, cookies, login codes, passwords, bot tokens, or session secrets.
- Configure trusted origins explicitly; do not enable permissive credentialed CORS.
- Do not store long-lived session credentials in browser-accessible storage.

## Implementation gate

Before adding protected web endpoints:

1. Define the account, credential, external-identity, and session records in a versioned migration.
2. Implement email verification, password hashing/login, reset flow, Telegram identity verification, and secure account linking without automatic account merges.
3. Create the internal principal only after successful authentication; rotate the session on login and privilege changes.
4. Add tests proving invalid/unauthenticated requests never reach application services, sessions expire, account linking requires proof of both identities, and one user's principal cannot read or mutate another user's objects.
5. Only then expose event/participant routes and connect the web UI.

Until these steps are complete, this repository has no production-ready web authentication. The existing application services and their ownership checks remain the security boundary for business operations; the future HTTP adapter must preserve that boundary.
