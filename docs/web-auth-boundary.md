# Web authentication and authorization boundary

## Status

**Design contract; no web routes are exposed yet.** Do not treat this document as an implemented login system.

The product decision between Telegram-based sign-in and a separate account/session model remains open. The web adapter must not invent identity or ship routes that assume every request is trusted while that decision is pending.

## Request flow

```text
HTTP request
    |
    v
Authentication adapter -- verifies a session/token
    |
    v
Authenticated Principal (stable internal user ID)
    |
    v
Web handler -- validates transport input only
    |
    v
Application service -- applies use-case rules and ownership checks
    |
    v
Repository
```

### Identity rules

- Never accept `owner_id`, `user_id`, or an equivalent identity field from request JSON, query parameters, or URL paths as proof of identity.
- The authentication adapter must derive the principal from a cryptographically verified session or token. A client-supplied user ID is not a principal.
- Web handlers pass the verified principal's internal user ID to application services. They must not duplicate business rules or bypass service-level ownership checks.
- Every object read and mutation must be scoped to the authenticated owner. Continue using generic not-found behavior for objects the principal does not own, so the API does not reveal whether another user's object exists.
- Missing, expired, malformed, or invalid credentials must be rejected before calling application services. No fallback to a default user, development identity, or unauthenticated write path is allowed.

## HTTP behavior

- Return `401 Unauthorized` when authentication is absent or invalid.
- Return `403 Forbidden` only when the resource's existence may safely be disclosed and the principal is authenticated but lacks a specific permission. For owner-scoped objects, prefer the application's generic not-found result.
- Return `400` or `422` for invalid transport input, consistently with the chosen web framework.
- Do not return stack traces, database errors, tokens, session identifiers, or internal secrets to clients.

## Session and browser protections

The final controls depend on the authentication method, but must be reviewed before launch:

- If browser cookies carry sessions, use `HttpOnly`, `Secure` in production, an appropriate `SameSite` policy, session expiry and rotation, and CSRF protection for state-changing requests.
- If bearer tokens are used, verify signature, issuer, audience, expiry, and relevant scopes; define revocation/rotation behavior and avoid storing long-lived credentials in browser-accessible storage.
- Rate-limit login, verification-code, and recovery endpoints; avoid revealing whether an account exists.
- Use HTTPS in production. Never log authorization headers, cookies, login codes, bot tokens, or session secrets.
- Configure trusted origins explicitly; do not enable permissive credentialed CORS.

## Implementation gate

Before adding protected web endpoints:

1. Choose the sign-in and account-linking model.
2. Define the stable internal user ID and how an external Telegram/account identity maps to it.
3. Implement and test the authentication adapter and principal creation.
4. Add tests proving unauthenticated requests never reach application services and that one user's principal cannot read or mutate another user's objects.
5. Only then expose event/participant routes and connect the web UI.

Until these steps are complete, this repository has no production-ready web authentication. The existing application services and their ownership checks remain the security boundary for business operations; the future HTTP adapter must preserve that boundary.
