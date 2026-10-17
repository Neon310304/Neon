import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const start = source.indexOf("async function streamAGUI");
const end = source.indexOf("async function loadAuthConfig", start);
assert.ok(start >= 0 && end > start);
const transportSource = `let residentChecklistState = null; let residentOwnershipState = { profile: null };\n${source.slice(start, end)}`;

function responseFor(events, separator = "\n\n") {
  const bytes = new TextEncoder().encode(events.map((event) => `data: ${JSON.stringify(event)}${separator}`).join(""));
  let offset = 0;
  return new Response(new ReadableStream({
    pull(controller) {
      if (offset >= bytes.length) return controller.close();
      controller.enqueue(bytes.slice(offset, offset + 7));
      offset += 7;
    },
  }), { headers: { "Content-Type": "text/event-stream" } });
}

for (const separator of ["\n\n", "\r\n\r\n"]) {
  test(`reads split UTF-8 AG-UI events with ${JSON.stringify(separator)}`, async () => {
    const tokens = [];
    const context = {
      API: "/api/v1", TextDecoder, authGeneration: 1,
      authorizedFetch: async () => responseFor([
        { type: "RUN_STARTED" },
        { type: "TEXT_MESSAGE_CONTENT", delta: "Đã đọc hồ sơ." },
        { type: "CUSTOM", name: "a2ui", value: { surface_id: "answer" } },
        { type: "RUN_FINISHED", result: { answer: "Đã đọc hồ sơ." } },
      ], separator),
    };
    const stream = vm.runInNewContext(`${transportSource}\nstreamAGUI;`, context);
    const result = await stream("/agent/chat/ag-ui", {}, (event) => tokens.push(event.content));
    assert.equal(result.answer, "Đã đọc hồ sơ.");
    assert.equal(result.a2ui.surface_id, "answer");
    assert.deepEqual(tokens, ["Đã đọc hồ sơ."]);
  });
}

test("does not accept a run error or incomplete stream as success", async () => {
  for (const events of [[{ type: "RUN_ERROR", message: "Unavailable" }], [{ type: "RUN_STARTED" }]]) {
    const stream = vm.runInNewContext(`${transportSource}\nstreamAGUI;`, {
      API: "/api/v1", TextDecoder, authGeneration: 1, authorizedFetch: async () => responseFor(events),
    });
    await assert.rejects(stream("/agent/chat/ag-ui"));
  }
});

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.attributes = {};
  }

  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(event, listener) { this.listeners[event] = listener; }
  setAttribute(name, value) { this.attributes[name] = value; }
  set innerHTML(value) { throw new Error("A2UI must not inject HTML"); }
}

test("renders the fixed catalog as text and routes buttons through chat", () => {
  const prompts = [];
  const host = new Element("host");
  const render = vm.runInNewContext(`${transportSource}\nrenderA2UI;`, {
    document: {
      createElement: (name) => new Element(name),
      createDocumentFragment: () => new Element("fragment"),
      querySelectorAll: () => [],
    },
    sendSuggestedPrompt: (prompt) => prompts.push(prompt),
  });
  render({ surface_id: "test", messages: [
    { createSurface: { surfaceId: "test" } },
    { updateComponents: { surfaceId: "test", components: [
      { id: "root", component: "Column", children: ["text", "button", "unsafe"] },
      { id: "text", component: "Text", text: { path: "/answer" } },
      { id: "button", component: "Button", text: "Xem tiến độ", action: { event: { name: "send_message", context: { message: "Kiểm tra tiến độ" } } } },
      { id: "unsafe", component: "Script", text: "alert(1)" },
    ] } },
    { updateDataModel: { surfaceId: "test", path: "/", value: { answer: "<script>alert(1)</script>" } } },
  ] }, host);
  const children = host.children[0].children;
  assert.equal(children[0].textContent, "<script>alert(1)</script>");
  assert.equal(children[2].tagName, "fragment");
  children[1].listeners.click();
  assert.deepEqual(prompts, ["Kiểm tra tiến độ"]);
});

