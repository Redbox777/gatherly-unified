"use strict";

const CACHE_NAME = "gatherly-shell-v2";
const SHELL_ASSETS = [
  "/",
  "/index.html",
  "/offline.html",
  "/styles.css",
  "/pwa.js",
  "/offline-store.js",
  "/api-client.js",
  "/offline-sync.js",
  "/date-utils.js",
  "/participants-ui.js",
  "/notes-ui.js",
  "/tasks-ui.js",
  "/notifications-ui.js",
  "/finance-ui.js",
  "/app.js",
  "/diagnostics.js",
  "/auth.js",
  "/manifest.webmanifest",
  "/icon.svg"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys
          .filter(key => key.startsWith("gatherly-shell-") && key !== CACHE_NAME)
          .map(key => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("push", event => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = {};
  }
  const title = typeof payload.title === "string"
    ? payload.title.slice(0, 100)
    : "Gatherly";
  const body = typeof payload.body === "string" ? payload.body.slice(0, 300) : "";
  const requestedUrl = typeof payload.url === "string" ? payload.url : "/";
  const safeUrl = requestedUrl.startsWith("/") && !requestedUrl.startsWith("//")
    ? requestedUrl
    : "/";
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/icon.svg",
      badge: "/icon.svg",
      data: { url: safeUrl }
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const requestedUrl = event.notification.data?.url;
  const safeUrl = typeof requestedUrl === "string" &&
    requestedUrl.startsWith("/") && !requestedUrl.startsWith("//")
    ? requestedUrl
    : "/";
  const targetUrl = new URL(safeUrl, self.location.origin);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true })
      .then(async clients => {
        const existing = clients.find(client => {
          try {
            return new URL(client.url).origin === self.location.origin;
          } catch (_) {
            return false;
          }
        });
        if (existing) {
          await existing.navigate(targetUrl.href);
          return existing.focus();
        }
        return self.clients.openWindow(targetUrl.href);
      })
  );
});

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cache API responses, authenticated data, or mutation requests.
  if (
    request.headers.get("accept")?.includes("application/json") ||
    url.pathname.startsWith("/auth/") ||
    url.pathname === "/account" ||
    url.pathname.startsWith("/account/") ||
    url.pathname === "/notifications" ||
    url.pathname.startsWith("/notifications/")
  ) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(async () => {
        const cached = await caches.match(request, { ignoreSearch: true });
        return cached || caches.match("/offline.html");
      })
    );
    return;
  }

  if (
    url.pathname === "/styles.css" ||
    url.pathname.endsWith(".js") ||
    url.pathname === "/manifest.webmanifest" ||
    url.pathname === "/icon.svg"
  ) {
    event.respondWith(
      caches.match(request).then(cached => cached || fetch(request))
    );
  }
});
