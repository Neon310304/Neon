import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/ticket-corrections.js", "utf8");
const app = fs.readFileSync("web/app.js", "utf8");

function context(extra = {}) {
  const state = { apiErrorLabels: {}, document: { getElementById: () => ({ addEventListener() {} }) }, escapeHtml: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"), ...extra };
  vm.createContext(state);
  vm.runInContext(source, state);
  return state;
}

test("correction uses the existing ticket fields and sends only real changes", () => {
  const state = context();
  const item = { request_type: "temporary_residency", payload: { person_name: "Tên Sai", citizen_id: "001234567890", stay_start: "2026-10-12", details: "Ghi chú" } };
  const changes = state.ticketCorrectionChanges(item, { person_name: "Tên Đúng", citizen_id: "001234567890", move_date: "2026-10-12", replacement_details: "Ghi chú" });
  assert.equal(JSON.stringify(changes), JSON.stringify({ person_name: "Tên Đúng" }));
  const date = state.ticketCorrectionChanges(item, { person_name: "Tên Sai", citizen_id: "001234567890", move_date: "2026-10-13", replacement_details: "Ghi chú" });
  assert.equal(JSON.stringify(date), JSON.stringify({ move_date: "2026-10-13" }));
});

test("editing the description replaces rather than appends the wrong content", () => {
  const state = context();
  const item = { request_type: "general_support", payload: { details: "Nội dung sai" } };
  assert.equal(JSON.stringify(state.ticketCorrectionChanges(item, { replacement_details: "Nội dung đúng" })), JSON.stringify({ replacement_details: "Nội dung đúng" }));
  assert.equal(JSON.stringify(state.ticketCorrectionChanges(item, { replacement_details: "Nội dung sai" })), "{}");
});

test("names and notes are escaped and vehicle choices are preselected", () => {
  const state = context();
  const markup = state.ticketCorrectionFieldMarkup({ key: "person_name", label: "Tên", required: true, max: 120 }, { payload: { person_name: '"><img src=x>' } });
  assert.match(markup, /&quot;&gt;|&quot;>&lt;img/);
  assert.ok(!markup.includes("<img"));
  const field = state.ticketCorrectionFields({ request_type: "vehicle_registration" }).find(item => item.key === "vehicle_type");
  assert.match(state.ticketCorrectionFieldMarkup(field, { payload: { vehicle_type: "car" } }), /value="car" selected/);
});

test("all analogous workflows expose their editable fields", () => {
  const state = context();
  for (const [type, keys] of Object.entries({ temporary_residency: ["person_name", "citizen_id", "move_date"], visitor_registration: ["visitor_name", "visit_date"], move_in_schedule: ["move_date", "move_time"], vehicle_registration: ["vehicle_plate", "vehicle_type"], general_support: ["replacement_details"] })) {
    const actual = state.ticketCorrectionFields({ request_type: type }).map(field => field.key);
    keys.forEach(key => assert.ok(actual.includes(key)));
  }
});

function submissionContext({ confirmed = true, generation = true, fail = false } = {}) {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, { textContent: "", hidden: false, innerHTML: "", disabled: false, focus() {}, addEventListener() {} });
    return nodes.get(selector);
  };
  const calls = [];
  const values = { person_name: "Tên Đúng", citizen_id: "001234567890", move_date: "", replacement_details: "Ghi chú" };
  const form = { values, elements: { namedItem: key => key === "confirmed" ? { checked: confirmed, focus() {} } : null }, querySelectorAll: () => [node("submit")], reportValidity: () => true };
  const article = { querySelector: selector => selector === "form" ? form : node(selector), innerHTML: "", remove() {} };
  const state = context({
    isSessionCurrent: () => generation, document: { getElementById: node },
    residentPath: path => `/residents/R001${path}`, requestDetails: () => "", openResidentTab() {},
    FormData: class { constructor(form) { this.form = form; } [Symbol.iterator]() { return Object.entries(this.form.values)[Symbol.iterator](); } },
    api: async (path, options) => { calls.push({ path, options }); if (fail) throw new Error("Cannot update"); return { request_id: "REQ-12345678", payload: {} }; },
    loadResidentTickets: async () => {}, loadProgress: async () => {}, loadHousehold: async () => {}, loadNotificationFeed: async () => {},
  });
  state.current = { item: { request_id: "REQ-12345678", request_type: "temporary_residency", payload: { person_name: "Tên Sai", citizen_id: "001234567890", details: "Ghi chú" }, updated_at: "version-1" }, article, generation: 1, phase: "review", submitting: false, changes: { person_name: "Tên Đúng" } };
  vm.runInContext("ticketCorrectionState = current", state);
  return { state, calls, node };
}