test("renders a compact choice next to its procedure with an accessible label", () => {
  const prompts = [];
  const host = new Element("host");
  const render = vm.runInNewContext(`${transportSource}\nrenderA2UI;`, {
    document: {
      createElement: (name) => new Element(name),
      createDocumentFragment: () => new Element("fragment"),
      querySelectorAll: () => [],
    },
    sendSuggestedPrompt: (prompt) => prompts.push(prompt),
  });
  const prompt = "Hướng dẫn thủ tục Đăng ký thẻ cư dân trong MoveInMate. Chỉ xem thông tin.";
  render({ surface_id: "choices", messages: [
    { createSurface: { surfaceId: "choices" } },
    { updateComponents: { surfaceId: "choices", components: [
      { id: "root", component: "Row", variant: "procedure", children: ["title", "choice", "unsafe"] },
      { id: "title", component: "Text", variant: "title", text: { path: "/procedures/0/title" } },
      { id: "choice", component: "Button", text: "Xem hướng dẫn", ariaLabel: "Xem hướng dẫn: Đăng ký thẻ cư dân", action: { event: { name: "send_message", context: { message: prompt, task_code: "resident_card" } } } },
      { id: "unsafe", component: "Button", text: "Unknown action", action: { event: { name: "delete_all", context: { message: "delete data" } } } },
    ] } },
    { updateDataModel: { surfaceId: "choices", path: "/", value: { procedures: [{ title: "Đăng ký thẻ cư dân" }] } } },
  ] }, host);
  const row = host.children[0];
  assert.equal(row.className, "a2ui-row a2ui-procedure");
  assert.equal(row.children[0].textContent, "Đăng ký thẻ cư dân");
  const button = row.children[1];
  assert.equal(button.type, "button");
  assert.equal(button.dataset.procedure, "resident_card");
  assert.equal(button.attributes["aria-label"], "Xem hướng dẫn: Đăng ký thẻ cư dân");
  button.listeners.click();
  row.children[2].listeners.click();
  assert.deepEqual(prompts, [prompt]);
});

test("routes only allow-listed navigation and draft actions without sending chat", () => {
  const calls = [];
  const handle = vm.runInNewContext(`${transportSource}\nhandleUIAction;`, {
    sendSuggestedPrompt: (message) => calls.push(["send", message]),
    openProcedurePicker: () => calls.push(["directory"]),
    openResidentTab: (tab) => calls.push(["tab", tab]),
    focusChatDraft: (message) => calls.push(["draft", message]),
    openResidentEntry: (form) => calls.push(["form", form]),
  });
  handle("open_procedures");
  handle("open_tab", { tab: "progress" });
  handle("open_tab", { tab: "regulations" });
  handle("focus_input", { message: "Cần BQL xác nhận" });
  handle("open_tab", { tab: "management" });
  handle("open_tab", { tab: 'progress"] script' });
  handle("delete_all", { message: "Delete" });
  handle("open_form", { form: "vehicle_registration" });
  handle("open_form", { form: "management" });
  handle("send_message", { message: 42 });
  assert.deepEqual(calls, [["directory"], ["tab", "progress"], ["tab", "regulations"], ["draft", "Cần BQL xác nhận"], ["form", "vehicle_registration"]]);
});

test("management question opens a real editable ticket draft, never sends a chat or ticket directly", () => {
  const calls = [];
  const handle = vm.runInNewContext(`${transportSource}\nhandleUIAction;`, {
    sendSuggestedPrompt: () => calls.push(["send"]),
    openResidentEntry: (...args) => calls.push(args),
  });
  handle("open_form", { form: "general_support", draft_question: "Kính gửi Ban quản lý, tôi cần hướng dẫn cư trú." });
  assert.deepEqual(calls[0], ["general_support", "Kính gửi Ban quản lý, tôi cần hướng dẫn cư trú."]);
  handle("open_form", { form: "resident_card", draft_question: "Không dùng cho form khác" });
  assert.deepEqual(calls[1], ["resident_card", ""]);
  handle("open_form", { form: "general_support", draft_question: "a".repeat(3000) });
  assert.equal(calls[2][1].length, 1800);
});

