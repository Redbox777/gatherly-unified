"use strict";

require("fake-indexeddb/auto");
const test = require("node:test");
const assert = require("node:assert/strict");

const storage = new Map();
global.window = globalThis;
global.localStorage = {
  getItem(key) { return storage.has(key) ? storage.get(key) : null; },
  setItem(key, value) { storage.set(key, String(value)); },
  removeItem(key) { storage.delete(key); }
};
require("../../app/interfaces/web/static/offline-store.js");

const store = global.GatherlyOfflineStore;
const userA = "account-a";
const userB = "account-b";
const eventId = "11111111-1111-4111-8111-111111111111";
const ownerOne = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const ownerTwo = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

test("IndexedDB outbox is durable, account-scoped and protected from accidental discard", async () => {
  await store.setEnabled(true);
  await store.setUser(userA);
  await store.put("GET:/events/" + eventId, { title: "cached" });
  assert.equal(await store.setEventRevision(eventId, 4), true);
  await store.clearCurrentCache();
  assert.equal(await store.get("GET:/events/" + eventId), null);
  assert.equal(await store.getEventRevision(eventId), 4);

  const first = await store.enqueue({
    eventId,
    baseRevision: 4,
    kind: "note.create",
    payload: { content: "Draft", visibility: "private" },
    createdAt: 1000
  });
  assert.match(first.operationId, /^[0-9a-f-]{36}$/i);
  assert.equal(first.state, "pending");
  assert.equal((await store.listOutbox()).length, 1);

  await assert.rejects(
    store.enqueue({ eventId, baseRevision: 4, kind: "finance.create", payload: {} }),
    /Недопустимая офлайн-команда/
  );
  await assert.rejects(
    store.enqueue({ eventId, baseRevision: 4, kind: "task.create", payload: { title: "Task", due_at: "2026-11-15T10:00" } }),
    /часовой пояс/
  );
  const task = await store.enqueue({
    eventId, baseRevision: 5, kind: "task.create", payload: { title: "Task" }, createdAt: 2000
  });
  assert.equal(task.payload.title, "Task");
  const datedTask = await store.enqueue({
    eventId, baseRevision: 6, kind: "task.create",
    payload: { title: "Dated task", due_at: "2026-11-15T10:00:00+03:00" }, createdAt: 2500
  });
  assert.equal(datedTask.payload.due_at, "2026-11-15T10:00:00+03:00");

  await store.setUser(null);
  await store.setUser(userB);
  assert.equal((await store.listOutbox()).length, 0);
  await store.setUser(userA);
  assert.equal((await store.listOutbox()).length, 2);

  assert.equal(await store.acquireLease(eventId, ownerOne), true);
  assert.equal(await store.acquireLease(eventId, ownerTwo), false);
  await store.releaseLease(eventId, ownerOne);
  assert.equal(await store.acquireLease(eventId, ownerTwo), true);
  await store.releaseLease(eventId, ownerTwo);

  await store.updateOutboxState(first.operationId, "sending");
  await store.recoverSending();
  const recovered = (await store.listOutbox()).find(item => item.operationId === first.operationId);
  assert.equal(recovered.state, "pending");
  assert.equal(recovered.attempts, 1);

  const next = (await store.listOutbox()).find(item => item.operationId === task.operationId);
  await store.rebasePendingEvent(eventId, first.createdAt, 12);
  const rebased = (await store.listOutbox()).find(item => item.operationId === next.operationId);
  assert.equal(rebased.baseRevision, 12);

  await assert.rejects(store.setEnabled(false), /несинхронизированные изменения/);
  await store.setEnabled(false, { discardPending: true });
  await store.setEnabled(true);
  assert.equal((await store.listOutbox()).length, 0);
});
