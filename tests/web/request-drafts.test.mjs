import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { File } from "node:buffer";

const source = fs.readFileSync("web/request-drafts.js", "utf8");
const prepared = { handled: true, request_type: "resident_card", draft: {}, identity: { full_name: "Cư dân", apartment: "A0101" }, ready_to_confirm: true, answer: "Bản nháp chưa gửi" };

function harness(api) {
  let key = 0;
  const context = { authGeneration: 1, api, FormData, crypto: { randomUUID: () => `draft-key-${++key}` },
    residentPath: path => `/residents/R001${path}`, isSessionCurrent: generation => generation === context.authGeneration,
    normalizeProcedureSearch: value => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replaceAll("đ", "d").toLowerCase(),
    loadResidentTickets: async () => {}, loadProgress: async () => {},
  };
  vm.createContext(context);
  vm.runInContext(`${source}\nfunction currentDraft() { return requestDraftChatState; }\nfunction setPreview(article) { requestDraftChatPreview = article; }`, context);
  return context;
}

test("ordinary chat falls through without starting a draft or writing anything", async () => {
  const context = harness(async path => { assert.match(path, /request-drafts\/chat$/); return { handled: false }; });
  assert.equal((await context.requestTicketDraftChat("Đọc nội quy ở đâu")).handled, false);
  assert.equal(context.currentDraft(), null);
});

for (const message of ["", "đăng kí cho tôi xe này"]) {
  test(`vehicle image is read before text-only clarification (${message || "image-only"})`, async () => {
    const calls = [];
    const file = new File(["image fixture"], "xe.jpg", { type: "image/jpeg" });
    const context = harness(async (path, options) => {
      calls.push(path);
      if (path === "/agent/form-from-files") {
        assert.equal(options.body.get("message"), message);
        assert.equal(options.body.get("form_type"), "");
        return { answer: "Đã đọc", form_draft: { form_type: "vehicle_registration", values: { vehicle_plate: "29E2-986.07", vehicle_type: "motorbike" } } };
      }
      const body = JSON.parse(options.body);
      assert.equal(body.request_type, "vehicle_registration");
      assert.equal(body.document_attached, true);
      return { ...prepared, request_type: body.request_type, draft: body.draft };
    });
    const result = await context.requestTicketDraftChat(message, [{ file }]);
    assert.equal(result.handled, true);
    assert.deepEqual(calls, ["/agent/form-from-files", "/residents/R001/request-drafts/chat"]);
    assert.equal(context.currentDraft().draft.vehicle_plate, "29E2-986.07");
    assert.equal(context.currentDraft().document, file);
  });
}

test("ambiguous, out-of-scope and multiple-vehicle photos never submit or silently choose one", async () => {
  const file = new File(["fixture"], "xe.jpg", { type: "image/jpeg" });
  for (const result of [
    { policy_code: "prompt_injection_blocked", answer: "Blocked" },
    { answer: "Bạn muốn thủ tục nào?", form_draft: { form_type: "", values: {} } },
    { answer: "Đã đọc", form_draft: { form_type: "profile", values: {} } },
    { answer: "Đã đọc", form_draft: { form_type: "vehicle_registration", values: {} } },
  ]) {
    const paths = [];
    const context = harness(async path => { paths.push(path); return result; });
    const response = await context.requestTicketDraftChat("", [{ file }, { file }]);
    assert.equal(response.handled, true);
    assert.deepEqual(paths, ["/agent/form-from-files"]);
    assert.equal(context.currentDraft(), null);
  }
});

test("a blurred vehicle photo keeps missing fields empty instead of confirming guessed values", async () => {
  const file = new File(["blurred fixture"], "mo.jpg", { type: "image/jpeg" });
  const context = harness(async (path, options) => path === "/agent/form-from-files"
    ? { answer: "Chưa đọc được biển số xe", form_draft: { form_type: "vehicle_registration", values: {}, unreadable_fields: ["vehicle_plate"] } }
    : { ...prepared, request_type: "vehicle_registration", draft: JSON.parse(options.body).draft, ready_to_confirm: false, missing_fields: ["vehicle_plate", "vehicle_type"] });
  const result = await context.requestTicketDraftChat("", [{ file }]);
  assert.match(result.answer, /Chưa đọc được biển số xe/);
  assert.equal(context.currentDraft().ready, false);
  assert.deepEqual(Object.keys(context.currentDraft().draft), []);
});

