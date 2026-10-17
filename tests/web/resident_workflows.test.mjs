import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const workflows = fs.readFileSync("web/resident-workflows.js", "utf8");
const html = fs.readFileSync("web/index.html", "utf8");
const portal = fs.readFileSync("web/portal.js", "utf8");

test("ticket query includes date and type filters and an independent page per role", () => {
  const fields = new Map([
    ["request-search", "  biển số  "], ["request-type-filter", "vehicle_registration"],
    ["request-status-filter", "needs_info"], ["request-date-from", "2026-09-01"],
    ["request-date-to", "2026-10-01"], ["resident-ticket-search", "REQ-123"],
    ["resident-ticket-type-filter", "all"], ["resident-ticket-status-filter", "all"],
    ["resident-ticket-date-from", ""], ["resident-ticket-date-to", ""],
  ]);
  const start = workflows.indexOf("function ticketQuery(");
  const end = workflows.indexOf("function updateTicketPagination(", start);
  const query = vm.runInNewContext(`${workflows.slice(start, end)}\nticketQuery;`, {
    document: { getElementById: id => ({ value: fields.get(id) }) },
    managementPage: { offset: 40, limit: 20 }, residentTicketPage: { offset: 0, limit: 20 }, URLSearchParams,
    ticketStages: { manager: "active", resident: "active" },
    ticketStatusInStage: status => !["completed", "rejected", "cancelled_by_resident"].includes(status),
  });
  const manager = new URLSearchParams(query("manager"));
  assert.equal(manager.get("offset"), "40");
  assert.equal(manager.get("date_from"), "2026-09-01");
  assert.equal(manager.get("q"), "biển số");
  assert.equal(manager.get("request_type"), "vehicle_registration");
  const resident = new URLSearchParams(query("resident"));
  assert.equal(resident.get("offset"), "0");
  assert.equal(resident.get("status"), "active");
  assert.equal(resident.has("date_from"), false);
});

test("vehicle form has a mandatory attachment and separate optional image-processing consent", () => {
  assert.match(html, /id="vehicle-document" type="file"/);
  assert.match(workflows, /input.required = active/);
  assert.match(workflows, /form.append\("document", vehicleDocumentFile\)/);
  assert.match(workflows, /form.append\("confirmed", "true"\)/);
  assert.match(html, /id="vehicle-extract-consent" type="checkbox"\s*\//);
  assert.match(workflows, /residentEntryState\?\.review/);
  assert.match(workflows, /URL.revokeObjectURL/);
  assert.match(workflows, /\/requests\/vehicle/);
});

test("ticket pagination returns to the last valid page after the result count shrinks", () => {
  const start = workflows.indexOf("function alignTicketPage(");
  const end = workflows.indexOf("function setTicketLoading(", start);
  const manager = { offset: 40, limit: 20 };
  const resident = { offset: 20, limit: 20 };
  const align = vm.runInNewContext(`${workflows.slice(start, end)}\nalignTicketPage;`, {
    managementPage: manager, residentTicketPage: resident,
  });
  assert.equal(align("manager", 21), true);
  assert.equal(manager.offset, 20);
  assert.equal(align("resident", 0), true);
  assert.equal(resident.offset, 0);
  assert.equal(align("manager", 21), false);
});

test("one reading confirmation sends current revisions including read documents, prevents double submits and allows retry", async () => {
  const start = portal.indexOf('document.getElementById("reading-confirm-selected").addEventListener');
  const end = portal.indexOf("function clearPortalSession", start);
  const button = { disabled: false, textContent: "", addEventListener: (_, callback) => { handler = callback; } };
  let handler;
  let reject = true;
  let calls = 0;
  let reloads = 0;
  const documents = [{ source_id: "rules", revision: "2" }, { source_id: "safety", revision: "3" }, { source_id: "done", revision: "1", acknowledged_at: "today" }];
  const context = { authGeneration: 1, authReady: true, readingSubmitting: false, readingDocuments: documents,
    document: { getElementById: () => button }, hasRole: () => true, residentPath: path => path,
    isSessionCurrent: generation => generation === 1, portalError: () => {}, updateReadingSelection: () => {},
    loadProgress: async () => {}, loadRegulations: async () => { reloads += 1; },
    api: async (path, options) => {
      calls += 1;
      assert.equal(path, "/regulations/acknowledge-batch");
      assert.deepEqual(JSON.parse(options.body), { confirmed: true, documents: [{ source_id: "rules", revision: "2" }, { source_id: "safety", revision: "3" }, { source_id: "done", revision: "1" }] });
      if (reject) throw new Error("temporary failure");
    },
  };
  vm.runInNewContext(portal.slice(start, end), context);
  const first = handler();
  await handler();
  await first;
  assert.equal(calls, 1);
  assert.equal(reloads, 0);
  assert.equal(context.readingSubmitting, false);
  reject = false;
  await handler();
  assert.equal(calls, 2);
  assert.equal(reloads, 1);
  assert.equal(context.readingSubmitting, false);
  assert.doesNotMatch(portal, /data-reading-select|ack-dialog/);
});

test("password change requires matching confirmation and clears session only after successful request", () => {
  assert.match(html, /name="current_password" type="password" autocomplete="current-password"/);
  assert.match(html, /name="new_password" type="password" autocomplete="new-password" minlength="8"/);
  assert.match(workflows, /values.new_password !== values.confirm_password/);
  const handler = workflows.slice(workflows.indexOf('document.getElementById("password-change-form").addEventListener'));
  assert.ok(handler.indexOf('await api("/auth/password"') < handler.indexOf("showLogin("));
  assert.doesNotMatch(handler, /console\.|localStorage\.setItem/);
});

test("already read regulations remain confirmable, while empty/loading submissions are disabled", () => {
  const start = portal.indexOf("function updateReadingSelection(");
  const end = portal.indexOf("function filterReadingDocuments(", start);
  const button = {};
  const state = { readingDocuments: [{ acknowledged_at: "today" }], readingSubmitting: false, document: { getElementById: () => button } };
  vm.createContext(state);
  vm.runInContext(portal.slice(start, end), state);
  state.updateReadingSelection();
  assert.equal(button.disabled, false);
  state.readingSubmitting = true;
  state.updateReadingSelection();
  assert.equal(button.disabled, true);
  state.readingSubmitting = false;
  state.readingDocuments = [];
  state.updateReadingSelection();
  assert.equal(button.disabled, true);
});

test("password fields open on demand; success expires session but stays for manual dismissal", async () => {
  const start = workflows.indexOf("let passwordChangeAllowed");
  const end = workflows.indexOf("setupTicketFilters();", start);
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, { hidden: true, disabled: false, value: "", type: "password", open: false, listeners: {},
      setAttribute(key, value) { this[key] = value; }, addEventListener(name, fn) { this.listeners[name] = fn; },
      focus() {}, replaceChildren() {}, querySelectorAll() { return []; },
      showModal() { this.open = true; }, close() { this.open = false; this.listeners.close?.(); },
    });
    return elements.get(id);
  };
  const values = { current_password: "old-valid", new_password: "new-valid", confirm_password: "mismatch" };
  const form = get("password-change-form");
  form.reset = () => {};
  form.reportValidity = () => true;
  form.elements = { namedItem: () => ({ focus() {} }) };
  get("personal-dialog").open = true;
  let requests = 0;
  let fail = true;
  const state = { authGeneration: 1, authReady: true, authContext: { auth_subject: "local:resident.qa" }, bindUsernameValidation() {},
    document: { getElementById: get }, window: { addEventListener() {} },
    FormData: class { [Symbol.iterator]() { return Object.entries(values)[Symbol.iterator](); } },
    isSessionCurrent: generation => state.authReady && generation === state.authGeneration,
    api: async path => {
      if (path.endsWith("/status")) return { change_allowed: true, username: "resident.qa" };
      requests += 1;
      if (fail) throw new Error("incorrect password");
      return {};
    },
    portalSummary: (target, rows) => { target.rows = rows; },
    showLogin: () => { state.authReady = false; state.authGeneration += 1; get("personal-dialog").close(); },
  };
  vm.runInNewContext(workflows.slice(start, end), state);
  await get("user-role").listeners.click();
  assert.equal(get("password-change-section").hidden, true);
  get("password-change-open").listeners.click();
  assert.equal(get("password-change-section").hidden, false);
  assert.equal(get("password-change-dialog").open, true);
  assert.equal(get("personal-dialog").open, false);
  const submit = () => form.listeners.submit({ preventDefault() {}, currentTarget: form });
  await submit();
  assert.equal(requests, 0);
  values.confirm_password = values.new_password;
  await submit();
  assert.equal(state.authReady, true);
  assert.equal(get("password-change-error").hidden, false);
  fail = false;
  await submit();
  assert.equal(state.authReady, false);
  assert.equal(get("password-result-dialog").open, true);
  assert.equal(get("password-result-secret").value, "new-valid");
  assert.equal(get("password-result-secret").type, "password");
  get("password-result-done").listeners.click();
  assert.equal(get("password-result-dialog").open, false);
  assert.equal(get("password-result-secret").value, "");
});

