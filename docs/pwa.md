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