test("a vehicle image can switch an existing internet draft without losing image intent", async () => {
  const file = new File(["fixture"], "xe.jpg", { type: "image/jpeg" });
  const context = harness(async (path, options) => {
    if (path === "/agent/form-from-files") {
      assert.equal(options.body.get("form_type"), "");
      assert.equal(options.body.get("active_form_type"), "internet_registration");
      return { form_draft: { form_type: "vehicle_registration", values: { vehicle_plate: "29E2-986.07", vehicle_type: "motorbike" } } };
    }
    const body = JSON.parse(options.body);
    return { ...prepared, request_type: body.request_type || "internet_registration", draft: body.request_type ? body.draft : { details: "Nhờ tư vấn internet" } };
  });
  await context.requestTicketDraftChat("Đăng ký internet");
  await context.requestTicketDraftChat("đăng kí cho tôi xe này", [{ file }]);
  assert.equal(context.currentDraft().type, "vehicle_registration");
  assert.equal(context.currentDraft().draft.vehicle_plate, "29E2-986.07");
  assert.equal(context.currentDraft().draft.details, undefined);
  assert.equal(context.currentDraft().document, file);
});

test("replacing a vehicle photo never combines its unreadable fields with the old vehicle", async () => {
  const oldFile = new File(["old vehicle"], "old.jpg", { type: "image/jpeg" });
  const newFile = new File(["new vehicle"], "new.jpg", { type: "image/jpeg" });
  let reads = 0;
  const context = harness(async (path, options) => {
    if (path === "/agent/form-from-files") {
      reads += 1;
      return { form_draft: { form_type: "vehicle_registration", values: reads === 1 ? { vehicle_plate: "29E2-986.07", vehicle_type: "motorbike" } : {} } };
    }
    const body = JSON.parse(options.body);
    return { ...prepared, request_type: "vehicle_registration", draft: body.draft, ready_to_confirm: Boolean(body.draft.vehicle_plate) };
  });
  await context.requestTicketDraftChat("", [{ file: oldFile }]);
  const oldKey = context.currentDraft().key;
  const response = await context.requestTicketDraftChat("", [{ file: newFile }]);
  assert.equal(context.currentDraft().draft.vehicle_plate, undefined);
  assert.equal(context.currentDraft().ready, false);
  assert.equal(context.currentDraft().document, newFile);
  assert.notEqual(context.currentDraft().key, oldKey);
  assert.match(response.answer, /không tự dùng biển số từ ảnh cũ/);
});

test("late image extraction cannot restore a cancelled or logged-out draft", async () => {
  const file = new File(["fixture"], "xe.jpg", { type: "image/jpeg" });
  for (const logout of [false, true]) {
    let finish;
    const context = harness(() => new Promise(resolve => { finish = resolve; }));
    const pending = context.requestTicketDraftChat("", [{ file }]);
    if (logout) context.authGeneration += 1;
    else context.clearRequestDraftChat();
    finish({ form_draft: { form_type: "vehicle_registration", values: { vehicle_plate: "29E2-986.07" } } });
    await pending;
    assert.equal(context.currentDraft(), null);
  }
});

