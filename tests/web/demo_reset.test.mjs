import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/login-reset.js", "utf8");

function client({ ready = true, manager = true, enabled = true, disabled = false, failure = false, storageFailure = false } = {}) {
  const calls = [];
  const redirects = [];
  const removed = [];
  const nodes = new Map();
  const node = name => {
    if (!nodes.has(name)) nodes.set(name, {
      hidden: false, disabled: false, value: "", checked: false, open: false, textContent: "",
      listeners: {}, focus() {}, setAttribute() {}, removeAttribute() {}, reportValidity: () => true,
      addEventListener(event, handler) { this.listeners[event] = handler; },
      showModal() { this.open = true; }, close() { this.open = false; this.listeners.close?.(); }, reset() {},
    });
    return nodes.get(name);
  };
  node("login-reset").hidden = !enabled;
  node("login-reset").disabled = disabled;
  const context = vm.createContext({
    authReady: ready, hasRole: role => manager && role === "manager",
    MANAGEMENT_CURSOR_KEY: "cursor", crypto: { randomUUID: () => "reset-event" },
    document: { getElementById: node },
    authorizedFetch: async (path, options) => {
      calls.push({ path, method: options.method, payload: JSON.parse(options.body) });
      if (failure) throw new Error("Không thể reset");
      return { ok: true, json: async () => ({ reset: true }) };
    },
    clearStoredSession: () => removed.push("session"),
    localStorage: {
      removeItem: key => { if (storageFailure) throw new Error("Storage unavailable"); removed.push(key); },
      setItem() { if (storageFailure) throw new Error("Storage unavailable"); },
    },
    sessionStorage: { getItem: () => null, setItem() { if (storageFailure) throw new Error("Storage unavailable"); } },
    window: { location: { pathname: "/", replace: path => redirects.push(path) } },
  });
  vm.runInContext(source, context);
  const open = () => node("login-reset").listeners.click();
  const submit = () => node("login-reset-form").listeners.submit({ preventDefault() {} });
  const reset = () => { open(); node("reset-code").value = "entered-code"; node("reset-confirm").checked = true; return submit(); };
  return { calls, redirects, removed, context, node, open, submit, reset };
}

for (const options of [{ ready: false }, { manager: false }, { enabled: false }, { disabled: true }]) {
  test(`does not open Reset without permission or availability: ${JSON.stringify(options)}`, () => {
    const browser = client(options);
    browser.open();
    assert.equal(browser.node("login-reset-dialog").open, false);
    assert.deepEqual(browser.calls, []);
  });
}

test("cancelling confirmation leaves all state unchanged", () => {
  const browser = client();
  browser.open();
  browser.node("reset-code").value = "entered-code";
  browser.node("reset-cancel").listeners.click();
  assert.equal(browser.node("login-reset-dialog").open, false);
  assert.equal(browser.node("reset-code").value, "");
  assert.deepEqual(browser.calls, []);
  assert.deepEqual(browser.removed, []);
  assert.deepEqual(browser.redirects, []);
});

test("successful reset posts code and consent once, revokes session and reloads", async () => {
  const browser = client();
  await browser.reset();
  assert.deepEqual(browser.calls, [{ path: "/api/v1/management/reset", method: "POST", payload: { code: "entered-code", confirm: true } }]);
  assert.deepEqual(browser.removed, ["session", "cursor"]);
  assert.deepEqual(browser.redirects, ["/"]);
});

test("network failure preserves session, clears the secret and allows explicit retry", async () => {
  const browser = client({ failure: true });
  await browser.reset();
  assert.equal(browser.node("reset-submit").disabled, false);
  assert.equal(browser.node("reset-error").textContent, "Không thể reset");
  assert.equal(browser.node("reset-code").value, "");
  assert.deepEqual(browser.removed, []);
  assert.deepEqual(browser.redirects, []);
});

test("successful reset still reloads if optional storage is unavailable", async () => {
  const browser = client({ storageFailure: true });
  await browser.reset();
  assert.deepEqual(browser.redirects, ["/"]);
});

test("repeated clicks and cancellation cannot race an in-flight Reset", async () => {
  const browser = client();
  let complete;
  let calls = 0;
  browser.context.authorizedFetch = () => { calls += 1; return new Promise(resolve => { complete = resolve; }); };
  const pending = browser.reset();
  assert.equal(browser.node("reset-submit").disabled, true);
  browser.node("reset-cancel").listeners.click();
  assert.equal(browser.node("login-reset-dialog").open, true);
  await browser.submit();
  assert.equal(calls, 1);
  complete({ ok: true, json: async () => ({ reset: true }) });
  await pending;
  assert.deepEqual(browser.redirects, ["/"]);
});
