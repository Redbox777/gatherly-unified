"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const handlers = new Map();
const elements = new Map();
const makeElement = () => {
  const listeners = new Map();
  return {
    checked: false,
    disabled: false,
    hidden: false,
    textContent: "",
    addEventListener(name, callback) { listeners.set(name, callback); },
    async dispatch(name, event = {}) { return listeners.get(name)?.(event); },
    showModal() { this.open = true; },
    close() { this.open = false; }
  };
};
for (const id of [
  "bug-report-open", "bug-report-dialog", "bug-report-form",
  "bug-report-preview", "bug-report-consent", "bug-report-cancel",
  "bug-report-submit", "bug-report-status"
]) elements.set(id, makeElement());

global.window = globalThis;
global.window.addEventListener = (name, callback) => handlers.set(name, callback);
global.document = { getElementById: id => elements.get(id) || null };
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { userAgent: "Mozilla/5.0 Chrome/120.0 Android", onLine: true }
});

require("../../app/interfaces/web/static/diagnostics.js");

test("diagnostics keeps a bounded allowlisted event buffer and sends only reviewed metadata", async () => {
  handlers.get("offline")();
  handlers.get("online")();
  global.GatherlyDiagnostics.recordApiEvent({
    path: "/events/12345678-1234-4234-8234-123456789012/notes",
    method: "POST",
    status_code: 503,
    duration_ms: 135.4,
    request_id: "a".repeat(32)
  });
  global.GatherlyDiagnostics.recordSyncState("conflict");
  handlers.get("error")({
    error: { stack: "Error: secret@example.com\n at https://example.test/private?token=secret:42:9" }
  });

  const initial = global.GatherlyDiagnostics.getEvents();
  assert.equal(initial.length, 5);
  assert.equal(initial[2].route_category, "event_detail");
  assert.equal(initial[2].status_code, 503);
  assert.equal(initial[2].duration_ms, 135);
  assert.equal(initial[2].request_id, "a".repeat(32));
  assert.equal(initial[3].state, "conflict");
  assert.equal(JSON.stringify(initial).includes("12345678"), false);
  assert.equal(JSON.stringify(initial).includes("secret"), false);
  assert.equal(JSON.stringify(initial).includes("example.com"), false);

  for (let index = 0; index < 50; index += 1) {
    global.GatherlyDiagnostics.recordSyncState("sending");
  }
  assert.equal(global.GatherlyDiagnostics.getEvents().length, 40);

  let submitted = null;
  global.fetch = async (_url, options) => {
    submitted = JSON.parse(options.body);
    return { ok: true };
  };
  const openButton = elements.get("bug-report-open");
  await openButton.dispatch("click");
  assert.match(elements.get("bug-report-preview").textContent, /Технические события/);
  elements.get("bug-report-consent").checked = true;
  await elements.get("bug-report-form").dispatch("submit", { preventDefault() {} });

  assert.equal(submitted.consent, true);
  assert.equal(submitted.diagnostics.length, 40);
  assert.equal(JSON.stringify(submitted).includes("secret@example.com"), false);
  assert.equal(elements.get("bug-report-status").textContent, "Спасибо. Технический отчёт отправлен.");
  assert.equal(global.GatherlyDiagnostics.getEvents().length, 0);
});
