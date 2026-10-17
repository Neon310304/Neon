import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/login-reset.js", "utf8");

function browser({ success = true, detail = "reset_code_invalid", ready = true, manager = true } = {}) {
  const elements = new Map();
  const calls = [];
  const locations = [];
  let sessionsCleared = 0;
  for (const name of ["login-reset", "login-reset-dialog", "login-reset-form", "reset-code", "reset-confirm", "reset-submit", "reset-cancel", "reset-error", "login-reset-notice"]) {
    elements.set(name, {
      hidden: false, disabled: false, value: "", checked: false, open: false,
      listeners: {}, focus() {}, setAttribute() {}, removeAttribute() {},
      reportValidity: () => true,
      addEventListener(event, handler) { this.listeners[event] = handler; },
      showModal() { this.open = true; }, close() { this.open = false; }, reset() {},
    });
  }
  vm.runInNewContext(source, {
    authReady: ready, hasRole: role => manager && role === "manager",
    document: { getElementById: (name) => elements.get(name) },
    authorizedFetch: async (path, options) => {
      calls.push({ path, ...options });
      return { ok: success, json: async () => success ? { reset: true } : { detail } };
    },
    clearStoredSession: () => { sessionsCleared += 1; },
    MANAGEMENT_CURSOR_KEY: "cursor",
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    localStorage: { removeItem() {} },
    window: { location: { pathname: "/", replace: (path) => locations.push(path) } },
  });
  const element = (name) => elements.get(name);
  const submit = () => element("login-reset-form").listeners.submit({ preventDefault() {} });
  return { calls, locations, element, submit, sessionsCleared: () => sessionsCleared };
}

test("Reset requires entered code and unchecked confirmation", async () => {
  const page = browser();
  page.element("reset-code").value = "entered-code";
  await page.submit();
  assert.equal(page.calls.length, 0);
});

test("successful Reset clears session and reloads to clear chat presentation", async () => {
  const page = browser();
  page.element("reset-code").value = "entered-code";
  page.element("reset-confirm").checked = true;
  await page.submit();
  assert.equal(page.calls[0].path, "/api/v1/management/reset");
  assert.deepEqual(JSON.parse(page.calls[0].body), { code: "entered-code", confirm: true });
  assert.equal(page.sessionsCleared(), 1);
  assert.deepEqual(page.locations, ["/"]);
  assert.equal(page.element("reset-code").value, "");
});

test("failed Reset preserves session and displays a useful error", async () => {
  const page = browser({ success: false });
  page.element("reset-code").value = "incorrect";
  page.element("reset-confirm").checked = true;
  await page.submit();
  assert.equal(page.sessionsCleared(), 0);
  assert.equal(page.locations.length, 0);
  assert.match(page.element("reset-error").textContent, /không đúng/);
  assert.equal(page.element("reset-submit").disabled, false);
});

test("cancel does not send any request and source never contains the configured code", () => {
  const page = browser();
  page.element("login-reset").listeners.click();
  page.element("reset-cancel").listeners.click();
  assert.equal(page.element("login-reset-dialog").open, false);
  assert.equal(page.calls.length, 0);
  assert.equal(source.includes("0000"), false);
  const html = fs.readFileSync("web/index.html", "utf8");
  assert.match(html, /id="login-reset"[^>]*hidden><span[^>]*>↺<\/span> Reset<\/button>/);
  assert.match(html, /aria-describedby="reset-description"/);
  assert.match(html, /Khôi phục demo001–demo010 về tên đăng nhập và mật khẩu ban đầu/);
  assert.match(html, /user\/admin dùng tài khoản và mật khẩu cấu hình sẵn/);
  assert.doesNotMatch(html, /vuongtq/);
  assert.match(html, /Tài khoản tạo thêm ngoài danh sách được giữ sẽ bị xóa/);
  assert.match(html, /đoạn chat của mọi tài khoản/);
  assert.doesNotMatch(html, /cư dân khác được giữ nguyên/);
});

test("Reset stays at the bottom left of the app, outside every feature and login screen", () => {
  const html = fs.readFileSync("web/index.html", "utf8");
  const header = html.slice(html.indexOf("<header>"), html.indexOf("</header>"));
  assert.equal((html.match(/id="login-reset"/g) || []).length, 1);
  assert.doesNotMatch(header, /id="login-reset"/);
  assert.match(html, /<footer id="application-footer"[^>]*hidden>\s*<button id="login-reset"/);
  assert.ok(html.indexOf('id="login-reset"') > html.indexOf('id="auth-screen"'));
  const css = fs.readFileSync("web/portal.css", "utf8");
  assert.match(css, /\.application-footer\s*\{[^}]*justify-content: flex-start/);
  assert.match(css, /\.application-footer \.page-reset\s*\{[^}]*color: #a12626/);
});

test("disabled Reset cannot open the dialog or send a request", () => {
  const page = browser();
  page.element("login-reset").disabled = true;
  page.element("login-reset").listeners.click();
  assert.equal(page.element("login-reset-dialog").open, false);
  assert.equal(page.calls.length, 0);
});

test("public auth configuration never exposes or enables Reset", async () => {
  const appSource = fs.readFileSync("web/app.js", "utf8");
  const authSource = appSource.slice(appSource.indexOf("async function loadAuthConfig()"), appSource.indexOf("function loginHintField"));
  for (const available of [true, false, undefined]) {
    const button = { hidden: false, disabled: true };
    const context = vm.createContext({
      API: "/api/v1", authConfig: null,
      authAbortController: { signal: undefined },
      document: { getElementById: () => button },
      fetch: async () => ({ ok: true, json: async () => ({ reset_available: available }) }),
    });
    vm.runInContext(authSource, context);
    await context.loadAuthConfig();
    assert.equal(button.disabled, true);
    assert.equal(button.hidden, true);
  }
});

test("lost authentication or resident role cannot submit a pending Reset", async () => {
  for (const settings of [{ ready: false }, { manager: false }]) {
    const page = browser(settings);
    page.element("reset-code").value = "entered-code";
    page.element("reset-confirm").checked = true;
    await page.submit();
    assert.equal(page.calls.length, 0);
    assert.equal(page.sessionsCleared(), 0);
  }
});
