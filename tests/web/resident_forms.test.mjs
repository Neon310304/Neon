import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const payloadStart = source.indexOf("function entryPayload(");
const payloadEnd = source.indexOf("function setEntryError(", payloadStart);
const payload = vm.runInNewContext(`${source.slice(payloadStart, payloadEnd)}\nentryPayload;`);
const attachmentStart = source.indexOf("function attachmentChatMessage(");
const attachmentEnd = source.indexOf("function sendSuggestedPrompt(", attachmentStart);
const attachmentPrompt = vm.runInNewContext(`${source.slice(attachmentStart, attachmentEnd)}\nattachmentChatMessage;`);

test("profile form sends typed values without apartment or role escalation", () => {
  const result = payload("profile", { full_name: "  Cư dân  ", resident_type: "tenant", family_members: "2", vehicle: "car", needs_internet: "false", apartment: "Other", role: "manager" });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { family_members: 2, vehicle: "car", needs_internet: false });
});

test("request form trims submitted fields and excludes empty optional values", () => {
  const result = payload("vehicle_registration", { vehicle_type: "car", vehicle_plate: " 30K-123.45 ", details: "  " });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { vehicle_type: "car", vehicle_plate: "30K-123.45" });
});

test("resident card form exposes collection date and time and serializes the preferred slot", () => {
  const start = source.indexOf("const residentEntryCatalog =");
  const end = source.indexOf("let residentEntryState =", start);
  const catalog = vm.runInNewContext(`${source.slice(start, end)}\nresidentEntryCatalog;`);
  assert.ok(catalog.resident_card.fields.some(field => field.key === "pickup_date" && field.type === "date"));
  assert.ok(catalog.resident_card.fields.some(field => field.key === "pickup_time" && field.type === "time"));
  assert.deepEqual(JSON.parse(JSON.stringify(payload("resident_card", { pickup_date: "2030-10-20", pickup_time: "09:07", details: " " }))), { pickup_date: "2030-10-20", pickup_time: "09:07" });
  assert.deepEqual(JSON.parse(JSON.stringify(payload("resident_card", { pickup_date: "", pickup_time: "", details: "" }))), {});
  const corrections = fs.readFileSync("web/ticket-corrections.js", "utf8");
  const fields = vm.runInNewContext(`${corrections.match(/function ticketCorrectionFields\(item\) \{[\s\S]*?\n\}/)[0]}\nticketCorrectionFields;`);
  assert.ok(fields({ request_type: "resident_card" }).some(field => field.key === "pickup_date"));
  assert.ok(fields({ request_type: "resident_card" }).some(field => field.key === "pickup_time"));
});

test("image-only chat shows only the uploaded image without an invented user message", () => {
  const prompt = attachmentPrompt("", 1);
  assert.equal(prompt, "");
  assert.equal(attachmentPrompt("  Đọc biển số  ", 1), "Đọc biển số");
  assert.equal(attachmentPrompt("", 0), "");
});

test("form catalog does not expose administrative or unsupported write operations", () => {
  const start = source.indexOf("const residentEntryCatalog =");
  const end = source.indexOf("let residentEntryState =", start);
  const catalog = vm.runInNewContext(`${source.slice(start, end)}\nresidentEntryCatalog;`);
  assert.ok(catalog.profile.fields.some((field) => field.key === "full_name"));
  assert.ok(catalog.temporary_residency.fields.some((field) => field.key === "citizen_id" && field.pattern === "[0-9]{12}"));
  assert.ok(!catalog.management);
  assert.ok(!catalog.admin);
  assert.ok(!catalog.handover_inspection);
  assert.ok(!catalog.emergency_contact);
});

test("attachment autofill only fills blanks and respects manual edits, identity and options", () => {
  const workflow = fs.readFileSync("web/resident-workflows.js", "utf8");
  const start = workflow.indexOf("function fillBlankFormFields(");
  const end = workflow.indexOf("function carryVehicleImage(", start);
  const fill = vm.runInNewContext(`${workflow.slice(start, end)}\nfillBlankFormFields;`, { Event: class {} });
  const input = (value = "", changes = {}) => ({ value, dataset: {}, classList: { add() {} }, dispatchEvent() {}, ...changes });
  const fields = {
    person_name: input(), citizen_id: input("012345678901"), edited: input("", { dataset: { userEdited: "true" } }),
    identity: input("", { readOnly: true }), disabled: input("", { disabled: true }),
    vehicle_type: input("car", { tagName: "SELECT", options: [{ value: "car" }, { value: "motorbike" }] }),
    vehicle: input("", { tagName: "SELECT", options: [{ value: "car" }] }),
  };
  const filled = fill({ elements: { namedItem: key => fields[key] } }, { person_name: "Nguyễn Thu Hà", citizen_id: "999999999999", edited: "X", identity: "X", disabled: "X", vehicle_type: "motorbike", vehicle: "manager", role: "manager" });
  assert.deepEqual(Array.from(filled), ["person_name"]);
  assert.equal(fields.citizen_id.value, "012345678901");
  assert.equal(fields.vehicle_type.value, "car");
  assert.equal(fields.edited.value, "");
  assert.equal(fields.identity.value, "");
});
