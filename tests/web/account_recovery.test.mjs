import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/account-recovery.js", "utf8");
const html = fs.readFileSync("web/index.html", "utf8");
const css = fs.readFileSync("web/account-recovery.css", "utf8");

function harness() {
  const nodes = new Map();
  class Element {
    constructor() {
      this.children = []; this.listeners = {}; this.elements = { reason: { value: "", focus() {} }, username: { value: "", focus() {} } };
      this.dataset = {}; this.value = ""; this.attributes = {};
    }
    append(...children) { this.children.push(...children); }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    replaceChildren() { this.children = []; }
    setAttribute(name, value) { this.attributes[name] = value; }
    scrollIntoView() {}
    focus() { this.focused = true; }
    showModal() { this.open = true; }
    close() { this.open = false; this.listeners.close?.(); }
    reset() {}
  }
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const calls = [];
  const state = {
    AbortController, encodeURIComponent, authReady: true, authGeneration: 1,
    hasRole: role => role === "manager", isSessionCurrent: generation => generation === state.authGeneration,
    document: { getElementById: node, createElement: () => new Element() },
    api: async (...args) => { calls.push(args); return {}; },
  };
  vm.createContext(state); vm.runInContext(source, state);
  return { state, node, calls };
}

test("recovery agent gathers only minimal details and pauses at explicit confirmation", () => {
  const { state, node } = harness();
  state.chooseRecoveryMethod(true);
  assert.equal(node("recovery-agent").hidden, false);
  assert.doesNotMatch(html, /recovery-email-form|email-registration-form|recovery-email-choice|type="email"/);
  assert.equal(node("recovery-agent-input").maxLength, 80);
  assert.equal(node("recovery-agent-input").autocomplete, "username");
  vm.runInContext('recoveryStep = recoveryFields.length; recoveryValues = { username: "demo001" }; renderRecoveryStep();', state);
  assert.equal(node("recovery-review").hidden, false);
  assert.equal(node("recovery-agent-form").hidden, true);
  assert.equal(node("recovery-consent").checked, false);
  assert.equal(node("recovery-review-details").children.length, 12);
  assert.doesNotMatch(source, /citizen_id|document_number|type="file"/);
});

test("manager reset opens same form for a direct account or a recovery request", () => {
  const { state, node } = harness();
  state.openManagerPasswordReset("demo001");
  assert.equal(node("manager-password-dialog").open, true);
  assert.equal(node("manager-recovery-reject").hidden, true);
  state.openManagerPasswordReset("demo002", "request-id");
  assert.equal(node("manager-recovery-reject").hidden, false);
  assert.equal(node("manager-password-result").hidden, true);
  assert.match(node("manager-password-account").textContent, /demo002/);
});

test("recovery never submits without resident consent", async () => {
  const { node, calls } = harness();
  node("recovery-consent").checked = false;
  await node("recovery-send").listeners.click({ currentTarget: node("recovery-send") });
  assert.equal(calls.length, 0);
  assert.match(node("recovery-error").textContent, /xác nhận/);
});

test("closing manager dialog removes the temporary password from the DOM", () => {
  const { state, node } = harness();
  state.openManagerPasswordReset("demo001");
  node("manager-password-secret").value = "one-time-secret";
  node("manager-password-dialog").close();
  assert.equal(node("manager-password-secret").value, "");
});

test("recovery shows errors in Vietnamese without treating text as HTML", () => {
  const { state, node } = harness();
  state.recoveryError("recovery-error", "email_delivery_unavailable");
  assert.match(node("recovery-error").textContent, /dịch vụ email/);
  state.recoveryBubble("<img src=x onerror=alert(1)>", "user");
  assert.equal(node("recovery-messages").children[0].textContent, "<img src=x onerror=alert(1)>");
  assert.doesNotMatch(source, /innerHTML|localStorage|sessionStorage/);
});

test("password recovery opens without requesting email configuration", () => {
  const { node, calls } = harness();
  node("open-password-recovery").listeners.click();
  assert.equal(node("password-recovery-dialog").open, true);
  assert.equal(node("recovery-agent").hidden, false);
  assert.equal(calls.length, 0);
  assert.doesNotMatch(source, /api\("\/auth\/recovery\/config|email-registration-form/);
});

test("password forms and procedure edges remain consistent, without redundant CTA or counts", () => {
  assert.match(html, /id="first-password-submit" class="auth-submit"/);
  assert.doesNotMatch(html, /id="picker-back"/);
  assert.match(html, /id="open-chat-main"/);
  assert.match(html, /Có yêu cầu khác\?/);
  assert.doesNotMatch(fs.readFileSync("web/app.js", "utf8"), /acknowledged_count.*document_count.*tài liệu/);
  for (const status of ["submitted", "needs_info", "approved", "completed"]) assert.ok(css.includes(`data-status="${status}"`));
  assert.match(source, /current !== recoveryController/);
  assert.match(source, /isSessionCurrent\(generation\)/);
});
