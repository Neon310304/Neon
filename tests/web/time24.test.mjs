import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const start = source.indexOf("function initialize24HourTimeInput(");
const end = source.indexOf("function renderEntryFields(", start);

function timeControl(value = "", required = true) {
  const node = tag => ({
    tag, children: [], listeners: {}, attributes: {}, value: "",
    append(...children) { this.children.push(...children); },
    setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener(event, handler) { this.listeners[event] = handler; },
  });
  const input = {
    ...node("input"), id: "entry-move_time", type: "time", value, required,
    dataset: {}, classList: { remove() {} },
    dispatchEvent(event) { this.listeners[event.type]?.(); },
    closest() { return null; },
    after(control) { this.control = control; },
  };
  const initialize = vm.runInNewContext(`${source.slice(start, end)}\ninitialize24HourTimeInput;`, {
    document: { createElement: node }, Event: class { constructor(type) { this.type = type; } },
  });
  initialize(input, "Giờ chuyển đồ");
  return { input, selects: input.control.children.filter(child => child.tag === "select") };
}

test("24-hour picker exposes every hour and minute without AM/PM", () => {
  const { input, selects: [hour, minute] } = timeControl();
  assert.equal(input.type, "hidden");
  assert.equal(hour.required, true);
  assert.equal(minute.required, true);
  assert.deepEqual(hour.children.slice(1).map(option => option.value), Array.from({ length: 24 }, (_, index) => String(index).padStart(2, "0")));
  assert.equal(minute.children.length, 61);
  assert.equal(minute.children.at(-1).value, "59");
  assert.equal(input.control.attributes["role"], "group");
  assert.equal(input.control.attributes["aria-label"], "Giờ chuyển đồ, định dạng 24 giờ");
  assert.ok(!input.control.children.some(child => child.textContent === "24h"));
  assert.equal(hour.children[0].textContent, "--");
  assert.equal(minute.children[0].textContent, "--");
});

test("optional time selection requires both parts only while a slot is being selected", () => {
  const { input, selects: [hour, minute] } = timeControl("", false);
  assert.equal(hour.required, false);
  assert.equal(minute.required, false);
  hour.value = "09";
  hour.listeners.change();
  assert.equal(minute.required, true);
  assert.equal(input.value, "");
  minute.value = "07";
  minute.listeners.change();
  assert.equal(input.value, "09:07");
  hour.value = "";
  minute.value = "";
  minute.listeners.change();
  assert.equal(hour.required, false);
  assert.equal(minute.required, false);
});

test("partial selection stays editable and a complete selection serializes HH:MM", () => {
  const { input, selects: [hour, minute] } = timeControl();
  hour.value = "13";
  hour.listeners.change();
  assert.equal(hour.value, "13");
  assert.equal(input.value, "");
  minute.value = "07";
  minute.listeners.change();
  assert.equal(input.value, "13:07");
  hour.value = "00";
  hour.listeners.change();
  assert.equal(input.value, "00:07");
});

test("existing values and attachment autofill synchronize both selectors", () => {
  for (const value of ["00:00", "09:30", "13:45", "23:59"]) {
    const { input, selects } = timeControl(value);
    assert.equal(selects.map(select => select.value).join(":"), value);
    input.value = "18:01";
    input.listeners.input();
    assert.equal(selects.map(select => select.value).join(":"), "18:01");
  }
  for (const value of ["", "24:00", "12:60", "1:30", "09:30 AM"]) {
    assert.ok(timeControl(value).selects.every(select => select.value === ""));
  }
});

test("a manually selected hour is not overwritten by attachment autofill", () => {
  const { input, selects: [hour] } = timeControl();
  hour.value = "16";
  hour.listeners.change({ isTrusted: true });
  assert.equal(hour.value, "16");
  assert.equal(input.value, "");
  assert.equal(input.dataset.userEdited, "true");
});

test("create and correction forms share the same locale-independent time control", () => {
  assert.match(source, /if \(field.type === "time"\) initialize24HourTimeInput\(input, field.label\)/);
  const corrections = fs.readFileSync("web/ticket-corrections.js", "utf8");
  assert.match(corrections, /querySelectorAll\('input\[type="time"\]'\).forEach\(input => initialize24HourTimeInput/);
});