test("reviewing changes is read-only and shows old and new names", async () => {
  const { state, calls, node } = submissionContext();
  state.current.phase = "edit";
  await state.submitTicketCorrection(state.current);
  assert.equal(calls.length, 0);
  assert.equal(state.current.phase, "review");
  assert.match(node(".correction-review dl").innerHTML, /<del>Tên Sai<\/del><strong>Tên Đúng<\/strong>/);
});

test("resubmission requires explicit consent and never creates a new ticket", async () => {
  const denied = submissionContext({ confirmed: false });
  await denied.state.submitTicketCorrection(denied.state.current);
  assert.equal(denied.calls.length, 0);
  const approved = submissionContext();
  await approved.state.submitTicketCorrection(approved.state.current);
  assert.equal(approved.calls.length, 1);
  assert.equal(approved.calls[0].path, "/residents/R001/requests/REQ-12345678");
  assert.equal(approved.calls[0].options.method, "PATCH");
  assert.deepEqual(JSON.parse(approved.calls[0].options.body), { person_name: "Tên Đúng", resubmit: true, expected_updated_at: "version-1" });
  assert.equal(approved.state.current.phase, "sent");
});

test("failed updates preserve the draft and unlock controls; stale sessions cannot submit", async () => {
  const failed = submissionContext({ fail: true });
  await failed.state.submitTicketCorrection(failed.state.current);
  assert.equal(failed.state.current.submitting, false);
  assert.equal(failed.node("submit").disabled, false);
  assert.equal(failed.node(".correction-error").textContent, "Cannot update");
  assert.equal(failed.state.current.changes.person_name, "Tên Đúng");
  const stale = submissionContext({ generation: false });
  await stale.state.submitTicketCorrection(stale.state.current);
  assert.equal(stale.calls.length, 0);
});

test("completion is only rendered after approval and resubmitted tickets show receive again", () => {
  const map = app.slice(app.indexOf("const requestActionMap ="), app.indexOf("const toolLabels ="));
  const state = { managementRequests: [], managementPage: { total: 1 }, escapeHtml: String, requestTypeIcon: () => "", requestTypeLabels: {}, requestStatusLabels: {}, requestDetails: () => "", requestProcedureNote: () => "", internetTicketNotice: () => "", residentProfileDetails: () => "", updateRequestFilterState() {} };
  const list = { innerHTML: "" };
  state.document = { getElementById: () => list };
  vm.createContext(state);
  vm.runInContext(`${map}\n${app.match(/function renderRequests\(\) \{[\s\S]*?\n\}/)[0]}`, state);
  for (const status of ["submitted", "in_review", "needs_info", "approved", "completed", "rejected", "cancelled_by_resident"]) {
    state.managementRequests = [{ request_id: "REQ-12345678", resident_id: "R001", request_type: "temporary_residency", status, updated_at: "2026-10-10T01:00:00Z", payload: { resubmission_count: 1 } }];
    state.renderRequests();
    assert.equal(list.innerHTML.includes('data-status="completed"'), status === "approved");
    if (status === "submitted") assert.match(list.innerHTML, /Tiếp nhận lại/);
  }
});

