import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const app = fs.readFileSync("web/app.js", "utf8");
const html = fs.readFileSync("web/index.html", "utf8");
const workflows = fs.readFileSync("web/resident-workflows.js", "utf8");
const styles = fs.readFileSync("web/portal.css", "utf8");

test("resident agent toolbar contains just procedures and my tickets", () => {
  const toolbar = html.match(/<nav class="chat-toolbar"[\s\S]*?<\/nav>/)[0];
  assert.equal((toolbar.match(/<button /g) || []).length, 2);
  assert.match(toolbar, /Chọn thủ tục/);
  assert.match(toolbar, /Ticket của tôi/);
  assert.doesNotMatch(toolbar, /Nhập thông tin|data-ui-form="profile"/);
});

test("vehicle uses one required document upload, with optional fill-from-image and no generic duplicate", () => {
  const section = html.match(/<section id="vehicle-upload"[\s\S]*?<\/section>/)[0];
  assert.match(section, /Điền thông tin từ ảnh/);
  assert.doesNotMatch(section, /Đọc thông tin từ ảnh/);
  assert.match(section, /vehicle-extract-consent/);
  const source = workflows.match(/function resetFormAutofill\(\) \{[\s\S]*?\n\}/)[0];
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); };
  const context = { autofillLoadId: 0, residentEntryState: { type: "vehicle_registration" }, document: { getElementById: get } };
  vm.createContext(context);
  vm.runInContext(source, context);
  context.resetFormAutofill();
  assert.equal(get("form-autofill").hidden, true);
  for (const type of ["resident_card", "move_in_schedule"]) {
    context.residentEntryState.type = type;
    context.resetFormAutofill();
    assert.equal(get("form-autofill").hidden, true);
    assert.match(app, /\["ownership", "account_registration", "vehicle_registration", "resident_card", "move_in_schedule"\]\.includes\(residentEntryState.type\)/);
  }
  context.residentEntryState.type = "temporary_residency";
  context.resetFormAutofill();
  assert.equal(get("form-autofill").hidden, false);
});

test("vehicle document action sits directly after plate, before notes, and outside review buttons", () => {
  const details = app.match(/function requestDetails\(item, showCompletedMark = false\) \{[\s\S]*?\n\}/)[0];
  const render = vm.runInNewContext(`${details}\nrequestDetails;`, { escapeHtml: String });
  const markup = render({ request_type: "vehicle_registration", payload: { vehicle_type: "car", vehicle_plate: "30K-123.45", details: "Ghi chú" } });
  assert.ok(markup.indexOf("Loại xe") < markup.indexOf("Biển số"));
  assert.ok(markup.indexOf("Biển số") < markup.indexOf("vehicle-document-slot"));
  assert.ok(markup.indexOf("vehicle-document-slot") < markup.indexOf("Chi tiết"));
  const source = workflows.match(/function addVehicleDocumentActions\(list, items\) \{[\s\S]*?\n\}/)[0];
  const appended = [];
  const slot = { appendChild: node => appended.push(node) };
  const state = { document: { createElement: () => ({ addEventListener() {} }) } };
  const attach = vm.runInNewContext(`${source}\naddVehicleDocumentActions;`, state);
  const list = { querySelectorAll: () => [{ querySelector: selector => selector === ".vehicle-document-slot dd" ? slot : assert.fail("Document must not enter the footer") }] };
  attach(list, [{ request_id: "REQ-VEHICLE", request_type: "vehicle_registration", vehicle_document_available: true }]);
  assert.equal(appended[0].textContent, "Xem giấy đăng ký xe");
  assert.equal(appended[0].disabled, false);
  assert.match(styles, /\.request-details > \.vehicle-document-slot \{ grid-column: 1 \/ -1;/);
});

async function renderHousehold(canRemove = true) {
  const source = app.slice(app.indexOf("function householdDeleteAction("), app.indexOf('document.getElementById("household").addEventListener'));
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, { innerHTML: "", textContent: "" }); return nodes.get(id); };
  const version = "2026-10-16T12:00:00+00:00";
  const state = {
    authReady: true, hasRole: () => true, authGeneration: 1, householdLoadId: 0, isSessionCurrent: () => true,
    document: { getElementById: get, querySelector: () => null }, escapeHtml: value => String(value).replaceAll('"', "&quot;").replaceAll("<", "&lt;"),
    residentPath: path => path, displayDate: String, requestStatusLabels: {}, ticketResidentTypeLabels: {}, residentTypeLabels: {},
    api: async () => ({
      apartment: "A1205", as_of_date: "2026-10-16", can_add_occupants: canRemove,
      occupants: { recorded_count: 2, people: [{ person_name: "An", resident_type: "family_member", request_id: "REQ-PERSON", updated_at: version, can_remove: canRemove }],
        temporary_residency_requests: [{ person_name: "Bình", status: "submitted", request_id: "REQ-PENDING", updated_at: version, can_remove: canRemove }] },
      vehicles: [{ vehicle_type: "car", vehicle_plate: "30K-123.45", status: "completed", owner_name: "An", owner_resident_type: "family_member", request_id: "REQ-CAR", updated_at: version, can_remove: canRemove }],
      pending_vehicles: [], internet: { needed: true, status: "completed", request_id: "REQ-INTERNET", updated_at: version, can_cancel: canRemove },
    }),
  };
  vm.createContext(state);
  vm.runInContext(source, state);
  await state.loadHousehold();
  return { state, get };
}

