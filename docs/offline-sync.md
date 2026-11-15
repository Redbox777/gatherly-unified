# S6.3 — Safe offline mutation queue

**Status:** S6.3a and S6.3b server foundation implemented. The authenticated command endpoint and atomic idempotency handler exist, and existing online writes advance event revisions. The PWA does not call this endpoint; browser-side offline mutation remains disabled.

## Goal

Allow a user who explicitly enabled local storage to prepare a limited set of changes while offline, then submit them safely after connectivity returns. The queue must preserve account isolation, server authorization, operation ordering, and recoverability after ambiguous network failures.

The browser is an untrusted client. An IndexedDB record, local user ID, queued payload, timestamp, or client-side permission flag is never authorization. The server session and existing domain rules remain authoritative.

## Non-goals for the first release

- No finance, expenses, settlements, payment-like records, notifications, push subscriptions, invitations, email/Telegram actions, account changes, or participant add/remove operations.
- No offline event creation, event deletion, or event ownership changes.
- No service-worker background sync and no authenticated work while the app is closed.
- No last-write-wins behavior and no silent automatic merge of conflicting edits.
- No claim of exactly-once network delivery. The target is **at-least-once delivery with idempotent server application**.

## Phase 1 allowlist

Only commands on an existing event are candidates:

| Command | Authorization at sync time | Notes |
|---|---|---|
| Create/update/delete note | Same event membership and author/owner rules as online API | Private/shared visibility is revalidated |
| Create/update/delete/complete task | Event owner, matching current online API | Assignee must still belong to the event |
| Set/delete venue | Event owner, matching current online API | Coordinates and text are revalidated |

The allowlist is a closed server-side enum, not an arbitrary URL/method supplied by the client. Unknown command types are rejected. Existing online endpoints remain usable and are not routed through a permissive generic write endpoint.

## Operation envelope

Each queued command has a stable UUID generated once on the device:

```json
{
  "operation_id": "UUID",
  "event_id": "UUID",
  "base_revision": 42,
  "kind": "task.complete",
  "payload": { "task_id": "UUID", "completed": true },
  "created_at": "client timestamp for display only"
}
```

The authenticated user ID is **not** accepted from the payload. The server derives it from the existing session cookie. The client never stores session cookies or bearer credentials in the outbox.

The same `operation_id` and exact payload are reused for every retry. If the user changes the command after a conflict, that is a new operation with a new UUID and a newly confirmed base revision.

## Server contract: idempotency and atomicity

Introduce a dedicated sync command endpoint and a server-side idempotency ledger. Do not implement idempotency as a client-only header convention.

For every accepted command, one SQLite write transaction must:

1. Resolve the live authenticated user from the session.
2. Validate the command enum, payload schema, event ID, payload size, and idempotency key.
3. Look up the idempotency key scoped to the authenticated user.
4. If the key already exists with the same canonical payload hash, return the previously stored result without applying the command again.
5. If the key exists with a different hash, return a deterministic conflict response; never reinterpret the key.
6. Check current event membership/ownership and the expected event revision.
7. Apply the domain command using the same validation and authorization rules as the online use cases.
8. Store the resulting status/body and payload hash in the idempotency ledger in the **same transaction** as the domain change.
9. Commit once. A crash before commit applies neither the change nor its idempotency record; a lost response after commit is safe to retry.

A separate transaction that records the idempotency key after the domain write is not acceptable: a crash between those writes can duplicate creates.

Use a unique constraint on (authenticated user ID, operation ID), canonical JSON hashing, a bounded request size, and a documented retention window (initial proposal: 30 days, with cleanup that never removes recent keys). A repeated key with a different payload must never be treated as a new command.

## Revision and conflict model

Add a monotonically increasing per-event revision. Every online or offline mutation that can affect the Phase 1 command set must increment it atomically, including changes made through existing online endpoints. A migration should use database triggers or a single shared repository boundary so no legacy write path can bypass revision updates.

The client sends `base_revision`. The server applies a command only if it matches the current revision. A stale revision returns a typed conflict response with the current revision and, where safe, the current representation of the affected resource. Authorization is checked before returning resource details.

Conflict policy:

- Never overwrite newer server data automatically.
- Mark the operation `conflict` and pause later commands for that event.
- Show the local draft and current server version.
- User may discard the local command, or explicitly reapply/edit it against the newly fetched revision. Reapplication creates a new operation ID.
- Independent events may continue syncing while one event is blocked.