test("closing manual entry retains edited name and ID for the next chat turn", async () => {
  const calls = [];
  const context = harness(async (path, options) => {
    const body = JSON.parse(options.body);
    calls.push(body);
    return { ...prepared, request_type: "temporary_residency", draft: body.draft };
  });
  await context.requestTicketDraftChat("Đăng ký tạm trú");
  context.residentEntryState = { type: "temporary_residency", submitting: false };
  context.currentDraft().formEntry = context.residentEntryState;
  context.residentEntryCatalog = { temporary_residency: { fields: [{ key: "person_name" }, { key: "citizen_id" }] } };
  context.residentEntryForm = { elements: { namedItem: key => ({ value: key === "person_name" ? "Phạm Văn Hùng" : "" }) } };
  context.entryPayload = (type, values) => Object.fromEntries(Object.entries(values).filter(([, value]) => value));
  context.syncRequestDraftFromEntry();
  assert.equal(context.currentDraft().ready, false);
  assert.equal(context.currentDraft().draft.person_name, "Phạm Văn Hùng");
  await context.requestTicketDraftChat("035262426852");
  assert.equal(calls.at(-1).request_type, "temporary_residency");
  assert.equal(calls.at(-1).draft.person_name, "Phạm Văn Hùng");
});

test("returning from the manual vehicle form synchronizes a replaced or removed photo", async () => {
  const oldFile = new File(["old"], "old.jpg", { type: "image/jpeg" });
  const newFile = new File(["new"], "new.jpg", { type: "image/jpeg" });
  for (const file of [newFile, null]) {
    const context = harness(async () => ({ ...prepared, request_type: "vehicle_registration", draft: { vehicle_plate: "29E2-986.07", vehicle_type: "motorbike" } }));
    await context.requestTicketDraftChat("Đăng ký thẻ xe");
    context.currentDraft().document = oldFile;
    context.residentEntryState = { type: "vehicle_registration", submitting: false };
    context.currentDraft().formEntry = context.residentEntryState;
    context.residentEntryCatalog = { vehicle_registration: { fields: [{ key: "vehicle_plate" }, { key: "vehicle_type" }] } };
    context.residentEntryForm = { elements: { namedItem: key => ({ value: key === "vehicle_plate" ? "30K-123.45" : "car" }) } };
    context.vehicleDocumentFile = file;
    context.entryPayload = (type, values) => values;
    context.syncRequestDraftFromEntry();
    assert.equal(context.currentDraft().document, file);
    assert.equal(context.currentDraft().draft.vehicle_plate, "30K-123.45");
    assert.equal(context.currentDraft().ready, false);
  }
});

test("preparing and updating a draft never submits until exact confirmation", async () => {
  const calls = [];
  const context = harness(async (path, options) => {
    calls.push([path, JSON.parse(options.body)]);
    return path.endsWith("/requests") ? { created: true, request_id: "REQ-12345678" } : { ...prepared };
  });
  await context.requestTicketDraftChat("Đăng kí hộ thẻ cư dân");
  await context.requestTicketDraftChat("OK");
  assert.equal(calls.filter(([path]) => path.endsWith("/requests")).length, 0);
  assert.equal(context.isRequestDraftConfirmation("Đồng ý gửi nhưng đổi ngày trước"), false);
  const sent = await context.requestTicketDraftChat("Xác nhận gửi");
  assert.equal(calls.filter(([path]) => path.endsWith("/requests")).length, 1);
  assert.equal(calls.at(-1)[0], "/requests");
  assert.match(sent.answer, /Đã gửi.*REQ-12345678/);
  assert.equal(sent.tools_used[0].summary, "created:REQ-12345678");
  assert.equal(context.currentDraft(), null);
});