test("pending and approved people plus completed vehicles have Delete; internet has cancellation", async () => {
  const { get } = await renderHousehold();
  assert.equal((get("household-occupants").innerHTML.match(/data-household-action="remove-person"/g) || []).length, 2);
  assert.match(get("household-vehicles").innerHTML, /data-household-action="remove-vehicle"/);
  assert.match(get("household-vehicles").innerHTML, /<span>Delete<\/span>/);
  assert.match(get("household-internet").innerHTML, /data-household-action="cancel-internet"/);
  assert.match(get("household-internet").innerHTML, /Hủy dịch vụ internet/);
  assert.doesNotMatch(get("household-occupants").innerHTML, /Xóa khỏi danh sách/);
  assert.match(get("household-occupants").innerHTML, /data-updated-at="2026-10-16T12:00:00\+00:00"/);
});

test("read-only household entries cannot display an unauthorized Delete", async () => {
  const { get, state } = await renderHousehold(false);
  assert.doesNotMatch(get("household-occupants").innerHTML, /data-household-action=/);
  assert.doesNotMatch(get("household-vehicles").innerHTML, /data-household-action=/);
  assert.equal(state.householdDeleteAction({ can_remove: true, request_id: "REQ-1", updated_at: "now" }, "remove-person", '<img onerror="bad">').includes("<img"), false);
});

test("household Delete confirms first, sends the displayed version, and refreshes dependent views", async () => {
  const source = app.slice(app.indexOf('document.getElementById("household").addEventListener'), app.indexOf("function renderRequestStats("));
  const calls = [];
  let click;
  let consent = false;
  const button = { disabled: false, dataset: { householdAction: "remove-vehicle", requestId: "REQ-CAR", updatedAt: "2026-10-16T12:00:00Z", name: "30K-123.45" } };
  const context = {
    document: { getElementById: () => ({ addEventListener: (type, handler) => { click = handler; } }) },
    window: { confirm: () => consent }, authGeneration: 1, isSessionCurrent: () => true, residentPath: path => path,
    api: async (path, options) => calls.push({ path, payload: JSON.parse(options.body) }),
    loadHousehold: async () => calls.push("household"), loadProgress: async () => calls.push("progress"),
    loadResidentTickets: async () => calls.push("tickets"), loadNotificationFeed: async () => calls.push("notifications"),
    showTransientNotice: message => calls.push(message), sendSuggestedPrompt: () => assert.fail("not a chat action"),
  };
  vm.runInNewContext(source, context);
  await click({ target: { closest: () => button } });
  assert.equal(calls.length, 0);
  consent = true;
  await click({ target: { closest: () => button } });
  assert.equal(calls[0].path, "/requests/REQ-CAR/remove-vehicle");
  assert.deepEqual(calls[0].payload, { confirmed: true, expected_updated_at: button.dataset.updatedAt });
  assert.deepEqual(calls.slice(1, 5), ["household", "progress", "tickets", "notifications"]);
});

test("person Delete warns about suspending an issued account before any mutation", async () => {
  const source = app.slice(app.indexOf('document.getElementById("household").addEventListener'), app.indexOf("function renderRequestStats("));
  let click;
  let message;
  const button = { disabled: false, dataset: { householdAction: "remove-person", requestId: "REQ-PERSON", name: "An" } };
  const context = {
    document: { getElementById: () => ({ addEventListener: (type, handler) => { click = handler; } }) },
    window: { confirm: text => { message = text; return false; } }, authGeneration: 1,
    api: () => assert.fail("cancel must not change any data"),
  };
  vm.runInNewContext(source, context);
  await click({ target: { closest: () => button } });
  assert.match(message, /tạm ngừng sử dụng ngay/);
  assert.match(message, /kể cả phiên đang đăng nhập/);
  assert.match(message, /Tài khoản, ticket, giấy tờ và lịch sử vẫn được giữ lại/);
});
