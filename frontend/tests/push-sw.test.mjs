// Unit tests for public/push-sw.js with a fake service-worker global.
// Run: node --test tests/push-sw.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../public/push-sw.js", import.meta.url), "utf8");

function load(windows = []) {
  const listeners = {};
  const shown = [];
  const opened = [];
  const self = {
    location: { origin: "https://screenmate.example" },
    addEventListener: (type, fn) => { listeners[type] = fn; },
    registration: { showNotification: async (title, opts) => { shown.push({ title, ...opts }); } },
    clients: {
      matchAll: async () => windows,
      openWindow: async (url) => { opened.push(url); },
    },
  };
  vm.runInNewContext(source, { self, URL });
  const fire = async (type, event) => {
    let pending;
    listeners[type]({ ...event, waitUntil: (p) => { pending = p; } });
    await pending;
  };
  return { fire, shown, opened };
}

test("shows the pushed title, body and link", async () => {
  const sw = load();
  const data = { title: "Encoded", body: "Saved 1.2 GB", url: "/activity?open=3" };
  await sw.fire("push", { data: { json: () => data, text: () => JSON.stringify(data) } });
  assert.equal(sw.shown.length, 1);
  assert.equal(sw.shown[0].title, "Encoded");
  assert.equal(sw.shown[0].body, "Saved 1.2 GB");
  assert.equal(sw.shown[0].data.url, "/activity?open=3");
});

test("plain-text pushes still show, with defaults", async () => {
  const sw = load();
  await sw.fire("push", { data: { json: () => { throw new Error("not json"); }, text: () => "hello" } });
  assert.equal(sw.shown[0].title, "MediaMate");
  assert.equal(sw.shown[0].body, "hello");
  assert.equal(sw.shown[0].data.url, "/activity");
});

test("tapping focuses an open MediaMate window and navigates it", async () => {
  const navigated = [];
  let focused = false;
  const win = { url: "https://screenmate.example/library", navigate: async (u) => navigated.push(u), focus: async () => { focused = true; } };
  const sw = load([win]);
  let closed = false;
  await sw.fire("notificationclick", { notification: { close: () => { closed = true; }, data: { url: "/activity?open=3" } } });
  assert.ok(closed && focused);
  assert.deepEqual(navigated, ["https://screenmate.example/activity?open=3"]);
  assert.deepEqual(sw.opened, []);
});

test("tapping opens a new window when none is open", async () => {
  const sw = load([]);
  await sw.fire("notificationclick", { notification: { close() {}, data: {} } });
  assert.deepEqual(sw.opened, ["https://screenmate.example/activity"]);
});