test("natural moving conversation keeps the draft date and submits the actual chosen time", async () => {
  const calls = [];
  const context = harness(async (path, options) => {
    const body = JSON.parse(options.body);
    calls.push([path, body]);
    if (path.endsWith("/requests")) return { created: true, request_id: "REQ-12345678" };
    const draft = body.request_type ? { ...body.draft, move_time: body.message } : { visit_date: "2026-10-18" };
    return { ...prepared, request_type: "move_in_schedule", draft, ready_to_confirm: Boolean(draft.move_time) };
  });
  await context.requestTicketDraftChat("tôi có đồ muốn chuyển vào ngày mai, xếp lịch cho tôi được không");
  assert.equal(context.currentDraft().ready, false);
  await context.requestTicketDraftChat("14:00");
  assert.equal(calls[1][1].request_type, "move_in_schedule");
  assert.equal(calls[1][1].draft.visit_date, "2026-10-18");
  assert.equal(context.currentDraft().ready, true);
  assert.equal(calls.filter(([path]) => path.endsWith("/requests")).length, 0);
  await context.requestTicketDraftChat("Xác nhận gửi");
  assert.equal(calls[2][0], "/requests");
  assert.equal(calls[2][1].visit_date, "2026-10-18");
  assert.equal(calls[2][1].move_time, "14:00");
  assert.equal(context.currentDraft(), null);
});

test("staying guest name and citizen ID remain in one draft rather than falling into ordinary chat", async () => {
  const calls = [];
  const context = harness(async (path, options) => {
    const body = JSON.parse(options.body);
    calls.push([path, body]);
    if (path.endsWith("/requests")) return { created: true, request_id: "REQ-12345678" };
    const draft = { visit_date: "2026-10-20", ...body.draft };
    if (body.message === "nguyễn song sắt") draft.person_name = body.message;
    if (/^\d{12}$/.test(body.message)) draft.citizen_id = body.message;
    return { ...prepared, request_type: "temporary_residency", draft, ready_to_confirm: Boolean(draft.person_name && draft.citizen_id) };
  });
  for (const message of ["tôi có bạn lên ở tạm vài ngày bắt đầu từ ngày 20/10", "nguyễn song sắt", "111111111111"]) {
    assert.equal((await context.requestTicketDraftChat(message)).handled, true);
  }
  assert.equal(context.currentDraft().draft.citizen_id, "111111111111");
  assert.equal(context.currentDraft().ready, true);
  assert.equal(calls.filter(([path]) => path.endsWith("/requests")).length, 0);
  await context.requestTicketDraftChat("Xác nhận gửi");
  assert.equal(calls.at(-1)[1].person_name, "nguyễn song sắt");
  assert.equal(calls.at(-1)[1].citizen_id, "111111111111");
});

test("duplicates are not a new submission and are not labelled as one", async () => {
  const context = harness(async path => path.endsWith("/requests") ? { duplicate: true, request_id: "REQ-12345678" } : { ...prepared });
  await context.requestTicketDraftChat("Đăng ký thẻ cư dân");
  const result = await context.requestTicketDraftChat("Xác nhận gửi");
  assert.match(result.answer, /Không tạo thêm ticket/);
  assert.equal(result.tools_used[0].summary, "existing:REQ-12345678");
});

test("failed preparation invalidates old confirmation instead of submitting stale values", async () => {
  let fail = false;
  let submissions = 0;
  const context = harness(async path => {
    if (path.endsWith("/requests")) submissions += 1;
    if (fail) throw new Error("Unavailable");
    return { ...prepared };
  });
  await context.requestTicketDraftChat("Đăng ký thẻ cư dân");
  fail = true;
  await assert.rejects(context.requestTicketDraftChat("Đổi giờ nhận"));
  assert.equal(context.currentDraft().ready, false);
  fail = false;
  await context.requestTicketDraftChat("Xác nhận gửi");
  assert.equal(submissions, 0);
});

test("cancel, logout and delayed responses cannot restore a discarded draft", async () => {
  let resolve;
  const context = harness(() => new Promise(done => { resolve = done; }));
  const pending = context.requestTicketDraftChat("Đăng ký thẻ cư dân");
  context.clearRequestDraftChat();
  resolve({ ...prepared });
  await pending;
  assert.equal(context.currentDraft(), null);
  const stale = context.requestTicketDraftChat("Đăng ký thẻ cư dân");
  context.authGeneration += 1;
  resolve({ ...prepared });
  await stale;
  assert.equal(context.currentDraft(), null);
  const active = harness(async () => ({ ...prepared }));
  await active.requestTicketDraftChat("Đăng ký thẻ cư dân");
  active.api = async () => ({ handled: true, clear_draft: true, answer: "Đã dừng" });
  await active.requestTicketDraftChat("Hủy đăng ký");
  assert.equal(active.currentDraft(), null);
});