This event-level revision is deliberately conservative: unrelated changes within the same event can cause a conflict, but they cannot silently overwrite each other.

## IndexedDB outbox

Use a separate `outbox` object store in the existing account-scoped database. Key every record by `userId + operationId`; store only allowlisted command data and delivery metadata.

Suggested states:

- `pending` — waiting for connectivity or user action.
- `sending` — transient state; recovered to pending after a tab/process crash.
- `done` — server acknowledged and the local optimistic overlay can be reconciled.
- `conflict` — revision conflict; later commands for that event are paused.
- `blocked` — permanent validation/authorization/not-found failure requiring user attention.

Do not delete an operation merely because a fetch timed out. The server may already have committed it; retry the same ID and payload to obtain the stored result.

On logout, lock the outbox and do not display or send it. Preserve pending operations for the same user so an accidental logout does not silently destroy unsent work; resume only after a fresh `/auth/me` response proves the same user ID. On account switch, keep namespaces inaccessible to the new user. Account deletion and explicit “discard pending changes” must purge the matching outbox. If local storage is disabled while pending commands exist, require an explicit discard confirmation rather than silently deleting them.

Because IndexedDB is not application-encrypted, the UI must disclose that queued drafts remain on the device until synchronized or explicitly discarded. No queue contents are exposed to another authenticated account.

## Delivery, ordering, and retry

- Initially sync only while the app is open and online, with a visible pending count and an explicit “Синхронизировать” action. Automatic foreground resume may follow after the user has opted in.
- Send one operation at a time per event, preserving creation order. A blocked/conflicting command pauses only its event chain.
- Use Web Locks where available, with a short IndexedDB lease fallback, to avoid multiple tabs acting as independent senders. Server idempotency remains the final duplicate protection.
- Network errors and 5xx: retry with capped exponential backoff and jitter.
- 429: respect `Retry-After`.
- 401: pause all sending until the same account is freshly authenticated.
- 403/404/422: mark blocked; do not retry automatically.
- Revision conflict: mark conflict and pause that event.
- Do not use a service-worker background fetch or store auth material in the service worker.

## Required tests before enabling the feature

### Server tests
- Same user + same operation ID + same payload applies a create once and returns the identical stored result on replay.
- Same key + different payload is rejected.
- Two concurrent requests with the same key cannot create duplicates.
- Crash/rollback simulation proves the domain write and idempotency record are atomic.
- Stale event revision does not mutate data.
- Every existing online mutation path increments the revision.
- Unauthenticated, wrong-owner, removed-member, and cross-account requests are rejected at sync time.
- Invalid task assignee and invalid note visibility/content are rejected.
- Expired/revoked sessions cannot replay queued commands.

### Browser tests
- Outbox survives reload and temporary offline/online transitions.
- A timeout after server commit retries the same operation ID and does not duplicate.
- Account A's queue is never rendered or sent under account B.
- Logout locks the queue; re-authentication as A resumes it; account deletion purges it.
- One conflict pauses only that event chain.
- 401, 403, 404, 409, 422, 429, and 5xx each follow the documented state transition.
- Two tabs do not produce independent conflicting queue workers.
- Disabling local storage with pending operations requires an explicit discard decision.

## Implementation sequence

1. **S6.3a — server foundation: complete.** Migration 24 adds per-event revisions and a user-scoped idempotency ledger. The authenticated `/offline-sync/commands` endpoint validates the live session inside the same `BEGIN IMMEDIATE` transaction as authorization, revision check, domain mutation and replay receipt. The endpoint is not called by the PWA.
2. **S6.3b — online-path consistency:** ensure every existing relevant mutation increments the same event revision; add endpoint and concurrency tests.
3. **S6.3c — client outbox:** IndexedDB outbox, strict command allowlist, account lock/unlock, visible queue state. Do not send yet.
4. **S6.3d — foreground sync:** ordered sender, retry rules, idempotent replay, conflict UI and browser tests.
5. **S6.3e — controlled trial:** enable only for test accounts, inspect real-device behavior, then consider broader rollout.

Do not start S6.3c/d until S6.3a/b are merged and their CI is green. This condition is satisfied after PRs #91–#93; proceed with S6.3c only after a separate review of the browser outbox design. No server deployment is required for designing or testing these changes locally/through CI, but the live PWA cannot synchronize until the updated backend is deployed.
