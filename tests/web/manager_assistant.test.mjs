import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/manager-assistant.js", "utf8");

function harness() {
  const nodes = new Map();
  class Element {
    constructor() {
      this.children = [];
      this.dataset = {};
      this.attributes = {};
      this.listeners = {};
      this.classes = new Set();
      this.classList = {
        remove: (name) => this.classes.delete(name),
        contains: (name) => this.classes.has(name),
        toggle: (name) => { if (this.classes.delete(name)) return false; this.classes.add(name); return true; },
      };
      this.textContent = "";
      this.value = "";
      this.isConnected = true;
      this.open = false;
    }
    get childElementCount() { return this.children.length; }
    get firstElementChild() { return this.children[0]; }
    append(...elements) { elements.forEach(element => { element.parent = this; this.children.push(element); }); }
    replaceChildren() { this.children = []; }
    remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
    addEventListener(type, listener) { this.listeners[type] = listener; }
    setAttribute(name, value) { this.attributes[name] = value; }
    showModal() { this.open = true; }
    show() { this.open = true; }
    close() { this.open = false; }
    reset() { this.value = ""; }
    focus() { this.focused = true; }
    click() { this.listeners.click?.({ currentTarget: this }); }
  }
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const calls = [];
  const decisions = [];
  const state = {
    AbortController, authReady: true, authGeneration: 1, role: "manager",
    hasRole: role => state.role === role,
    isSessionCurrent: generation => state.authReady && generation === state.authGeneration,
    document: { getElementById: node, createElement: () => new Element(), querySelectorAll: () => [], querySelector: node, activeElement: node("trigger"), addEventListener() {} },
    managementPage: { offset: 20 }, loadRequests: async () => {}, decisionReason: node("decision-reason"),
    selectTicketStageForStatus: (role, status) => { state.selectedTicketStage = { role, status }; },
    decide: (...args) => decisions.push(args),
    requestDetails: () => "<dl>Thông tin hồ sơ</dl>", residentProfileDetails: () => "", openVehicleDocument() {},
    api: async (path, options) => { calls.push({ path, options }); return { answer: "Có 0 ticket", tickets: [], total: 0, filters: {} }; },
  };
  vm.createContext(state);
  vm.runInContext(source, state);
  return { state, node, calls, decisions, read: code => vm.runInContext(code, state) };
}

test("manager launcher is hidden for residents and chat refuses requests", async () => {
  const { state, node, calls } = harness();
  state.role = "resident";
  state.configureManagementAgent();
  state.openManagementAgent();
  await state.sendManagerAgentMessage("Tổng quan hàng đợi");
  assert.equal(node("manager-agent-launcher").hidden, true);
  assert.equal(node("manager-agent-dialog").open, false);
  assert.equal(calls.length, 0);
});

test("chat uses only the scoped management endpoint and selected context", async () => {
  const { state, calls, read } = harness();
  read('managerAgentSelectedId = "REQ-12345678"');
  await state.sendManagerAgentMessage("Tóm tắt ticket này");
  assert.equal(calls[0].path, "/management/assistant/chat");
  assert.deepEqual(JSON.parse(calls[0].options.body), { message: "Tóm tắt ticket này", request_id: "REQ-12345678" });
  assert.equal(calls[0].options.method, "POST");
});

test("logout/reset removes every management message, selection and expansion", () => {
  const { state, node, read } = harness();
  state.openManagementAgent();
  read('managerAgentSelectedId = "REQ-12345678"');
  node("manager-agent-expand").click();
  state.resetManagementAgent();
  assert.equal(node("manager-agent-messages").childElementCount, 0);
  assert.equal(node("manager-agent-dialog").open, false);
  assert.equal(node("manager-agent-selection").hidden, true);
  assert.equal(node("manager-agent-launcher").hidden, true);
  assert.equal(node("manager-agent-expand").attributes["aria-label"], "Mở rộng trợ lý quản lý");
});

test("a late chat response after logout cannot restore confidential content", async () => {
  const { state, node } = harness();
  let resolve;
  state.api = () => new Promise(done => { resolve = done; });
  const pending = state.sendManagerAgentMessage("Tổng quan hàng đợi");
  state.authReady = false;
  state.authGeneration += 1;
  state.resetManagementAgent();
  resolve({ answer: "Private queue", tickets: [] });
  await pending;
  assert.equal(node("manager-agent-messages").childElementCount, 0);
});