test("unconfirmed API results retain the draft and never announce success", async () => {
  const context = harness(async path => path.endsWith("/requests") ? { created: false } : { ...prepared });
  await context.requestTicketDraftChat("Đăng ký thẻ cư dân");
  await assert.rejects(context.requestTicketDraftChat("Xác nhận gửi"), /chưa xác nhận/);
  assert.ok(context.currentDraft());
});

test("double confirmation has one in-flight submission", async () => {
  let finish;
  let submissions = 0;
  const context = harness(async path => {
    if (!path.endsWith("/requests")) return { ...prepared };
    submissions += 1;
    return new Promise(resolve => { finish = resolve; });
  });
  await context.requestTicketDraftChat("Đăng ký thẻ cư dân");
  const pending = context.requestTicketDraftChat("Xác nhận gửi");
  await context.requestTicketDraftChat("Xác nhận gửi");
  assert.equal(submissions, 1);
  finish({ created: true, request_id: "REQ-12345678" });
  await pending;
});

test("chat confirmation cannot send a schedule edited but not applied", async () => {
  const calls = [];
  const context = harness(async path => { calls.push(path); return { ...prepared }; });
  await context.requestTicketDraftChat("Đăng ký thẻ cư dân");
  context.currentDraft().scheduleDirty = true;
  const result = await context.requestTicketDraftChat("Xác nhận gửi");
  assert.equal(result.keep_preview, true);
  assert.match(result.answer, /Áp dụng ngày giờ/);
  assert.equal(calls.length, 1);
  await context.confirmRequestDraftChat(context.currentDraft());
  assert.equal(calls.length, 1);
});

test("inline date/time edits are revalidated read-only before one explicit submission", async () => {
  const calls = [];
  const context = harness(async (path, options) => {
    const body = JSON.parse(options.body);
    calls.push([path, body]);
    if (path.endsWith("/requests")) return { created: true, request_id: "REQ-12345678" };
    return { ...prepared, draft: body.draft };
  });
  await context.requestTicketDraftChat("Đăng ký thẻ cư dân");
  const state = context.currentDraft();
  const initialKey = state.key;
  state.ready = false;
  state.scheduleDirty = true;
  const controls = [{ disabled: false }];
  const confirmation = { disabled: true, focus() {} };
  const notice = { hidden: true };
  context.setPreview({ querySelectorAll: () => controls, querySelector: selector => selector === ".field-error" ? notice : confirmation, remove() {} });
  context.renderRequestDraftChat = () => {};
  const values = { pickup_date: "2026-12-01", pickup_time: "17:35" };
  const form = { reportValidity: () => true, elements: { namedItem: key => ({ value: values[key] }) } };
  await context.applyRequestDraftSchedule(state, form, null);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1][1].draft, values);
  assert.equal(state.ready, true);
  assert.equal(state.scheduleDirty, false);
  assert.notEqual(state.key, initialKey);
  assert.equal(confirmation.disabled, false);
  await context.confirmRequestDraftChat(state);
  assert.equal(calls.length, 3);
  assert.equal(calls[2][0], "/requests");
  assert.equal(calls[2][1].pickup_date, values.pickup_date);
  assert.equal(calls[2][1].pickup_time, values.pickup_time);
});

test("invalid schedule confirmation stays blocked instead of resurrecting stale values", async () => {
  let calls = 0;
  const context = harness(async () => { calls += 1; return { ...prepared, ready_to_confirm: false, draft: {} }; });
  await context.requestTicketDraftChat("Đăng ký lịch chuyển đồ vào 25h");
  const result = await context.requestTicketDraftChat("Xác nhận gửi");
  assert.equal(result.keep_preview, true);
  assert.equal(calls, 1);
  assert.equal(context.currentDraft().ready, false);
});
