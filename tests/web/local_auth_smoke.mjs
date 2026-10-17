import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import vm from "node:vm";

const environment = Object.fromEntries(
  fs.readFileSync(".env", "utf8").split(/\r?\n/)
    .filter((line) => /^(LOCAL_AUTH_[A-Z_]+|BACKEND_PORT|APP_ENV|SHOW_LOCAL_LOGIN_CREDENTIALS)=/.test(line))
    .map((line) => {
      const separator = line.indexOf("=");
      return [line.slice(0, separator), line.slice(separator + 1).replace(/^(["'])(.*)\1$/, "$2")];
    }),
);
const origin = `http://127.0.0.1:${environment.BACKEND_PORT || 8000}`;
const source = fs.readFileSync("web/app.js", "utf8");
const html = fs.readFileSync("web/index.html", "utf8");
assert.match(html, /<button id="tab-resident-accounts"[^>]*data-tab="resident-accounts"[^>]*hidden>/);
assert.ok(!/supabase|jwks|refresh_token/i.test(source));
const boundary = source.indexOf('document.getElementById("login-form").addEventListener');
assert.ok(boundary > 0);
const navigationStart = source.indexOf('document.querySelectorAll(".tabs button").forEach((button) => {', boundary);
const navigationEnd = source.indexOf("const initialTab =", navigationStart);
const shortcutStart = source.indexOf('document.querySelectorAll(".tab-shortcut").forEach', boundary);
const shortcutEnd = source.indexOf("async function loadProgress", shortcutStart);
assert.ok(navigationStart > boundary && navigationEnd > navigationStart);
assert.ok(shortcutStart > boundary && shortcutEnd > shortcutStart);
const calls = [];

function storage(values = new Map()) {
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

function element(id) {
  const classes = new Set();
  const listeners = new Map();
  const attributes = new Map();
  return {
    id, hidden: false, disabled: false, textContent: "", value: "",
    children: [], selected: false,
    dataset: { tab: id.replace("tab-", "") },
    classList: {
      contains: (value) => classes.has(value),
      add: (value) => classes.add(value),
      remove: (value) => classes.delete(value),
      toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value),
    },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    addEventListener(name, callback) { listeners.set(name, callback); },
    setAttribute(name, value) { attributes.set(name, value); },
    getAttribute(name) { return attributes.get(name); },
    focus() {}, scrollIntoView() {},
    select() { this.selected = true; },
    async dispatch(name, event) { return listeners.get(name)?.(event); },
    async click() { return listeners.get("click")?.(); },
  };
}

function createClient(sessionStorage = storage()) {
  const elements = new Map();
  const clipboard = [];
  const tabLoads = [];
  let focusedElement = null;
  const getElement = (id) => {
    if (!elements.has(id)) {
      const item = element(id);
      item.focus = () => { focusedElement = item; };
      elements.set(id, item);
    }
    return elements.get(id);
  };
  const tabs = ["agent", "progress", "resident-accounts", "tickets", "household", "regulations", "management"];
  getElement("tab-resident-accounts").dataset = { tab: "resident-accounts" };
  getElement("hero-secondary-action").dataset = { tabTarget: "progress" };
  const context = vm.createContext({
    crypto: { randomUUID },
    navigator: { clipboard: { writeText: async (value) => clipboard.push(value) } },
    Headers, AbortController, console, tabLoads,
    localStorage: storage(), sessionStorage,
    window: { location: { origin, pathname: "/", assign() {} } },
    document: {
      get activeElement() { return focusedElement; },
      getElementById: getElement,
      createElement: (name) => element(name),
      querySelectorAll: (selector) => selector === ".tabs button"
        ? tabs.map((tab) => getElement(`tab-${tab}`))
        : selector === ".tab-panel" ? tabs.map(getElement)
        : selector === ".tab-shortcut" ? [getElement("hero-secondary-action")] : [],
      querySelector: (selector) => {
        const match = selector.match(/^\.tabs button\[data-tab="([^"]+)"\]$/);
        return match ? getElement(`tab-${match[1]}`) : getElement(selector);
      },
    },
    fetch: async (url, options) => {
      const target = new URL(url, origin);
      assert.equal(target.origin, origin);
      calls.push(target.pathname);
      return fetch(target, options);
    },
  });
  vm.runInContext(source.slice(0, boundary), context);
  vm.runInContext("activateInitialTab = () => {}; startAuthorizedBackgroundTasks = async () => {};", context);
  vm.runInContext(`
    loadProgress = () => tabLoads.push("progress");
    loadResidentTickets = () => tabLoads.push("tickets");
    loadHousehold = () => tabLoads.push("household");
    loadRegulations = () => tabLoads.push("regulations");
    loadRequests = () => tabLoads.push("management");
    loadAccountWorkspace = () => tabLoads.push("resident-accounts");
  `, context);
  vm.runInContext(source.slice(navigationStart, navigationEnd), context);
  vm.runInContext(source.slice(shortcutStart, shortcutEnd), context);
  return { context, elements, sessionStorage, clipboard, tabLoads };
}

for (const [usernameKey, passwordKey, expectedRole] of [
  ["LOCAL_AUTH_ADMIN_USERNAME", "LOCAL_AUTH_ADMIN_PASSWORD", "manager"],
  ["LOCAL_AUTH_USER_USERNAME", "LOCAL_AUTH_USER_PASSWORD", "resident"],
]) {
  const client = createClient();
  await vm.runInContext("loadAuthConfig()", client.context);
  await vm.runInContext("loadLoginHints()", client.context);
  const showHints = environment.SHOW_LOCAL_LOGIN_CREDENTIALS === "true"
    && ["development", "test"].includes(environment.APP_ENV);
  assert.equal(client.elements.get("login-hints").hidden, !showHints);
  if (showHints) {
    const accounts = client.elements.get("login-hint-accounts").children;
    assert.equal(accounts.length, 2);
    const account = accounts[expectedRole === "manager" ? 0 : 1];
    await account.children[0].children[1].click();
    assert.equal(client.elements.get("login-username").value, environment[usernameKey]);
    assert.equal(client.elements.get("login-password").value, environment[passwordKey]);
    await account.children[1].children[2].click();
    await account.children[2].children[2].click();
    assert.deepEqual(client.clipboard, [environment[usernameKey], environment[passwordKey]]);
    client.context.navigator.clipboard.writeText = async () => { throw new Error("permission-denied"); };
    await account.children[2].children[2].click();
    assert.equal(account.children[2].children[1].selected, true);
    assert.match(client.elements.get("login-hints-status").textContent, /Ctrl\+C/);
  }
  const form = {
    elements: { username: { value: environment[usernameKey] }, password: { value: "incorrect-password" } },
    reset() { this.elements.password.value = ""; },
  };
  assert.ok(environment[passwordKey]);
  client.context.event = { preventDefault() {}, currentTarget: form };
  await vm.runInContext("handleLoginSubmit(event)", client.context);
  assert.match(client.elements.get("auth-error").textContent, /không đúng/);
  assert.equal(client.elements.get("login-submit").disabled, false);
  form.elements.password.value = environment[passwordKey];
  await vm.runInContext("handleLoginSubmit(event)", client.context);
  assert.equal(vm.runInContext("authContext.roles[0]", client.context), expectedRole);
  assert.equal(client.elements.get("auth-screen").hidden, true);
  assert.equal(client.elements.get("main-content").hidden, false);
  assert.equal(client.elements.get("tab-management").hidden, expectedRole !== "manager");
  assert.equal(client.elements.get("demo-role-wrap").hidden, true);
  const resetEnabled = vm.runInContext('hasRole("manager") && authContext.demo_reset_enabled === true', client.context);
  assert.equal(client.elements.get("reset-app").hidden, !resetEnabled);
  const manager = expectedRole === "manager";
  const demoTab = client.elements.get("tab-resident-accounts");
  const shortcut = client.elements.get("hero-secondary-action");
  assert.equal(demoTab.hidden, !manager);
  assert.equal(demoTab.disabled, !manager);
  assert.equal(shortcut.disabled, false);
  assert.equal(shortcut.getAttribute("aria-disabled"), "false");
  assert.equal(client.elements.get("hero-secondary-demo").hidden, true);
  assert.equal(client.elements.get("hero-secondary-label").textContent, manager ? "Tạo tài khoản cư dân" : "Xem việc cần làm");
  assert.equal(client.elements.get("open-chat-hero").hidden, manager);
  assert.equal(client.elements.get("hero-heading-lead").textContent, manager ? "Mỗi yêu cầu," : "Chuyển vào căn hộ,");
  assert.equal(client.elements.get("hero-heading-accent").textContent, manager ? "một bước rõ ràng." : "từng bước một.");
  const activeTab = client.elements.get(manager ? "tab-management" : "tab-agent");
  await activeTab.click();
  const loadsBeforeDemoClick = client.tabLoads.length;
  const callsBeforeDemoClick = calls.length;
  await demoTab.click();
  if (manager) {
    await shortcut.click();
    await client.elements.get("tab-progress").click();
  } else {
    await client.elements.get("tab-management").click();
  }
  assert.equal(client.tabLoads.length, loadsBeforeDemoClick + (manager ? 2 : 0));
  assert.equal(calls.length, callsBeforeDemoClick);
  assert.equal(activeTab.classList.contains("active"), !manager);
  assert.equal(demoTab.classList.contains("active"), manager);
  for (const key of ["Home", "End", "ArrowRight", "ArrowLeft"]) {
    activeTab.focus();
    await client.elements.get(".tabs").dispatch("keydown", { key, preventDefault() {} });
    const focused = client.context.document.activeElement;
    assert.equal(focused.hidden, false);
    assert.equal(focused.disabled, false);
    if (manager) assert.ok(["tab-management", "tab-resident-accounts"].includes(focused.id));
  }
  if (!manager) {
    await shortcut.click();
    assert.equal(client.elements.get("progress").hidden, false);
    assert.equal(client.elements.get("tab-progress").getAttribute("aria-selected"), "true");
    assert.equal(client.tabLoads.at(-1), "progress");
  }
  const otherRole = manager ? "resident" : "manager";
  vm.runInContext(`authContext.roles = ["${otherRole}"]; setAuthenticatedUi();`, client.context);
  assert.equal(shortcut.disabled, false);
  assert.equal(demoTab.hidden, manager);
  assert.equal(client.elements.get("hero-secondary-demo").hidden, true);
  vm.runInContext(`authContext.roles = ["${expectedRole}"]; setAuthenticatedUi();`, client.context);
  const token = vm.runInContext("authSession.access_token", client.context);
  const reload = createClient(client.sessionStorage);
  await vm.runInContext("loadAuthConfig();", reload.context);
  assert.equal(await vm.runInContext("authSession = readStoredSession(); completeRequiredAuthentication();", reload.context), true);
  assert.equal(vm.runInContext("authContext.roles[0]", reload.context), expectedRole);
  await vm.runInContext("signOut()", reload.context);
  assert.equal(vm.runInContext("readStoredSession()", reload.context), null);
  const denied = await fetch(`${origin}/api/v1/me/context`, { headers: { Authorization: `Bearer ${token}` } });
  assert.equal(denied.status, 401);
  console.log(`PASS client login / scoped account tab / role UI / tab navigation / reload / logout: ${expectedRole}`);
}
assert.ok(calls.includes("/api/v1/auth/login"));
assert.ok(calls.includes("/api/v1/auth/logout"));
console.log("Client JavaScript smoke checks pass against live Docker API; no external authentication calls.");