test("ticket and notification correction entry points open a separate ticket editor", () => {
  const notifications = fs.readFileSync("web/notifications.js", "utf8");
  assert.match(app, /data-request-id=.*Bổ sung thông tin/);
  assert.match(app, /openTicketCorrection\(chatButton.dataset.requestId, chatButton\)/);
  assert.match(app, /aria-controls="ticket-correction-dialog"/);
  assert.match(notifications, /correction.hidden = item.status !== "needs_info"/);
  assert.match(notifications, /openTicketCorrection\(correction.dataset.notificationCorrection, correction\)/);
});

test("the agent correction flow starts only from a chat question", () => {
  const state = context({ normalizeProcedureSearch: value => String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[đĐ]/g, "d").toLowerCase() });
  assert.equal(state.shouldHandleTicketCorrectionChat("Tôi thiếu gì?"), true);
  assert.equal(state.shouldHandleTicketCorrectionChat("BQL yêu cầu tôi bổ sung gì?"), true);
  assert.equal(state.shouldHandleTicketCorrectionChat("Xin chào"), false);
  assert.equal(state.shouldHandleTicketCorrectionChat("Nội quy thế nào?"), false);
  assert.ok(!app.includes("data-correction-agent"));
});

test("confirmation is exact and cannot submit a simultaneous correction or refusal", () => {
  const state = context({ normalizeProcedureSearch: value => String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[đĐ]/g, "d").toLowerCase() });
  for (const message of ["Đồng ý", "Xác nhận gửi", "Tôi xác nhận gửi bổ sung", "Gửi bổ sung cho BQL"]) assert.equal(state.isCorrectionChatConfirmation(message), true);
  for (const message of ["Không đồng ý", "Chưa gửi", "Đồng ý nhưng sửa tên thành Tên Mới", "Gửi cho ticket khác"]) assert.equal(state.isCorrectionChatConfirmation(message), false);
});

test("chat values only call the read-only assistant; confirmation patches the same ticket", async () => {
  const calls = [];
  const state = context({ authGeneration: 1, isSessionCurrent: () => true, normalizeProcedureSearch: value => value.toLowerCase(), residentPath: path => `/residents/R001${path}`,
    api: async (path, options) => { calls.push({ path, options }); return options.method === "PATCH" ? { request_id: "REQ-12345678" } : { ticket: { request_id: "REQ-12345678", updated_at: "version-1" }, draft: { person_name: "Tên Đúng" }, ready_to_confirm: true }; },
    loadResidentTickets: async () => {}, loadProgress: async () => {}, loadHousehold: async () => {}, loadNotificationFeed: async () => {} });
  await state.requestTicketCorrectionChat("Tên đúng là Tên Đúng");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/residents/R001/request-corrections/chat");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(state.shouldHandleTicketCorrectionChat("Tên Mới"), true);
  await state.requestTicketCorrectionChat("ok");
  assert.equal(calls[1].options.method, "PATCH");
  assert.equal(calls[1].path, "/residents/R001/requests/REQ-12345678");
  assert.deepEqual(JSON.parse(calls[1].options.body), { person_name: "Tên Đúng", resubmit: true, expected_updated_at: "version-1" });
  assert.equal(vm.runInContext("correctionChatState", state), null);
});

test("reset or logout clears the correction draft and a stale session cannot confirm", async () => {
  let calls = 0;
  const state = context({ isSessionCurrent: () => false, api: async () => { calls += 1; } });
  vm.runInContext('correctionChatState={ready:true,generation:1}; current=correctionChatState;', state);
  await assert.rejects(state.confirmCorrectionChat(state.current));
  assert.equal(calls, 0);
  state.clearCorrectionChatSession();
  assert.equal(vm.runInContext("correctionChatState", state), null);
});

test("a typed cancellation drops the draft without calling a write endpoint", async () => {
  const state = context({ normalizeProcedureSearch: value => value.toLowerCase(), api: async () => { throw new Error("must not call"); } });
  vm.runInContext('correctionChatState={ready:true,generation:1};', state);
  const response = await state.requestTicketCorrectionChat("huy bo sung");
  assert.match(response.answer, /chưa gửi/);
  assert.equal(vm.runInContext("correctionChatState", state), null);
});

