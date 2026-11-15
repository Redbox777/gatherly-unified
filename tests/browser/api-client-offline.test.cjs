"use strict";

require("fake-indexeddb/auto");
const test = require("node:test");
const assert = require("node:assert/strict");

const values = new Map();
global.window = globalThis;
global.window.location = { origin: "https://gatherly.test", replace() {} };
global.localStorage = {
  getItem(key) { return values.has(key) ? values.get(key) : null; },
  setItem(key, value) { values.set(key, String(value)); },
  removeItem(key) { values.delete(key); }
};
require("../../app/interfaces/web/static/offline-store.js");

const store = global.GatherlyOfflineStore;
const eventId = "11111111-1111-4111-8111-111111111111";

test("API client caches the initial revision and queues supported writes without erasing offline cache", async () => {
  await store.setEnabled(true);
  await store.setUser("api-account");
  let notesRead = 0;
  global.fetch = async (url, options = {}) => {
    if (url === "/events/" + eventId + "/sync-state") {
      return new Response(JSON.stringify({ event_id: eventId, revision: 7 }), {
        status: 200, headers: { "Content-Type": "application/json" }
      });
    }
    if (url === "/events/" + eventId + "/notes" && (!options.method || options.method === "GET")) {
      notesRead += 1;
      if (notesRead === 1) return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
      throw new TypeError("Network unavailable");
    }
    if (url === "/events/" + eventId + "/notes" && options.method === "POST") {
      throw new TypeError("Network unavailable");
    }
    throw new Error("Unexpected fetch URL: " + url);
  };

  require("../../app/interfaces/web/static/api-client.js");
  const api = global.GatherlyApiClient.request;
  await api("/events/" + eventId + "/sync-state");
  assert.equal(await store.getEventRevision(eventId), 7);
  assert.deepEqual(await api("/events/" + eventId + "/notes"), []);

  const queued = await api("/events/" + eventId + "/notes", {
    method: "POST",
    body: JSON.stringify({ content: "Queued while offline", visibility: "private" })
  });
  assert.equal(queued.offline_queued, true);
  const items = await store.listOutbox();
  assert.equal(items.length, 1);
  assert.equal(items[0].baseRevision, 7);
  assert.equal(items[0].kind, "note.create");
  assert.equal(items[0].payload.content, "Queued while offline");

  assert.deepEqual(await api("/events/" + eventId + "/notes"), []);
});
