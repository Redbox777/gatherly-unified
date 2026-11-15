# Progressive Web App

Gatherly exposes an installable PWA shell:

- `/manifest.webmanifest` provides the app name, standalone display mode, theme colors, start URL, and SVG app icon.
- `/service-worker.js` pre-caches the static application shell, including every local script loaded by the main page, and does not cache authenticated API responses.
- `/offline.html` provides a minimal offline fallback.
- `/pwa.js` registers the service worker only in secure contexts (HTTPS or localhost).
- Where supported, the app exposes an install action and an online/offline status message.

## Private data cache boundary

Private event data is stored only in the opt-in IndexedDB store described below. It is not placed in the service-worker Cache API. The service worker does not intercept POST/PUT/PATCH/DELETE requests and does not perform authenticated background synchronization.

Successful GET responses are cached only for the event list/details, participants, notes, tasks, and venue. A cached response is used only after a network-level fetch failure. HTTP errors (including 401, 403, 404, 422, and 5xx) are not masked with stale cached data. Authentication, account management, notifications, and finance remain network-only.

## Account-scoped local storage (S6.2)

- Browser structured storage uses IndexedDB (`gatherly-private-cache`); localStorage contains only the opt-in flag and last confirmed account namespace.
- Offline storage is **off by default** and can be enabled explicitly in Account → “Локальное хранение на этом устройстве”.
- The namespace comes from the authenticated `user_id` returned by the live `/auth/me` endpoint, never from a user-editable field.
- Switching to another authenticated account clears the previous account's cached records. Unsent outbox commands remain isolated under their original account and cannot be sent until that same account is authenticated again.
- When the network is unavailable at startup, an explicitly opted-in app may read the last confirmed account's cache. This is a local read-only view, not proof of a live server session.
- The application does not separately encrypt IndexedDB. Do not enable it on shared or untrusted devices.

## Foreground offline synchronization (S6.3)

When local storage is enabled, a limited set of changes to an existing event can be queued while offline:

- Create, update, and delete notes.
- Create, update, delete, and complete tasks.
- Set or remove the event venue.

The queue is account-scoped and uses stable operation IDs, server-side idempotency, event revisions, and authorization checks at synchronization time. The user starts synchronization explicitly while the app is open and online. Network retries reuse the same operation ID and payload.

Important boundaries:

- Finance, expenses, settlements, notifications, invitations, participant changes, account operations, and event creation/deletion are not available offline.
- There is no service-worker background sync and no authenticated work while the app is closed.
- Queued changes are shown in the queue panel; the cached event view is not a full optimistic projection of queued changes.
- Revision conflicts pause the affected event's queue. The user must explicitly discard or reapply a command; no silent overwrite or automatic merge is performed.
- IndexedDB is not encrypted by the application. Account scoping is not cryptographic isolation from another person who can use the same browser profile.
- Real-browser validation is still required for two-tab behavior, lost responses after server commit, account deletion cleanup, and the complete set of sender status transitions.

PWA installation requires HTTPS in deployed environments; localhost is accepted for development. Installation does not imply that the backend is installed on the device. The API and SQLite database remain server-side.
