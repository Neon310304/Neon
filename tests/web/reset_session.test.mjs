import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/reset-session.js", "utf8");

function browser({ status = 200, ready = true, manager = true, mode = "required" } = {}) {
  const events = {};
  const redirects = [];
  const calls = [];
  let cleared = 0;
  let poll;
  const context = vm.createContext({
    authReady: ready, authGeneration: 1, authConfig: { auth_mode: mode },
    API: "/api/v1", MANAGEMENT_CURSOR_KEY: "cursor",
    clearStoredSession: () => { cleared += 1; },
    hasRole: (role) => manager && role === "manager",
    authorizedFetch: async (...args) => { calls.push(args); return { status }; },
    localStorage: { removeItem() {} },
    document: { visibilityState: "visible", addEventListener: (name, handler) => { events[name] = handler; } },
    window: {
      addEventListener: (name, handler) => { events[name] = handler; },
      setInterval: (handler) => { poll = handler; },
      location: { pathname: "/", replace: (path) => redirects.push(path) },
    },
  });
  vm.runInContext(source, context);
  return { events, redirects, calls, context, cleared: () => cleared, poll: () => poll() };
}

test("Reset in another tab clears the session and all on-screen conversations by reloading", () => {
  const page = browser();
  page.events.storage({ key: "unrelated", newValue: "change" });
  assert.equal(page.cleared(), 0);
  page.events.storage({ key: "moveinmate.reset.event", newValue: "new-generation" });
  assert.equal(page.cleared(), 1);
  assert.deepEqual(page.redirects, ["/"]);
});

test("manager session check detects Reset from another device", async () => {
  const page = browser({ status: 401 });
  page.poll();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(page.calls[0][0], "/api/v1/me/context");
  assert.equal(page.cleared(), 1);
  assert.deepEqual(page.redirects, ["/"]);
});

test("temporary server failures do not sign users out", async () => {
  const page = browser({ status: 503 });
  page.poll();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(page.cleared(), 0);
});

test("session checks ignore signed-out users and explicit development demo mode", async () => {
  for (const options of [{ ready: false }, { mode: "demo" }]) {
    const page = browser(options);
    page.poll();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(page.calls.length, 0);
  }
});

test("a late response from a previous login cannot log out the current login", async () => {
  const page = browser({ status: 401 });
  page.poll();
  page.context.authGeneration += 1;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(page.cleared(), 0);
});
