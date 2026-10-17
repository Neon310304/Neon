import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const rendererStart = source.indexOf("const escapeHtml =");
const rendererEnd = source.indexOf("async function authorizedFetch", rendererStart);
assert.ok(rendererStart >= 0 && rendererEnd > rendererStart);
const renderMarkdown = vm.runInNewContext(`${source.slice(rendererStart, rendererEnd)}\nrenderMarkdown;`);

test("keeps all seven residence steps in one numbered list despite blank lines", () => {
  const steps = [
    "**Xác nhận thông tin cư trú**: Thông tin của bạn cần được xác nhận với Ban quản lý.",
    "**Đọc và xác nhận nội quy**: Bạn cần đọc và hiểu rõ các nội quy của khu cư dân.",
    "**Đăng ký thẻ cư dân**: Đây là thủ tục bắt buộc khi bạn chuyển vào.",
    "**Đăng ký tạm trú**: Nếu bạn có thêm người ở cùng, hãy thực hiện đăng ký tạm trú cho họ.",
    "**Đăng ký phương tiện**: Nếu bạn có phương tiện, hãy tiến hành đăng ký.",
    "**Đăng ký internet**: Nếu bạn cần internet, hãy đăng ký dịch vụ này.",
    "**Kiểm tra bàn giao và chỉ số điện nước**: Ghi nhận hiện trạng căn hộ và chỉ số điện nước ban đầu.",
  ];
  const markdown = `Để xác nhận thông tin cư trú, bạn cần thực hiện các bước sau:\n\n${steps.map((step) => `1. ${step}`).join("\n\n")}`;
  const html = renderMarkdown(markdown);
  assert.equal(html.match(/<ol>/g)?.length, 1);
  assert.equal(html.match(/<\/ol>/g)?.length, 1);
  assert.equal(html.match(/<li>/g)?.length, 7);
  assert.ok(html.endsWith("</li></ol>"));
  assert.match(html, /<li><strong>Xác nhận thông tin cư trú<\/strong>:/);
});

test("renders consecutive numbered markers as one list", () => {
  assert.equal(renderMarkdown("1. Một\n2. Hai\n3. Ba"), "<ol><li>Một</li><li>Hai</li><li>Ba</li></ol>");
});

test("handles Windows newlines and multiple empty lines", () => {
  assert.equal(renderMarkdown("1. Một\r\n\r\n  \r\n1. Hai\r\n\r\n"), "<ol><li>Một</li><li>Hai</li></ol>");
});

test("preserves the starting number of a new list", () => {
  assert.equal(renderMarkdown("4. Bốn\n\n5. Năm"), '<ol start="4"><li>Bốn</li><li>Năm</li></ol>');
});

test("supports ordered markers ending with a parenthesis", () => {
  assert.equal(renderMarkdown("1) Một\n\n1) Hai"), "<ol><li>Một</li><li>Hai</li></ol>");
});

test("closes a list before a separate paragraph and allows numbering to restart", () => {
  assert.equal(renderMarkdown("1. Một\n\nGhi chú\n\n1. Hai"), '<ol><li>Một</li></ol><p><span class="answer-text-line">Ghi chú</span></p><ol><li>Hai</li></ol>');
});

test("closes a list before a heading", () => {
  assert.equal(renderMarkdown("1. Một\n\n## Phần hai\n\n1. Hai"), "<ol><li>Một</li></ol><h2>Phần hai</h2><ol><li>Hai</li></ol>");
});

test("keeps unordered lists separate from ordered lists", () => {
  assert.equal(renderMarkdown("1. Một\n\n- Hai\n\n- Ba\n\n1. Bốn"), "<ol><li>Một</li></ol><ul><li>Hai</li><li>Ba</li></ul><ol><li>Bốn</li></ol>");
});

test("closes a list before a quote", () => {
  assert.equal(renderMarkdown("1. Một\n\n> Ghi chú\n\n1. Hai"), "<ol><li>Một</li></ol><blockquote>Ghi chú</blockquote><ol><li>Hai</li></ol>");
});

test("does not interpret numbered lines inside fenced code as list items", () => {
  assert.equal(renderMarkdown("1. Một\n\n```\n1. Mã\n\n1. Mã khác\n```\n\n1. Hai"), "<ol><li>Một</li></ol><pre><code>1. Mã\n\n1. Mã khác</code></pre><ol><li>Hai</li></ol>");
});

test("continues escaping HTML and formatting inline code in list items", () => {
  assert.equal(renderMarkdown('1. <script>alert("x")</script>\n\n1. `**mã**`'), "<ol><li>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;</li><li><code>**mã**</code></li></ol>");
});