test("an exact owned-ticket shortcut uses the dedicated navigator without accepting invalid IDs", () => {
  const calls = [];
  const handler = source.slice(source.indexOf("function handleUIAction("), source.indexOf("function normalizeProcedureSearch("));
  const handle = vm.runInNewContext(`${handler}\nhandleUIAction;`, {
    openRelatedTicket: requestId => calls.push(["ticket", requestId]),
    openResidentTab: tab => calls.push(["tab", tab]),
  });
  handle("open_tab", { tab: "tickets", request_id: "REQ-12345678" });
  handle("open_tab", { tab: "tickets", request_id: 'REQ-12345678" onclick="alert(1)' });
  assert.deepEqual(calls, [["ticket", "REQ-12345678"], ["tab", "tickets"]]);
});

test("fallback answers keep both required actions and only link confirmed ticket results", () => {
  const calls = [];
  const fallback = source.slice(source.indexOf("function renderAnswerFallback("), source.indexOf('document.getElementById("procedure-search").addEventListener'));
  const render = vm.runInNewContext(`${fallback}\nrenderAnswerFallback;`, {
    document: { createElement: name => new Element(name) },
    handleUIAction: (...args) => calls.push(args),
  });
  const host = new Element("host");
  render(host, "", [{ tool: "create_management_request", summary: "created:REQ-12345678" }]);
  let buttons = host.children.flatMap(row => row.children);
  assert.deepEqual(buttons.map(button => button.textContent), ["Xem REQ-12345678", "Yêu cầu khác", "Xem việc cần làm"]);
  assert.deepEqual(host.children.at(-1).children.map(button => button.textContent), ["Yêu cầu khác", "Xem việc cần làm"]);
  assert.equal(host.children[0].children.length, 1);
  buttons[0].listeners.click();
  assert.equal(calls[0][0], "open_tab");
  assert.equal(calls[0][1].request_id, "REQ-12345678");
  render(host, "", [{ tool: "create_management_request", summary: 'created:REQ-12345678" onclick="alert(1)' }]);
  buttons = host.children[0].children;
  assert.deepEqual(buttons.map(button => button.textContent), ["Yêu cầu khác", "Xem việc cần làm"]);
});

test("procedure search ignores accents and completion filter uses official status", () => {
  const matches = vm.runInNewContext(`${transportSource}\nmatchesProcedure;`);
  assert.ok(matches("Đăng ký thẻ cư dân", "approved", "the cu dan", "pending"));
  assert.ok(matches("Đăng ký thẻ cư dân", "completed", "THẺ", "completed"));
  assert.ok(!matches("Đăng ký thẻ cư dân", "approved", "", "completed"));
  assert.ok(!matches("Đăng ký thẻ cư dân", "completed", "", "pending"));
  assert.ok(!matches("Đăng ký xe", "pending", "internet", "all"));
});

test("disables duplicate chat actions but leaves navigation usable", () => {
  const host = new Element("host");
  const render = vm.runInNewContext(`${transportSource}\nrenderA2UI;`, {
    document: {
      createElement: (name) => new Element(name),
      createDocumentFragment: () => new Element("fragment"),
      querySelectorAll: () => [],
      getElementById: () => ({ disabled: true }),
    },
  });
  render({ surface_id: "busy", messages: [
    { createSurface: { surfaceId: "busy" } },
    { updateComponents: { surfaceId: "busy", components: [
      { id: "root", component: "Row", children: ["send", "tab", "unknown"] },
      { id: "send", component: "Button", action: { event: { name: "send_message", context: { message: "Hướng dẫn" } } } },
      { id: "tab", component: "Button", action: { event: { name: "open_tab", context: { tab: "progress" } } } },
      { id: "unknown", component: "Button", action: { event: { name: "delete_all" } } },
    ] } },
  ] }, host);
  const buttons = host.children[0].children;
  assert.equal(buttons[0].disabled, true);
  assert.equal(buttons[1].disabled, false);
  assert.equal(buttons[2].disabled, true);
});