test("issued-account username validation uses a browser-compatible Unicode pattern", () => {
  const pattern = html.match(/id="account-username"[^>]*pattern="([^"]+)"/)[1];
  const expression = new RegExp(`^(?:${pattern})$`, "v");
  assert.equal(expression.test("resident.new-2026"), true);
  assert.equal(expression.test("resident name"), false);
});

test("icon-only password fields toggle accessibly and re-hide on submit, reset, close and page exit", () => {
  const formEvents = {};
  const dialogEvents = {};
  const pageEvents = {};
  const buttonEvents = {};
  const attributes = { "aria-controls": "secret-field", "aria-label": "Hiện mật khẩu" };
  const input = {
    type: "password", value: "private-value",
    form: { addEventListener: (event, handler) => { formEvents[event] = handler; } },
    closest: () => ({ addEventListener: (event, handler) => { dialogEvents[event] = handler; } }),
  };
  const button = {
    getAttribute: key => attributes[key],
    setAttribute: (key, value) => { attributes[key] = value; },
    addEventListener: (event, handler) => { buttonEvents[event] = handler; },
    set textContent(value) { assert.fail("The icon must not be replaced with text"); },
  };
  const context = {
    document: { querySelectorAll: () => [button], getElementById: () => input },
    window: { addEventListener: (event, handler) => { pageEvents[event] = handler; } },
  };
  const start = workflows.indexOf("function bindPasswordVisibilityControls(");
  vm.runInNewContext(workflows.slice(start), context);
  assert.equal(attributes["aria-pressed"], "false");
  for (const hide of [formEvents.submit, formEvents.reset, dialogEvents.close, pageEvents.pagehide]) {
    buttonEvents.click();
    assert.equal(input.type, "text");
    assert.equal(attributes["aria-pressed"], "true");
    assert.equal(attributes["aria-label"], "Ẩn mật khẩu");
    buttonEvents.click();
    assert.equal(input.type, "password");
    buttonEvents.click();
    hide();
    assert.equal(input.type, "password");
    assert.equal(attributes["aria-pressed"], "false");
    assert.equal(attributes["aria-label"], "Hiện mật khẩu");
    assert.equal(input.value, "private-value");
  }
});
