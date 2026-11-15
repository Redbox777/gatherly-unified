"use strict";

require("fake-indexeddb/auto");
const test = require("node:test");
const assert = require("node:assert/strict");

const values = new Map();
const diagnosticStates = [];
global.GatherlyDiagnostics = { recordSyncState(state) { diagnosticStates.push(state); } };
global.window = globalThis;
global.localStorage = {
  getItem(key) { return values.has(key) ? values.get(key) : null; },
  setItem(key, value) { values.set(key, String(value)); },
  removeItem(key) { values.delete(key); }
};
global.window.addEventListener = () => {};
global.window.confirm = () => true;
const elements = new Map();
const element = () => ({
  hidden: false,
  disabled: false,
  textContent: "",
  className: "",
  children: [],
  addEventListener() {},
  replaceChildren() { this.children = []; },
  append(...items) { this.children.push(...items); }
});
for (const id of ["offline-sync-panel", "offline-sync-count", "offline-sync-status", "offline-sync-list", "offline-sync-now"]) {
  elements.set(id, element());
}
global.document = {
  getElementById(id) { return elements.get(id) || null; },
  createElement() { return element(); }
};
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    onLine: true,
    locks: { request: async (_name, _options, callback) => callback({ name: "test-lock" }) }
  }
});
require("../../app/interfaces/web/static/offline-store.js");

const store = global.GatherlyOfflineStore;
const eventOne = "11111111-1111-4111-8111-111111111111";
const eventTwo = "22222222-2222-4222-8222-222222222222";

test("foreground sender applies in order and pauses only a conflicted event", async () => {
  await store.setEnabled(true);
  await store.setUser("sender-account");
  await store.setEventRevision(eventOne, 1);
  await store.setEventRevision(eventTwo, 1);
  await store.enqueue({
    eventId: eventOne, baseRevision: 1, kind: "note.create",
    payload: { content: "Applied once", visibility: "private" }, createdAt: 1000
  });
  const conflict = await store.enqueue({
    eventId: eventTwo, baseRevision: 1, kind: "task.create",
    payload: { title: "Conflicting task" }, createdAt: 2000
  });
  const later = await store.enqueue({
    eventId: eventTwo, baseRevision: 2, kind: "task.complete",
    payload: { task_id: "33333333-3333-4333-8333-333333333333", completed: true }, createdAt: 3000
  });

  const sent = [];
  global.fetch = async (url, options = {}) => {
    if (url === "/auth/me") {
      return new Response(JSON.stringify({ user_id: "sender-account" }), { status: 200 });
    }
    if (url === "/offline-sync/commands") {
      const body = JSON.parse(options.body);
      sent.push(body);
      if (body.event_id === eventOne) {
        return new Response(JSON.stringify({ status: "applied", revision: 2 }), {
          status: 201, headers: { "Content-Type": "application/json" }
        });
      }
      return new Response(JSON.stringify({ detail: "Event revision conflict", current_revision: 4 }), {
        status: 409, headers: { "Content-Type": "application/json" }
      });
    }
    throw new Error("Unexpected fetch URL: " + url);
  };

  require("../../app/interfaces/web/static/offline-sync.js");
  await global.GatherlyOfflineSync.syncNow();

  assert.equal(sent.length, 2);
  assert.equal(sent[0].event_id, eventOne);
  assert.equal(sent[1].operation_id, conflict.operationId);
  assert.equal((await store.listOutbox()).some(item => item.eventId === eventOne), false);
  const remaining = (await store.listOutbox()).filter(item => item.eventId === eventTwo);
  assert.equal(remaining.find(item => item.operationId === conflict.operationId).state, "conflict");
  assert.equal(remaining.find(item => item.operationId === later.operationId).state, "pending");
  assert.equal(await store.getEventRevision(eventOne), 2);
  assert.equal(await store.getEventRevision(eventTwo), 4);
  assert.match(elements.get("offline-sync-status").textContent, /завершена частично/i);
  assert.ok(diagnosticStates.includes("sending"));
  assert.ok(diagnosticStates.includes("done"));
  assert.ok(diagnosticStates.includes("conflict"));
});


test("reapplying a conflicted command uses the current server revision before rebasing later commands", async () => {
  const conflict = (await store.listOutbox()).find(item => item.eventId === eventTwo && item.state === "conflict");
  const later = (await store.listOutbox()).find(item => item.eventId === eventTwo && item.state === "pending");
  assert.ok(conflict);
  assert.ok(later);
  global.fetch = async url => {
    if (url === "/auth/me") {
      return new Response(JSON.stringify({ user_id: "sender-account" }), { status: 200 });
    }
    if (url === "/events/" + eventTwo + "/sync-state") {
      return new Response(JSON.stringify({ event_id: eventTwo, revision: 4 }), {
        status: 200, headers: { "Content-Type": "application/json" }
      });
    }
    throw new Error("Unexpected fetch URL: " + url);
  };
  const previousConfirm = global.window.confirm;
  global.window.confirm = () => true;
  try {
    await global.GatherlyOfflineSync.reapplyItem(conflict);
  } finally {
    global.window.confirm = previousConfirm;
  }
  const remaining = (await store.listOutbox()).filter(item => item.eventId === eventTwo);
  const replacement = remaining.find(item => item.operationId !== later.operationId);
  const rebasedLater = remaining.find(item => item.operationId === later.operationId);
  assert.ok(replacement);
  assert.notEqual(replacement.operationId, conflict.operationId);
  assert.equal(replacement.baseRevision, 4);
  assert.equal(rebasedLater.baseRevision, 5);
  assert.equal(replacement.state, "pending");
});
