# Progressive Web App

Gatherly exposes an installable PWA shell:

- `/manifest.webmanifest` provides the app name, standalone display mode, theme colors, start URL, and SVG app icon.
- `/service-worker.js` pre-caches the static application shell and does not cache authenticated API responses.
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
- Switching to another authenticated account clears the previous account's cached records. Logout and disabling the option clear the current cached records.
- When the network is unavailable at startup, an explicitly opted-in app may read the last confirmed account's cache. This is a local read-only view, not proof of a live server session.
- The application does not separately encrypt IndexedDB. Do not enable it on shared or untrusted devices.

## Offline limitations before S6.3

- Cached records may be stale.
- Event-related writes are not queued. Before an online event-related write is sent, the current event cache is cleared to avoid later showing a known-obsolete snapshot.
- Finance, notifications, invitations, participant management, and account operations are not available offline.
- Installation requires HTTPS in deployed environments; localhost is accepted for development.
- PWA installation does not imply that the backend is installed on the device. The API and SQLite database remain server-side.
