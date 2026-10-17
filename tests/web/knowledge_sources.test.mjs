import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/app.js", "utf8");
const summary = source.slice(source.indexOf("function humanizeToolSummary("), source.indexOf("function addMessage("));
const { humanizeToolSummary, toolTraceSymbol, renderCitation, renderToolTraces } = vm.runInNewContext(`${summary}\n({humanizeToolSummary,toolTraceSymbol,renderCitation,renderToolTraces});`, {
  escapeHtml: value => String(value).replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
  URL, requestStatusLabels: {}, toolLabels: { create_management_request: "Đã gửi yêu cầu đến Ban quản lý" },
});

test("a missing-fields attempt is historical, not a contradictory current ticket status", () => {
  const html = renderToolTraces([
    { tool: "create_management_request", summary: "not_created:missing_fields" },
    { tool: "create_management_request", summary: "created:REQ-0D648588" },
  ]);
  assert.match(html, /data-phase="prior-attempt"/);
  assert.match(html, /Kiểm tra trước đó.*Thông tin còn thiếu ở bước này/);
  assert.match(html, /data-outcome="success".*REQ-0D648588/);
  assert.doesNotMatch(html, /Chưa gửi yêu cầu|Chưa tạo yêu cầu/);
});

test("failed, independent and later failed attempts remain visible", () => {
  const failed = { tool: "create_management_request", summary: "not_created:missing_fields" };
  assert.match(renderToolTraces([failed]), /Chưa gửi yêu cầu.*còn thiếu thông tin/);
  assert.doesNotMatch(renderToolTraces([failed]), /data-outcome="success"|prior-attempt/);
  const independent = renderToolTraces([failed, { tool: "update_task_status", summary: "status:completed" }]);
  assert.match(independent, /Chưa gửi yêu cầu.*còn thiếu thông tin/);
  const laterFailure = renderToolTraces([{ tool: failed.tool, summary: "created:REQ-0D648588" }, failed]);
  assert.match(laterFailure, /Chưa gửi yêu cầu/);
  assert.doesNotMatch(laterFailure, /prior-attempt/);
  const separateFailure = renderToolTraces([{ tool: failed.tool, summary: "error" }, { tool: failed.tool, summary: "created:REQ-0D648588" }]);
  assert.match(separateFailure, /data-outcome="error".*Lần thử này không thành công/);
});

test("an existing ticket is never represented as a newly sent request", () => {
  const html = renderToolTraces([{ tool: "create_management_request", summary: "existing:REQ-0D648588" }]);
  assert.match(html, /Yêu cầu đã tồn tại, không gửi mới/);
  assert.doesNotMatch(html, /data-outcome="success"|Đã gửi yêu cầu/);
});

test("insufficient sources have accurate labels and no success tick", () => {
  for (const status of ["no_valid_source", "insufficient_site_evidence", "insufficient_evidence"]) {
    assert.doesNotMatch(humanizeToolSummary(`retrieval:${status}`), /Đã tìm thấy/);
    assert.equal(toolTraceSymbol(`retrieval:${status}`), "ℹ");
  }
  assert.match(humanizeToolSummary("retrieval:application_guidance"), /chức năng đang chạy/);
  assert.doesNotMatch(humanizeToolSummary("retrieval:application_guidance"), /demo|BQL/);
  assert.equal(toolTraceSymbol("retrieval:supported"), "✓");
});

test("citations identify demo and public-law sources and escape titles", () => {
  const item = { title: "<img>", version: "1", source_url: "https://congbao.chinhphu.vn/example" };
  assert.match(renderCitation({ ...item, data_partition: "demo_bql" }), /BQL minh họa · demo/);
  assert.match(renderCitation({ ...item, data_partition: "public_law" }), /Pháp luật tham khảo/);
  assert.doesNotMatch(renderCitation(item), /<img>/);
  assert.doesNotMatch(renderCitation({ ...item, source_url: "javascript:alert(1)" }), /href=/);
});

const knowledge = source.slice(source.indexOf("function documentPartition("), source.indexOf("async function loadRegulations("));
const { documentPartition, renderKnowledgeGuides } = vm.runInNewContext(`${knowledge}\n({documentPartition,renderKnowledgeGuides});`, {
  residentEntryCatalog: { vehicle_registration: {} },
  escapeHtml: value => String(value).replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
});

test("application guides keep allow-listed actions without the removed BQL workflow", () => {
  const html = renderKnowledgeGuides({
    guides: [{ title: "Đăng ký xe", form: "vehicle_registration", steps: ["Ảnh bắt buộc cả khi nhập tay."] }, { title: "<script>", form: 'bad" onclick="alert(1)', tab: "invalid", steps: [] }],
    demo_workflow: { label: "Luồng BQL minh họa", description: "Không phải quy định đã xác minh." },
  });
  assert.match(html, /Hướng dẫn thao tác trong ứng dụng/);
  assert.doesNotMatch(html, /Luồng BQL|demo|Không phải quy định đã xác minh/);
  assert.match(html, /data-knowledge-form="vehicle_registration"/);
  assert.doesNotMatch(html, /onclick|<script>/);
  assert.equal(documentPartition({ site_id: "national" }), "public_law");
  assert.equal(documentPartition({ source_type: "demo_bql" }), "demo_bql");
  assert.equal(documentPartition({ source_type: "building_policy" }), "building_policy");
});
