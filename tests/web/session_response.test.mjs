import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const transport = source.slice(source.indexOf("async function api("), source.indexOf("function a2uiValue("));

test("suspended membership returns to login and clears the session only for the current response", async () => {
  const fetchSource = source.slice(source.indexOf("async function authorizedFetch("), source.indexOf("function presentAuthError("));
  const logins = [];
  let finish;
  const state = {
    URL, API: "/api/v1", authGeneration: 1, authReady: true, authAbortController: new AbortController(),
    window: { location: { origin: "http://localhost:8000" } },
    cloneRequestOptions: options => ({ ...options }), fetch: () => new Promise(resolve => { finish = resolve; }),
    showLogin: message => logins.push(message),
  };
  vm.createContext(state);
  vm.runInContext(fetchSource, state);
  const pending = state.authorizedFetch("/api/v1/me/context");
  finish(new Response(JSON.stringify({ detail: { code: "membership_required" } }), { status: 403 }));
  assert.equal((await pending).status, 403);
  assert.match(logins[0], /tạm ngừng sử dụng.*Ban quản lý/);
  const stale = state.authorizedFetch("/api/v1/me/context");
  state.authGeneration += 1;
  finish(new Response(JSON.stringify({ detail: { code: "membership_required" } }), { status: 403 }));
  await stale;
  assert.equal(logins.length, 1);
});

for (const name of ["api", "streamApi", "streamAGUI"]) {
  test(`${name}: stale unauthorized responses cannot dismiss password success or a newer login`, async () => {
    let finish;
    let logins = 0;
    const state = { API: "/api/v1", authGeneration: 1, authConfig: { auth_mode: "required" }, apiErrorLabels: {},
      authorizedFetch: () => new Promise(resolve => { finish = resolve; }), showLogin: () => { logins += 1; } };
    vm.createContext(state);
    vm.runInContext(transport, state);
    const request = state[name]("/test");
    state.authGeneration += 1;
    finish(new Response(JSON.stringify({ detail: "session_expired" }), { status: 401 }));
    await assert.rejects(request);
    assert.equal(logins, 0);
    const current = state[name]("/test");
    finish(new Response(JSON.stringify({ detail: "session_expired" }), { status: 401 }));
    await assert.rejects(current);
    assert.equal(logins, 1);
  });
}

test("a delayed health check does not close chat that the resident has already opened", async () => {
  const start = source.indexOf("async function startAuthorizedBackgroundTasks(");
  const end = source.indexOf('document.getElementById("login-form").addEventListener', start);
  let finishHealth;
  let chatOpen = false;
  let pickerOpens = 0;
  const state = { authReady: true, notificationIntervalId: 1, ownershipRequired: () => false,
    loadHealth: () => new Promise(resolve => { finishHealth = resolve; }), hasRole: () => true,
    document: { getElementById: id => ({ classList: { contains: () => id === "agent" || chatOpen } }) },
    openProcedurePicker: () => { pickerOpens += 1; }, loadHousehold() {}, pollManagementNotifications() {},
  };
  vm.createContext(state);
  vm.runInContext(source.slice(start, end), state);
  const pending = state.startAuthorizedBackgroundTasks();
  chatOpen = true;
  finishHealth();
  await pending;
  assert.equal(pickerOpens, 0);
});