test("an older cancelled request does not unlock the next request", async () => {
  const { state, node } = harness();
  const resolvers = [];
  state.api = () => new Promise(resolve => resolvers.push(resolve));
  const first = state.sendManagerAgentMessage("Ticket đã gửi");
  state.closeManagementAgent();
  const second = state.sendManagerAgentMessage("Ticket cần bổ sung");
  resolvers[0]({ answer: "old", tickets: [] });
  await first;
  assert.equal(node("manager-agent-send").disabled, true);
  resolvers[1]({ answer: "new", tickets: [] });
  await second;
  assert.equal(node("manager-agent-send").disabled, false);
});

test("reading a draft opens existing confirmation, never PATCHes or sends", async () => {
  const { state, decisions, calls } = harness();
  state.api = async (path, options) => { calls.push({ path, options }); return { items: [{ request_id: "REQ-12345678", status: "submitted", updated_at: "version-1" }] }; };
  await state.reviewManagerAgentDraft({ request_id: "REQ-12345678", expected_updated_at: "version-1", reason: "Bổ sung giấy đăng ký xe" }, {});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, undefined);
  assert.equal(decisions.length, 1);
  assert.deepEqual(decisions[0].slice(0, 2), ["REQ-12345678", "needs_info"]);
  assert.equal(state.decisionReason.value, "Bổ sung giấy đăng ký xe");
});

test("a stale or completed ticket prevents opening an outdated draft", async () => {
  for (const ticket of [{ status: "submitted", updated_at: "version-2" }, { status: "completed", updated_at: "version-1" }]) {
    const { state, decisions, node } = harness();
    state.api = async () => ({ items: [{ request_id: "REQ-12345678", ...ticket }] });
    await state.reviewManagerAgentDraft({ request_id: "REQ-12345678", expected_updated_at: "version-1", reason: "Old reason" }, {});
    assert.equal(decisions.length, 0);
    assert.match(node("manager-agent-messages").children[0].children[1].textContent, /đã thay đổi/);
  }
});

test("closing the assistant cancels draft freshness checks", async () => {
  const { state, decisions } = harness();
  let resolve;
  state.api = () => new Promise(done => { resolve = done; });
  const pending = state.reviewManagerAgentDraft({ request_id: "REQ-12345678", expected_updated_at: "version-1", reason: "Old reason" }, {});
  state.closeManagementAgent();
  resolve({ items: [{ request_id: "REQ-12345678", status: "submitted", updated_at: "version-1" }] });
  await pending;
  assert.equal(decisions.length, 0);
});

test("open queue transfers filters and restarts pagination", async () => {
  const { state, node } = harness();
  await state.openManagerAgentQueue({ status: "needs_info", query: "A1205", request_type: "vehicle_registration", date_from: "2026-10-01" });
  assert.equal(node("request-search").value, "A1205");
  assert.equal(node("request-status-filter").value, "needs_info");
  assert.equal(node("request-date-from").value, "2026-10-01");
  assert.deepEqual(state.selectedTicketStage, { role: "manager", status: "needs_info" });
  assert.equal(state.managementPage.offset, 0);
  assert.equal(node("request-list").focused, true);
});

test("responses remain text, and policy blocks have no actionable cards", () => {
  const { state, node } = harness();
  state.renderManagerAgentResult({ answer: "<img src=x onerror=alert(1)>", policy_code: "blocked", tickets: [{ request_id: "REQ-12345678" }] });
  const article = node("manager-agent-messages").children[0];
  assert.equal(article.children.length, 2);
  assert.equal(article.children[1].textContent, "<img src=x onerror=alert(1)>");
  for (let number = 0; number < 60; number += 1) state.managerAgentMessage("assistant", String(number));
  assert.equal(node("manager-agent-messages").childElementCount, 40);
});

const group = { title: "Thêm người cư trú", site_name: "Khu A", status_label: "Đã gửi", action: "in_review", variant: {}, total: 1, tickets: [{ request_id: "REQ-12345678", updated_at: "version-1" }] };

