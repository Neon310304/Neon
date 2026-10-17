import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const assignment = source.match(/omicxConfigured = \["omicx", "ptit"\][\s\S]*?geminiTtsConfigured = [^;]+;/)?.[0];

test("PTIT health enables microphone and server audio without dropping legacy providers", () => {
  assert.ok(assignment);
  for (const [stt, tts, expectedStt, expectedTts] of [
    ["ptit", "ptit", true, true], ["omicx", "gemini", true, true],
    ["configuration_required", "configuration_required", false, false], ["unknown", "unknown", false, false],
  ]) {
    const state = { data: { speech_to_text: stt, text_to_speech: tts }, omicxConfigured: false, geminiTtsConfigured: false };
    vm.runInNewContext(assignment, state);
    assert.equal(state.omicxConfigured, expectedStt);
    assert.equal(state.geminiTtsConfigured, expectedTts);
  }
});

test("speech still uses the authenticated backend and transcribes without auto-sending chat", () => {
  assert.match(source, /authorizedFetch\(`\$\{API\}\/speech\/synthesize`/);
  assert.match(source, /api\("\/speech\/transcribe"/);
  assert.match(source, /chatInput.value = \[recordingBaseText, result.transcript\]/);
  assert.doesNotMatch(source, /aitools\.ptit\.edu\.vn|PTIT_TTS_URL|PTIT_STT_URL/);
});