test("a late reply cannot reopen a cancelled correction draft", async () => {
  let respond;
  const state = context({ authGeneration: 1, isSessionCurrent: () => true, normalizeProcedureSearch: value => value.toLowerCase(), residentPath: path => path,
    api: () => new Promise(resolve => { respond = resolve; }) });
  const pending = state.requestTicketCorrectionChat("toi thieu gi");
  state.clearCorrectionChatSession();
  respond({ ticket: { request_id: "REQ-12345678" }, draft: {}, ready_to_confirm: false });
  const response = await pending;
  assert.equal(response.ticket, undefined);
  assert.equal(vm.runInContext("correctionChatState", state), null);
});

test("the old draft cannot be confirmed while a new value is being prepared", async () => {
  let respond;
  let writes = 0;
  const state = context({ authGeneration: 1, isSessionCurrent: () => true, normalizeProcedureSearch: value => value.toLowerCase(), residentPath: path => path,
    api: (path, options) => { if (options.method === "PATCH") writes += 1; return new Promise(resolve => { respond = resolve; }); } });
  vm.runInContext('correctionChatState={ready:true,generation:1,item:{request_id:"REQ-12345678"},draft:{person_name:"Tên Cũ"}}; current=correctionChatState;', state);
  const pending = state.requestTicketCorrectionChat("sua thanh ten moi");
  await assert.rejects(state.confirmCorrectionChat(state.current));
  assert.equal(writes, 0);
  respond({ ticket: state.current.item, draft: { person_name: "Tên Mới" }, ready_to_confirm: true });
  await pending;
  assert.equal(vm.runInContext("correctionChatState.draft.person_name", state), "Tên Mới");
});

test("attached correction fields remain a draft and preserve values already supplied", async () => {
  const calls = [];
  const ticket = { request_id: "REQ-12345678", request_type: "temporary_residency", updated_at: "version-1" };
  const state = context({ authGeneration: 1, isSessionCurrent: () => true, normalizeProcedureSearch: value => value.toLowerCase(), residentPath: path => `/residents/R001${path}`,
    readFormFiles: async () => ({ form_draft: { values: { person_name: "Tên từ ảnh", citizen_id: "012345678901", visit_date: "2026-10-12" } } }),
    api: async (path, options) => {
      calls.push({ path, options });
      const input = JSON.parse(options.body);
      return { ticket, draft: { person_name: "Tên đã nhập", ...input.draft }, ready_to_confirm: true, answer: "Kiểm tra rồi xác nhận." };
    } });
  const result = await state.requestTicketCorrectionChat("Bổ sung ticket REQ-12345678", [{ file: { name: "cccd.png" } }]);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(call => call.options.method === "POST"));
  assert.deepEqual(JSON.parse(calls[1].options.body).draft, { person_name: "Tên đã nhập", citizen_id: "012345678901", move_date: "2026-10-12" });
  assert.match(result.answer, /chưa|Kiểm tra/);
  assert.equal(vm.runInContext("correctionChatState.preparing", state), undefined);
});

test("a late attached-file extraction cannot restore a cancelled correction", async () => {
  let finishRead;
  const state = context({ authGeneration: 1, isSessionCurrent: () => true, normalizeProcedureSearch: value => value.toLowerCase(), residentPath: path => path,
    api: async () => ({ ticket: { request_id: "REQ-12345678", request_type: "temporary_residency" }, draft: {}, ready_to_confirm: false }),
    readFormFiles: () => new Promise(resolve => { finishRead = resolve; }) });
  const pending = state.requestTicketCorrectionChat("Bổ sung ticket REQ-12345678", [{ file: {} }]);
  while (!finishRead) await new Promise(resolve => setImmediate(resolve));
  state.clearCorrectionChatSession();
  finishRead({ form_draft: { values: { person_name: "Tên từ ảnh" } } });
  const result = await pending;
  assert.equal(result.ticket, undefined);
  assert.equal(vm.runInContext("correctionChatState", state), null);
});
