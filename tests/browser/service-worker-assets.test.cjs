const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const staticDirectory = path.join(
  __dirname,
  "../../app/interfaces/web/static"
);

test("service worker precaches every script loaded by the main app shell", () => {
  const html = fs.readFileSync(path.join(staticDirectory, "index.html"), "utf8");
  const serviceWorker = fs.readFileSync(
    path.join(staticDirectory, "service-worker.js"),
    "utf8"
  );
  const scriptPaths = [...html.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["']/gi)]
    .map(match => match[1])
    .filter(source => source.startsWith("./"))
    .map(source => "/" + source.slice(2));
  const shellBlock = serviceWorker.match(
    /const SHELL_ASSETS = \[([\s\S]*?)\];/
  );

  assert.ok(shellBlock, "service worker must declare SHELL_ASSETS");
  const cachedPaths = new Set(
    [...shellBlock[1].matchAll(/["'](\/[^"']+)["']/g)]
      .map(match => match[1])
  );
  const missingPaths = scriptPaths.filter(source => !cachedPaths.has(source));

  assert.deepEqual(
    missingPaths,
    [],
    "every script loaded by index.html must be available in the offline shell cache"
  );
});
