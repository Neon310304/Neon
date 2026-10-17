import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const start = source.indexOf("function resizeChatInput()");
const end = source.indexOf('chatInput.addEventListener("input"', start);

function composer() {
  const input = { style: {}, scrollHeight: 72 };
  let submitted = 0;
  const context = vm.createContext({
    chatInput: input,
    document: { getElementById: () => ({ requestSubmit: () => submitted++ }) },
  });
  vm.runInContext(source.slice(start, end), context);
  return { input, context, submitted: () => submitted };
}

test("Enter sends once; Shift+Enter and IME confirmation preserve typing", () => {
  const state = composer();
  const keydown = vm.runInContext("handleChatInputKeydown", state.context);
  for (const extra of [{ shiftKey: true }, { isComposing: true }, { keyCode: 229 }, { currentTarget: { disabled: true } }, { key: "a" }]) {
    keydown({ key: "Enter", currentTarget: {}, preventDefault: () => assert.fail("Typing was interrupted"), ...extra });
  }
  assert.equal(state.submitted(), 0);
  let prevented = 0;
  keydown({ key: "Enter", currentTarget: {}, preventDefault: () => prevented++ });
  assert.equal(state.submitted(), 1);
  assert.equal(prevented, 1);
});

test("composer grows for multiple lines and caps its height for long questions", () => {
  const state = composer();
  vm.runInContext("resizeChatInput()", state.context);
  assert.equal(state.input.style.height, "72px");
  state.input.scrollHeight = 500;
  vm.runInContext("resizeChatInput()", state.context);
  assert.equal(state.input.style.height, "128px");
});
