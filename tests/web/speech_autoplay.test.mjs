import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const start = source.indexOf("function resetSpeechButton(");
const end = source.indexOf("function formatBytes(", start);
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { textContent: "", listeners: {}, classList: { add() {}, remove() {}, toggle() {} },
      setAttribute() {}, addEventListener(name, fn) { this.listeners[name] = fn; } });
    return elements.get(id);
  };
  const requests = [];
  const sources = [];
  const state = {
    activeSpeechButton: null, activeAudio: null, activeAudioUrl: "", speechAudioContext: null, activeAudioSource: null,
    speechPlaybackId: 0, speechAudioCache: new Map(), speechSupported: false, authReady: true,
    geminiTtsConfigured: true, autoTtsEnabled: true, API: "/api/v1", AbortController, DOMException,
    document: { getElementById: element }, setSpeechButtonAppearance: (button, appearance) => { button.appearance = appearance; },
    saveAutoTtsPreference() {},
    window: { AudioContext: class {
      state = "suspended";
      resume() { this.state = "running"; return Promise.resolve(); }
      close() { this.state = "closed"; return Promise.resolve(); }
      decodeAudioData() { return Promise.resolve({}); }
      createBufferSource() {
        const audio = { connect() {}, disconnect() {}, start() { this.started = true; }, stop() { this.stopped = true; } };
        sources.push(audio);
        return audio;
      }
    } },
    authorizedFetch: (url, options) => new Promise((resolve, reject) => requests.push({ resolve, reject, options })),
  };
  vm.createContext(state);
  vm.runInContext(source.slice(start, end), state);
  const button = () => ({ ...element(Symbol()), closest: () => ({ querySelector: () => ({ innerText: "Câu trả lời mới." }) }) });
  const answer = () => { const b = button(); return { button: b, querySelector: () => b }; };
  const respond = index => requests[index].resolve({ ok: true, blob: async () => ({ arrayBuffer: async () => new ArrayBuffer(1) }) });
  return { state, element, requests, sources, answer, respond };
}

test("new assistant answers actually start audio automatically; off leaves only manual playback", async () => {
  const { state, requests, sources, answer, respond } = setup();
  state.unlockSpeechPlayback();
  const first = answer();
  state.autoSpeakMessage(first);
  assert.equal(requests.length, 1);
  respond(0);
  await flush();
  assert.equal(sources[0].started, true);
  assert.equal(first.button.appearance, "playing");
  state.autoTtsEnabled = false;
  state.resetSpeechButton();
  assert.equal(sources[0].stopped, true);
  const second = answer();
  state.autoSpeakMessage(second);
  assert.equal(requests.length, 1);
  const manual = state.speakResponse("Câu trả lời mới.", second.button);
  respond(1);
  await manual;
  assert.equal(sources[1].started, true);
});

test("turning off during synthesis cancels playback, including a late successful response", async () => {
  const { state, element, requests, sources, answer, respond } = setup();
  state.unlockSpeechPlayback();
  state.autoSpeakMessage(answer());
  element("auto-tts-toggle").listeners.click();
  assert.equal(state.autoTtsEnabled, false);
  respond(0); // A provider can finish even after the fetch was aborted.
  await flush();
  assert.equal(sources.length, 0);
  element("auto-tts-toggle").listeners.click();
  assert.equal(requests.length, 1, "enabling must not read historical answers");
});

test("a stale failed synthesis cannot reset or replace a newer manual playback", async () => {
  const { state, sources, requests, answer, respond } = setup();
  state.unlockSpeechPlayback();
  state.autoSpeakMessage(answer());
  const latest = answer();
  const manual = state.speakResponse("Nội dung khác.", latest.button);
  respond(1);
  await manual;
  requests[0].reject(new Error("late provider error"));
  await flush();
  assert.equal(latest.button.appearance, "playing");
  assert.equal(sources[0].stopped, undefined);
  state.clearSpeechSession();
  assert.equal(sources[0].stopped, true);
  assert.equal(state.speechAudioCache.size, 0);
});

test("logout during audio decoding prevents a late source from starting", async () => {
  const { state, sources, answer, respond } = setup();
  state.unlockSpeechPlayback();
  let finishDecode;
  state.speechAudioContext.decodeAudioData = () => new Promise(resolve => { finishDecode = resolve; });
  state.autoSpeakMessage(answer());
  respond(0);
  await flush();
  state.authReady = false;
  state.clearSpeechSession();
  finishDecode({});
  await flush();
  assert.equal(sources.length, 0);
});

test("short first audio starts before the next chunk is ready and replay reuses audio", async () => {
  const { state, requests, sources, answer, respond } = setup();
  state.unlockSpeechPlayback();
  const message = answer();
  const text = "Câu trả lời về thủ tục cư dân. ".repeat(35);
  const pending = state.speakResponse(text, message.button);
  assert.ok(JSON.parse(requests[0].options.body).text.length <= 240);
  assert.equal(requests.length, 1);
  respond(0);
  await pending;
  assert.equal(sources[0].started, true);
  assert.equal(requests.length, 2);
  respond(1);
  await flush();
  sources[0].onended();
  await flush();
  assert.equal(sources[1].started, true);
  const count = requests.length;
  state.resetSpeechButton();
  await state.speakResponse(text, message.button);
  assert.equal(requests.length, count);
  assert.equal(sources[2].started, true);
});

test("stop aborts prefetch and prevents later chunks from playing", async () => {
  const { state, requests, sources, answer, respond } = setup();
  state.unlockSpeechPlayback();
  const pending = state.speakResponse("Hồ sơ cần kiểm tra trước khi gửi BQL. ".repeat(40), answer().button);
  respond(0);
  await pending;
  const ended = sources[0].onended;
  state.resetSpeechButton();
  assert.equal(requests[1].options.signal.aborted, true);
  respond(1);
  ended();
  await flush();
  assert.equal(sources.length, 1);
});

test("remote chunks retain all words within limits and bound unusually long replies", () => {
  const { state } = setup();
  const text = "Thông tin cư dân cần được đối chiếu rõ ràng. ".repeat(50).trim();
  const chunks = state.remoteSpeechChunks(text);
  assert.equal(chunks.join(" "), text);
  assert.ok(chunks[0].length <= 240);
  assert.ok(chunks.slice(1).every(chunk => chunk.length <= 700));
  assert.ok(state.remoteSpeechChunks("X".repeat(8000)).join("").length <= 5000);
});

test("device voice expands standalone BQL without changing displayed text or other words", async () => {
  const { state, element } = setup();
  const spoken = [];
  state.loadSpeechVoices = async () => [];
  state.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
  state.window.speechSynthesis = { speak: utterance => spoken.push(utterance.text) };
  const text = "BQL, bql và (BqL) duyệt hồ sơ. ABQL BQL1 không đổi.";
  const button = element("voice");
  await state.speakResponseLocally(text, button, () => true);
  assert.equal(spoken[0], "ban quản lí, ban quản lí và (ban quản lí) duyệt hồ sơ. ABQL BQL1 không đổi.");
  assert.equal(text, "BQL, bql và (BqL) duyệt hồ sơ. ABQL BQL1 không đổi.");
});
