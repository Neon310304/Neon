import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync("web/index.html", "utf8");
const theme = fs.readFileSync("web/theme.css", "utf8");
const icons = fs.readFileSync("web/icons.svg", "utf8");
const styles = fs.readFileSync("web/styles.css", "utf8");

test("compact procedure cards put their actions below copy without changing expanded chat", () => {
  assert.match(styles, /\.chat-widget:not\(\.expanded\) \.a2ui-procedure\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\);/);
  assert.match(styles, /\.chat-widget:not\(\.expanded\) \.a2ui-procedure-copy\s*\{[^}]*display: flex;[^}]*flex-wrap: wrap;/);
  assert.match(styles, /\.chat-widget:not\(\.expanded\) \.a2ui-card:has\(\.a2ui-procedure\)\s*\{[^}]*padding: 0;[^}]*border: 0;/);
});

test("compact requirement and status labels share height and never wrap inside a badge", () => {
  const badges = styles.match(/\.chat-widget:not\(\.expanded\) \.a2ui-requirement,\s*\.chat-widget:not\(\.expanded\) \.a2ui-status\s*\{([^}]+)\}/)?.[1];
  assert.ok(badges);
  assert.match(badges, /box-sizing: border-box;/);
  assert.match(badges, /min-height: 30px;/);
  assert.match(badges, /align-items: center;/);
  assert.match(badges, /white-space: nowrap;/);
  assert.match(badges, /flex: 0 0 auto;/);
});

test("compact action buttons have consistent width and adapt height to their own container", () => {
  assert.match(styles, /\.chat-widget:not\(\.expanded\) \.a2ui-surface\s*\{[^}]*container: compact-procedures \/ inline-size;/);
  assert.match(styles, /\.chat-widget:not\(\.expanded\) \.a2ui-procedure > \.a2ui-button\s*\{[^}]*width: 100%;[^}]*min-height: 48px;/);
  assert.match(styles, /@container compact-procedures \(max-width: 190px\)\s*\{\s*\.chat-widget:not\(\.expanded\) \.a2ui-procedure > \.a2ui-button\s*\{ min-height: 64px;/);
});

test("refined theme loads after functional styles without external dependencies", () => {
  assert.ok(html.indexOf("/static/theme.css") > html.indexOf("/static/styles.css"));
  assert.ok(!/@import|https?:\/\//.test(theme));
  assert.match(theme, /prefers-reduced-motion: reduce/);
  assert.match(theme, /system-ui/);
});

test("every service keeps its real action and a local decorative icon", () => {
  const buttons = [...html.matchAll(/<button class="service-action"[^>]*>[\s\S]*?<\/button>/g)].map((match) => match[0]);
  assert.equal(buttons.length, 10);
  const allowed = new Set(["open_form", "open_tab", "open_procedures", "focus_input"]);
  for (const button of buttons) {
    assert.ok(allowed.has(button.match(/data-ui-action="([^"]+)"/)[1]));
    const icon = button.match(/\/static\/icons.svg#([^"]+)/)[1];
    assert.ok(icons.includes(`id="${icon}"`));
    assert.match(button, /<span aria-hidden="true">/);
    assert.match(button, /<strong>[^<]+<\/strong>/);
  }
});

test("illustration is local, dimensioned and decorative rather than fake resident data", () => {
  const images = [...html.matchAll(/<img[^>]+src="\/static\/residence-scene.svg"[^>]*>/g)].map((match) => match[0]);
  assert.equal(images.length, 2);
  for (const image of images) {
    assert.match(image, /width="520" height="340"/);
    assert.match(image, /alt="" aria-hidden="true"/);
  }
  const scene = fs.readFileSync("web/residence-scene.svg", "utf8");
  assert.ok(!/<script|<foreignObject|https?:\/\/(?!www.w3.org)/.test(scene));
});

test("mobile launcher retains an accessible name when its visual copy is hidden", () => {
  assert.match(html, /id="chat-launcher"[^>]*aria-label="Mở trợ lý MoveInMate"/);
  assert.match(theme, /\.chat-launcher-copy\s*\{\s*display: none;/);
  assert.match(html, /id="tab-resident-accounts"[^>]*data-tab="resident-accounts"/);
  assert.ok(!/id="tab-resident-accounts"[^>]*disabled/.test(html));
  assert.match(html, /id="resident-entry-consent" type="checkbox"\s*\//);
  assert.ok(!/id="resident-entry-consent"[^>]*checked/.test(html));
});
