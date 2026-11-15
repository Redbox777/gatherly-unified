# Progressive Web App shell

Gatherly now exposes an installable PWA shell:

- `/manifest.webmanifest` provides the app name, standalone display mode, theme colors, start URL, and SVG app icon.
- `/service-worker.js` registers at the root scope and pre-caches the static application shell.
- `/offline.html` provides a minimal offline fallback.
- `/pwa.js` registers the service worker only in secure contexts (HTTPS or localhost).
- When the browser exposes `beforeinstallprompt`, the app shows an explicit install action. Browsers that do not expose this event retain their native install/share menu.
- A visible offline status warns that only the cached application shell is available and that event data and changes are not synchronized.

## Cache boundary

The service worker only handles same-origin GET requests. It does not cache requests marked as JSON, authentication routes, account routes, or notification routes. It does not intercept POST/PUT/PATCH/DELETE requests. The offline shell contains no account data, and the app's authenticated API remains server-backed.

The static shell is cached for application startup. This is not offline-first data synchronization: event lists, participants, notes, finance records, and notifications still require a live server connection. Do not add private API responses or user-specific HTML to the service-worker cache.

## Requirements and limitations

- Installation requires HTTPS in deployed environments; localhost is accepted for development.
- Browser support and installation UI differ by platform. The browser decides when to show its install prompt.
- The current service worker provides a static shell and offline fallback only. Push notifications require a separate VAPID/subscription stage.
- PWA installability does not mean the app is fully usable offline. No background sync or offline mutation queue is implemented.


## Account-scoped local storage (S6.2 foundation)

- The browser uses IndexedDB (`gatherly-private-cache`), not SQLite or localStorage, for structured offline records.
- Offline storage is **off by default** and can be enabled explicitly in Account → “Локальное хранение на этом устройстве”.
- Records are partitioned by the authenticated `user_id`; the ID is obtained from the live `/auth/me` response, never from a user-editable field.
- Switching to another authenticated account clears the previous active account's IndexedDB records. A successful logout clears the current account's records. Disabling the option also clears the current account's records.
- This foundation does not yet cache API responses or allow offline writes. That follows in the next S6.2 step after the account boundary is tested.
- IndexedDB is browser-managed storage and is **not separately encrypted by this application**. Device access controls still matter. Do not enable this on shared devices.


## Read-only offline event cache (S6.2)

When a user has explicitly enabled local storage, successful GET responses are stored in IndexedDB for the event list and details, participants, shared notes, tasks, and venue details. The API client uses cached data only after a network-level fetch failure. HTTP responses such as 401, 403, 404, 422, and 5xx are not replaced with cached data. Authentication, account management, notifications, finance, and all mutation methods remain network-only. The service worker still never caches private API responses.

If the app starts without a network connection, it can use the last account namespace previously confirmed by the live auth endpoint, provided local storage was explicitly enabled. This is a device-local offline view, not proof of a current server session. Cached information can be stale; edits, deletions, and new records are not queued or synchronized.
