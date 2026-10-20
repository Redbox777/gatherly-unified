# Participant invitation lifecycle

## Scope

The first invitation flow links an existing event-scoped participant record to an existing Gatherly account. It does not create an account, grant event ownership, or change the participant's event history. Invitations are accepted through the shared application layer; web and Telegram adapters must not implement separate business rules.

The first delivery channel is email. Telegram-specific invitations are out of scope until their identity and delivery rules are specified.

## Authorization and identity

- Only the event owner may create or revoke an invitation for a participant in that event.
- The invited address must be normalized consistently and stored as the invitation's target email.
- Acceptance requires an authenticated Gatherly account with a verified email identity matching the target email. A display name, an unverified email, or possession of a different account is not sufficient.
- The accepting user cannot choose an arbitrary user ID. The application derives the account ID from the authenticated session.
- An invitation does not grant event ownership or access beyond the capabilities already provided to that account.
- Every read and write is scoped to the relevant event/participant and checked in the application layer.

## Lifecycle

An invitation has one of these states:

- pending: created and not yet accepted, revoked, or expired.
- accepted: consumed successfully and linked to the authenticated account.
- revoked: cancelled by the event owner before acceptance.
- expired: its deadline has passed; expiry is determined from persisted UTC timestamps.

Transitions:

- Create: no existing active invitation for the same participant; generate a cryptographically random, high-entropy token and store only its hash.
- Accept: atomically validate token hash, pending state, expiry, verified target email, participant existence, and that the participant is not already linked. In the same transaction, set linked_user_id and mark the invitation accepted.
- Revoke: only the event owner may revoke a pending invitation. Accepted invitations cannot be revoked through the invitation endpoint; unlinking, if ever supported, is a separate explicit workflow.
- Expire: a pending invitation becomes unusable after its deadline even if no background cleanup has run. A cleanup job may later mark it expired for reporting, but authorization must not depend on cleanup.

## Initial policy

- Invitation lifetime: 7 days from creation, stored in UTC.
- Tokens are single-use and must never be logged or stored in plaintext.
- Resending creates a fresh token and invalidates/revokes the previous pending invitation for that participant.
- At most one active invitation exists per participant at a time.
- If the participant is already linked to any account, creating or accepting another invitation fails without changing the existing link.
- Invalid, expired, revoked, and already-consumed tokens return a generic failure to avoid leaking invitation state to unauthorised callers.
- Email delivery is an adapter concern. The initial HTTP adapter sends after the invitation record commits; if SMTP delivery fails, it revokes the newly created invitation before returning a delivery error. A failed resend may therefore invalidate the previous invitation, because issuing the replacement revokes the old token first. If revocation cannot be confirmed, the API returns a distinct server error and does not claim the invitation was safely cancelled. A durable transactional outbox is a future improvement; raw bearer tokens must not be stored in an outbox or logs.

## Data and consistency

- Invitation records reference the event participant and the creating owner; they store normalized target email, token hash, creation/expiry timestamps, and lifecycle timestamps/state.
- Foreign-key behavior must not silently attach an invitation to a different participant. Deleting the participant invalidates/removes its pending invitations.
- Acceptance and participant linking must be one database transaction to prevent two concurrent acceptances from linking the same participant to different accounts.
- The participant record remains the stable event-scoped identity. Expenses and other event history continue to reference that record before and after account linking.
- Do not merge participant records by display name or automatically rewrite expense ownership during linking.

## Required security and regression tests

Before exposing an endpoint, cover at least:

1. Only the event owner can create or revoke an invitation.
2. A verified matching email can accept; an unverified or mismatched email cannot.
3. A token is single-use, expires at the deadline, and cannot be used after revocation.
4. Only the account authenticated for the request can be linked; clients cannot supply an arbitrary account ID.
5. A participant cannot be linked to two accounts, including concurrent acceptance attempts.
6. Cross-event participant IDs and invitation IDs cannot bypass ownership checks.
7. A failed acceptance leaves both invitation and participant state unchanged.
8. Existing participant/event history remains intact after acceptance.
9. Only a token hash is persisted and raw tokens are not written to logs.
10. Delivery failure behavior is tested before production email delivery is enabled.


## HTTP delivery and acceptance page

- The owner-only create endpoint sends the raw token only through the email adapter; the JSON response contains the invitation ID and expiry, never the bearer token.
- The email link points to the same-origin `/accept-invitation` page. The token is placed in the URL fragment (`#token=...`), which browsers do not include in the HTTP request to the server.
- The page removes the fragment from the visible address bar/history as soon as its script runs, then submits the token only in the POST body to the acceptance endpoint. Responses use `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
- If the visitor has no authenticated session, the page signs in the account first. Acceptance still relies on the server-side session and verified matching email; the page never supplies a user ID.
- If delivery fails after persistence, the adapter attempts to revoke the new invitation before returning an error. If revocation cannot be confirmed, the API reports a server error rather than claiming the token is invalidated.

## Current implementation status

The domain model, migration, repository, application service, HTTP create/revoke/accept endpoints, email delivery handling, and minimal acceptance page are implemented. The required checks are `ruff check .`, `mypy app`, and `pytest`; the CI run linked in the project history must be green before extending this flow.