test("group suggestion only displays a review action, never mutates tickets", () => {
  const { state, calls, node } = harness();
  state.renderManagerAgentResult({ answer: "Có nhóm", groups: [group], exceptions: [], reminders: [] });
  assert.equal(calls.length, 0);
  const card = node("manager-agent-messages").children[0].children[2];
  assert.equal(card.children.at(-1).textContent, "Chuẩn bị tiếp nhận");
});

test("opening group fetches current versions and leaves every checkbox unselected", async () => {
  const { state, calls, node, read } = harness();
  state.api = async (path, options) => { calls.push({ path, options }); return { items: [{ request_id: "REQ-12345678", updated_at: "version-1", status: "submitted", request_type: "temporary_residency", payload: {} }] }; };
  await state.reviewManagerAgentGroup(group);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/management/assistant/batch-preview");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(JSON.parse(calls[0].options.body).status, "in_review");
  assert.equal(node("manager-batch-dialog").open, true);
  const checkbox = read('managerBatchState.reviewed.get("REQ-12345678")');
  assert.equal(checkbox.checked, false);
  assert.notEqual(checkbox.disabled, true);
  assert.match(node("manager-batch-count").textContent, /0 \/ 1/);
  node("manager-batch-select-all").click();
  assert.equal(checkbox.checked, true);
  assert.equal(node("manager-batch-submit").disabled, false);
  node("manager-batch-clear").click();
  assert.equal(checkbox.checked, false);
  assert.equal(node("manager-batch-submit").disabled, true);
});

function batchHarness() {
  const setup = harness();
  setup.state.batch = { items: [{ request_id: "REQ-12345678", updated_at: "version-1" }], reviewed: new Map([["REQ-12345678", { checked: true }]]), action: "in_review", generation: 1, submitting: false };
  setup.read("managerBatchState = batch");
  setup.node("manager-batch-reason").value = "Đã đối chiếu hồ sơ";
  return setup;
}

test("batch submission requires review and confirmation before the write endpoint", async () => {
  const { state, calls, node } = batchHarness();
  await state.submitManagerBatch({ preventDefault() {} });
  assert.equal(calls.length, 0);
  node("manager-batch-confirmed").checked = true;
  state.api = async (path, options) => { calls.push({ path, options }); return { updated: 1 }; };
  await state.submitManagerBatch({ preventDefault() {} });
  assert.equal(calls[0].path, "/management/assistant/batch-decision");
  assert.equal(calls[0].options.method, "POST");
  const payload = JSON.parse(calls[0].options.body);
  assert.deepEqual(payload.tickets, [{ request_id: "REQ-12345678", expected_updated_at: "version-1", reviewed: true }]);
  assert.equal(payload.confirmed, true);
});

test("changed group cannot be opened; stale sessions cannot submit", async () => {
  const changed = harness();
  changed.state.api = async () => ({ items: [{ request_id: "REQ-12345678", updated_at: "version-2" }] });
  await changed.state.reviewManagerAgentGroup(group);
  assert.equal(changed.node("manager-batch-dialog").open, false);
  const stale = batchHarness();
  stale.state.authGeneration += 1;
  stale.node("manager-batch-confirmed").checked = true;
  await stale.state.submitManagerBatch({ preventDefault() {} });
  assert.equal(stale.calls.length, 0);
});

test("removed shortcuts stay absent and manager uses the same animated mascot hosts", () => {
  const html = fs.readFileSync("web/index.html", "utf8");
  const app = fs.readFileSync("web/app.js", "utf8");
  const mascot = fs.readFileSync("web/agent-mascot.js", "utf8");
  assert.doesNotMatch(html, /id="manager-agent-open"|id="picker-back"/);
  assert.doesNotMatch(app, /getElementById\("picker-back"\)/);
  assert.match(html, /class="chat-launcher manager-agent-launcher"/);
  assert.match(html, /data-agent-mascot="manager-launcher"/);
  assert.match(html, /data-agent-mascot="manager-header"/);
  assert.match(mascot, /managerActive/);
  assert.doesNotMatch(html, /data-manager-prompt="[^"]*(?:Nhắc việc|Gom nhóm|Đề xuất)/);
  assert.match(html, /data-manager-prompt="Rà soát hồ sơ chờ xử lý"/);
  assert.match(html, /id="manager-batch-select-all"/);
  assert.doesNotMatch(source, /manager-agent-reminders|details\.addEventListener\("toggle"/);
});
