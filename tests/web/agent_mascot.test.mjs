import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { agentVisualState, canAnimateAgent, createAgentDefinition, gazeForPointer, readMotionPreference, shouldReduceMotion } from "../../web/agent-mascot.js";
import { validateDefinition } from "../../web/vendor/open-mascot/definition.js";
import { motionAllowed } from "../../web/glass-motion.js";
import { sampleAnimation } from "../../web/vendor/open-mascot/motion.js";
import { buildScene } from "../../web/vendor/open-mascot/scene.js";

const html = fs.readFileSync("web/index.html", "utf8");
const appearance = fs.readFileSync("web/agent-mascot.js", "utf8");
const glass = fs.readFileSync("web/liquid-glass.css", "utf8");

test("waiting indicator uses themed surfaces and readable wrapping text", () => {
  const styles = fs.readFileSync("web/styles.css", "utf8");
  const theme = fs.readFileSync("web/appearance.css", "utf8");
  for (const selector of [".typing-indicator", ".thinking-orbit", ".chat-widget .typing-indicator"]) {
    const rule = styles.slice(styles.indexOf(`${selector} {`)).split("}")[0];
    assert.match(rule, /background: var\(--surface(?:-soft)?\)/);
    assert.doesNotMatch(rule, /background: (?:white|#[a-f0-9]+)/i);
  }
  const subtitle = styles.slice(styles.indexOf(".typing-copy small {")).split("}")[0];
  assert.match(subtitle, /color: var\(--muted\)/);
  assert.doesNotMatch(subtitle, /white-space: nowrap|text-overflow: ellipsis/);
  assert.match(theme, /\[data-theme="dark"\] \.chat-widget \.message\.assistant\.typing-message > div:last-child \{ background:transparent/);
});

test("mascot shows real request activity before success or attention states", () => {
  assert.equal(agentVisualState({ busy: true, failed: true, recentReply: true }), "thinking");
  assert.equal(agentVisualState({ failed: true, recentReply: true }), "attention");
  assert.equal(agentVisualState({ recentReply: true, focused: true }), "answered");
  assert.equal(agentVisualState({ focused: true }), "listening");
  assert.equal(agentVisualState(), "idle");
});

test("motion requires a visible resident surface and respects both pause preferences", () => {
  const allowed = { available: true, visible: true, reducedMotion: false, paused: false };
  assert.equal(canAnimateAgent(allowed), true);
  for (const override of [{ available: false }, { visible: false }, { reducedMotion: true }, { paused: true }]) {
    assert.equal(canAnimateAgent({ ...allowed, ...override }), false);
  }
});

test("system reduction is the default, while an explicit website choice can pause or resume", () => {
  assert.equal(shouldReduceMotion(), false);
  for (const systemReduced of [false, true]) {
    assert.equal(shouldReduceMotion({ preference: "auto", systemReduced }), systemReduced);
    assert.equal(shouldReduceMotion({ preference: "reduced", systemReduced }), true);
    assert.equal(shouldReduceMotion({ preference: "full", systemReduced }), false);
  }
  assert.equal(shouldReduceMotion({ preference: "invalid", systemReduced: true }), true);
});

test("saved motion settings preserve a legacy pause and safely handle unavailable storage", () => {
  const storage = values => ({ getItem: key => values[key] ?? null });
  assert.equal(readMotionPreference(storage({})), "auto");
  assert.equal(readMotionPreference(storage({ "moveinmate:reduce-motion": "true" })), "reduced");
  assert.equal(readMotionPreference(storage({ "moveinmate:reduce-motion": "false" })), "auto");
  assert.equal(readMotionPreference(storage({ "moveinmate:motion": "full", "moveinmate:reduce-motion": "true" })), "full");
  assert.equal(readMotionPreference(storage({ "moveinmate:motion": "reduced" })), "reduced");
  assert.equal(readMotionPreference(storage({ "moveinmate:motion": "corrupt" })), "auto");
  assert.equal(readMotionPreference({ getItem() { throw new Error("Storage blocked"); } }), "auto");
});

test("every initial and dynamically created mascot image uses a versioned URL", () => {
  const app = fs.readFileSync("web/app.js", "utf8");
  for (const source of [html, app]) {
    assert.doesNotMatch(source, /src="\/static\/agent-mascot\.svg"/);
    assert.match(source, /src="\/static\/agent-mascot\.svg\?v=[a-z\d-]+"/);
  }
});

test("Mallow retains the studio identity and a valid portable SVG definition", () => {
  const definition = createAgentDefinition();
  assert.equal(validateDefinition(definition).ok, true);
  assert.equal(definition.blob.shape, "soft");
  assert.equal(definition.blob.color, "#62a4ec");
  assert.equal(definition.face.eyeColor, "#254b78");
  assert.equal(definition.blob.renderMode, "projected-3d");
  for (const animation of ["idle", "listening", "thinking", "curious", "happy"]) {
    assert.ok(definition.animations[animation]);
  }
});

test("decorative mascot has a static local fallback and an accessible pause control", () => {
  for (const location of ["auth", "hero", "launcher", "header", "manager-launcher", "manager-header"]) {
    assert.match(html, new RegExp(`data-agent-mascot="${location}" aria-hidden="true"><img src="/static/agent-mascot.svg[^" ]+"[^>]*alt=""`));
  }
  assert.match(html, /id="agent-motion-toggle"[^>]*aria-label="Tắt hiệu ứng"[^>]*aria-pressed="false"/);
  assert.match(html, /src="\/static\/agent-mascot.js[^" ]*" type="module"/);
  assert.ok(html.indexOf("/static/liquid-glass.css") > html.indexOf("/static/theme.css"));
  const fallback = fs.readFileSync("web/agent-mascot.svg", "utf8");
  assert.match(fallback, /fill="url\(#ice\)"/);
  assert.doesNotMatch(fallback, /<script|<foreignObject|https?:\/\/(?!www.w3.org)/);
});

test("floating assistant avatar shrinks without shrinking the launcher touch target", () => {
  assert.match(glass, /\.chat-launcher-icon,\.chat-widget \.chat-widget-logo \{ width:44px; height:44px; flex:0 0 44px/);
  assert.match(glass, /\.chat-launcher-icon\[data-agent-mascot\] > img,\.chat-launcher-icon\[data-agent-mascot\] > svg \{ transform:scale\(1\.6\)/);
  assert.match(glass, /\.chat-launcher \{ min-height:66px/);
  assert.match(glass, /\.chat-launcher \{ width:64px; height:64px; min-width:64px/);
});

test("mascot destroys offscreen and hidden rendering and stores no conversation data", () => {
  assert.match(appearance, /IntersectionObserver/);
  assert.match(appearance, /scene\.controller\.destroy\(\)/);
  assert.match(appearance, /material-\$\{\+\+materialId\}/);
  assert.match(appearance, /controller\?\.destroy\(\)/);
  assert.match(appearance, /visibilitychange/);
  assert.match(appearance, /pagehide/);
  assert.match(appearance, /prefers-reduced-motion/);
  assert.match(appearance, /localStorage.setItem\("moveinmate:motion", preference\)/);
  assert.doesNotMatch(appearance, /fetch\(|WebSocket|WebGL|setInterval\(|innerHTML|https?:\/\/(?!www.w3.org)/);
});

test("liquid chrome stays translucent while long text has a readable layer and accessibility fallbacks", () => {
  assert.match(glass, /--glass-clear: rgba\(255, 255, 255, \.38\)/);
  assert.match(glass, /\.message.assistant > div:last-child \{ background: rgba\(255, 255, 255, \.92\)/);
  for (const preference of ["prefers-reduced-motion", "prefers-reduced-transparency", "prefers-contrast", "forced-colors"]) {
    assert.ok(glass.includes(preference));
  }
  assert.match(glass, /@supports not/);
  assert.match(glass, /\.motion-paused/);
  assert.doesNotMatch(glass, /@import|https?:\/\//);
});

test("logout label meets normal text contrast in default and hover states", () => {
  const base = glass.match(/(?:^|\n)\.logout-button\s*\{([^}]+)\}/)?.[1];
  const hover = glass.match(/(?:^|\n)\.logout-button:hover\s*\{([^}]+)\}/)?.[1];
  assert.ok(base && hover, "Logout needs explicit default and hover styles");
  const color = (rule, property) => rule.match(new RegExp(`(?:^|;)\\s*${property}:\\s*(#[\\da-f]{3}(?:[\\da-f]{3})?)\\s*(?:;|$)`, "i"))?.[1];
  const luminance = hex => {
    assert.ok(hex, "Contrast check requires an opaque color");
    const full = hex.length === 4 ? hex.slice(1).split("").map(value => value + value).join("") : hex.slice(1);
    return [0.2126, 0.7152, 0.0722].reduce((total, weight, index) => {
      const channel = parseInt(full.slice(index * 2, index * 2 + 2), 16) / 255;
      const linear = channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      return total + weight * linear;
    }, 0);
  };
  for (const [name, rule] of [["default", base], ["hover", hover]]) {
    const text = luminance(color(rule, "color") || color(base, "color"));
    const background = luminance(color(rule, "background"));
    const ratio = (Math.max(text, background) + 0.05) / (Math.min(text, background) + 0.05);
    assert.ok(ratio >= 4.5, `${name} logout contrast is ${ratio.toFixed(2)}:1; expected at least 4.5:1`);
  }
});

test("header controls fit small screens", () => {
  assert.match(glass, /body > header > \.header-actions \{[^\n]*flex-wrap: wrap/);
  assert.match(glass, /\.personal-trigger \{[^\n]*flex-shrink: 1; white-space: normal/);
});

test("idle animation changes projected geometry rather than sliding a static sticker", () => {
  const definition = createAgentDefinition();
  const paths = [0, 930, 2280, 3630].map(ms => buildScene(definition, sampleAnimation(definition, "idle", ms).pose).blob.path);
  assert.equal(new Set(paths).size, 4);
});

test("look targets point toward the cursor and stay bounded for small or offscreen mascots", () => {
  const bounds = { left: 100, top: 100, width: 200, height: 200 };
  assert.deepEqual(gazeForPointer({ x: 200, y: 200 }, bounds), { x: 0, y: 0 });
  assert.ok(gazeForPointer({ x: 350, y: 50 }, bounds).x > 0);
  assert.ok(gazeForPointer({ x: 350, y: 50 }, bounds).y < 0);
  assert.deepEqual(gazeForPointer({ x: -5000, y: 5000 }, bounds), { x: -1, y: 1 });
  const small = gazeForPointer({ x: 300, y: 100 }, { left: 10, top: 10, width: 0, height: 0 });
  assert.ok(Number.isFinite(small.x) && Number.isFinite(small.y));
});

test("animation preserves emotion and blinking but never supplies its own gaze direction", () => {
  const definition = createAgentDefinition();
  for (const name of ['idle', 'listening', 'thinking', 'curious', 'happy']) {
    for (const ms of [0, 930, 2280, 3630]) {
      const pose = sampleAnimation(definition, name, ms).pose;
      assert.deepEqual(pose.gaze, { x: 0, y: 0 });
      assert.equal(pose.blob.yaw, 0);
      assert.equal(pose.blob.pitch, 0);
    }
  }
});

test("scene interaction stops for every motion preference or hidden document", () => {
  assert.equal(motionAllowed(), true);
  for (const preference of [{ reduced: true }, { paused: true }, { hidden: true }]) {
    assert.equal(motionAllowed(preference), false);
  }
});

test("vendored SVG engine retains its MIT license and pinned upstream source", () => {
  const license = fs.readFileSync("web/vendor/open-mascot/LICENSE", "utf8");
  const origin = fs.readFileSync("web/vendor/open-mascot/README.md", "utf8");
  assert.match(license, /MIT License/);
  assert.match(license, /Copyright \(c\) 2026 0oooh/);
  assert.match(origin, /b83ca3863f4e35424342d42538873af1bacddf9f/);
});
