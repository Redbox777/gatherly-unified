"use strict";

const CACHE_NAME = "gatherly-shell-v1";
const SHELL_ASSETS = [
  "/",
  "/index.html",
  "/offline.html",
  "/styles.css",
  "/api-client.js",
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

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cache API responses, authenticated data, or mutation requests.
  if (
    request.headers.get("accept")?.includes("application/json") ||
    url.pathname.startsWith("/auth/") ||
    url.pathname.startsWith("/account/") ||
    url.pathname.startsWith("/notifications")
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
