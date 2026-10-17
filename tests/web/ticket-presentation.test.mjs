import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

const app = fs.readFileSync("web/app.js", "utf8");
const detailsSource = app.match(/function requestDetails\(item, showCompletedMark = false\) \{[\s\S]*?\n\}/)[0];
const ticketsSource = app.match(/async function loadResidentTickets\(\) \{[\s\S]*?\n\}/)[0];
const managementSource = app.match(/function renderRequests\(\) \{[\s\S]*?\n\}/)[0];
const notesSource = app.slice(app.indexOf("const residentProcedureNotices ="), app.indexOf("let residentEntryState ="));

async function renderTicket(overrides = {}, role = "resident") {
  const list = { innerHTML: "", insertAdjacentHTML() {}, querySelectorAll: () => [] };
  const item = {
    request_id: "REQ-TEST", request_type: "general_support", status: "submitted",
    payload: { details: "Please review" }, timeline: [], ...overrides,
  };
  const context = {
    authReady: true, hasRole: () => true, document: { getElementById: () => list },
    residentTicketPage: { loadId: 0 }, authGeneration: 1, isSessionCurrent: () => true,
    setTicketLoading() {}, ticketQuery: () => "limit=20", alignTicketPage: () => false,
    updateTicketPagination() {}, addVehicleDocumentActions() {},
    api: async () => ({ requests: [item] }), residentPath: (path) => path,
    escapeHtml: (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
    requestTypeIcon: () => "", requestTypeLabels: {}, requestStatusLabels: {},
    managementRequests: [item], managementPage: { total: 1 }, requestActionMap: {},
    updateRequestFilterState() {}, residentProfileDetails: () => "",
    displayDate: value => value,
    requestNextSteps: { completed: "Completed", submitted: "Waiting", needs_info: "More information required" },
  };
  vm.createContext(context);
  vm.runInContext(`${notesSource}\n${detailsSource}\n${ticketsSource}\n${managementSource}`, context);
  if (role === "manager") context.renderRequests();
  else await context.loadResidentTickets();
  return { html: list.innerHTML, context, item };
}

test("procedure guidance remains in registration forms, not either role's tickets", async () => {
  const types = ["resident_card", "vehicle_registration", "temporary_residency", "account_registration", "card_replacement", "visitor_registration", "move_in_schedule", "general_support", "verify_residency"];
  for (const role of ["resident", "manager"]) {
    for (const request_type of types) {
      const { html } = await renderTicket({ request_type }, role);
      assert.doesNotMatch(html, /ticket-procedure-note|internet-ticket-notice|Lưu ý:/);
    }
  }
  const guidance = vm.runInNewContext(`${notesSource}\nresidentProcedureNotices;`);
  assert.match(guidance.resident_card, /ngày và giờ/);
  assert.match(guidance.internet_registration, /người tư vấn/);
  assert.match(app, /: residentProcedureNotices\[type\]/);
});

test("active internet tickets show a short colored message specific to each role", async () => {
  for (const role of ["resident", "manager"]) {
    const message = role === "resident" ? "BQL sẽ cử người lên tư vấn." : "BQL cần cử người phụ trách lên tư vấn.";
    for (const status of ["submitted", "in_review", "needs_info", "approved"]) {
      const { html } = await renderTicket({ request_type: "internet_registration", status }, role);
      assert.match(html, /class="internet-ticket-notice"/);
      assert.ok(html.includes(message));
      assert.doesNotMatch(html, /Lưu ý:|chưa phải hợp đồng|đã được giải quyết/);
      assert.equal((html.match(/class="internet-ticket-notice"/g) || []).length, 1);
    }
  }
  const styles = fs.readFileSync("web/portal.css", "utf8");
  assert.match(styles, /\.internet-ticket-notice \{[^}]*color:var\(--status-pending-ink\);[^}]*background:var\(--status-pending-soft\);/);
});

test("internet reminders disappear when completed, rejected or cancelled without losing processing notes", async () => {
  for (const role of ["resident", "manager"]) {
    for (const status of ["completed", "rejected", "cancelled_by_resident", "unknown"]) {
      const { html } = await renderTicket({ request_type: "internet_registration", status, decision_reason: "BQL đã xử lý hồ sơ" }, role);
      assert.doesNotMatch(html, /internet-ticket-notice|ticket-procedure-note|BQL sẽ cử|BQL cần cử/);
      assert.match(html, /BQL đã xử lý hồ sơ/);
    }
  }
});

test("card tickets retain collection slots without the registration-only guidance", async () => {
  for (const role of ["resident", "manager"]) {
    const card = await renderTicket({ request_type: "resident_card", payload: { pickup_date: "2030-10-20", pickup_time: "13:07" } }, role);
    assert.match(card.html, /Ngày mong muốn nhận thẻ/);
    assert.match(card.html, /13:07/);
    assert.doesNotMatch(card.html, /BQL sẽ xem xét|Lưu ý:/);
    assert.match(card.context.requestDetails(card.item), /13:07/);
  }
});

test("cancel decision uses a solid red button rather than the white glass group", () => {
  const styles = fs.readFileSync("web/styles.css", "utf8");
  assert.match(styles, /#decision-cancel \{[^}]*color: #fff;[^}]*background: #b93646;/);
  assert.match(styles, /#decision-cancel:focus-visible/);
  assert.doesNotMatch(styles, /\.empty-state button,\s*#decision-cancel/);
  const portal = fs.readFileSync("web/portal.css", "utf8");
  assert.match(portal, /\.account-workspace-tabs \{[^}]*width: 100%;/);
  assert.match(portal, /\.vehicle-upload > \.portal-inline-actions \{[^}]*margin-block: 16px;/);
});

test("both finished tabs name cancelled tickets and the reading action is vertically centered", () => {
  const html = fs.readFileSync("web/index.html", "utf8");
  for (const role of ["resident", "manager"]) {
    const tab = html.match(new RegExp(`<button id="${role}-stage-finished"[^>]*>([\\s\\S]*?)<\\/button>`))[1];
    assert.match(tab, /Ticket đã hoàn thành\/bị huỷ/);
    assert.match(tab, /data-stage-count="finished"/);
  }
  const styles = fs.readFileSync("web/portal.css", "utf8");
  assert.match(styles, /#task-list \.task-side:has\(> \.task-regulations\) \{ align-self:center; \}/);
});

test("completed resident ticket places one check beside the BQL note without a next step", async () => {
  const { html, context, item } = await renderTicket({ status: "completed", decision_reason: "BQL finished" });
  assert.match(html, /status-border-completed/);
  assert.match(html, /<dd><span>BQL finished<\/span><span class="ticket-completed-mark"/);
  assert.equal((html.match(/class="ticket-completed-mark"/g) || []).length, 1);
  assert.ok(!html.includes('class="ticket-next-step"'));
  assert.ok(!context.requestDetails(item).includes("ticket-completed-mark"), "Manager details keep their existing display");
});

test("unfinished ticket preserves its next step, follow-up actions and history", async () => {
  const { html } = await renderTicket({
    status: "needs_info",
    timeline: [{ status: "needs_info", created_at: "2026-10-10T02:00:00Z", reason: "Missing <document>" }],
  });
  assert.match(html, /status-border-needs_info/);
  assert.match(html, /class="ticket-next-step"/);
  assert.match(html, /class="ticket-chat-action"/);
  assert.match(html, /class="cancel-ticket"/);
  assert.match(html, /class="ticket-timeline-count">1<\/span>/);
  assert.match(html, /Missing &lt;document>/);
  assert.match(html, /<time datetime="2026-10-10T02:00:00Z"/);
  assert.ok(!html.includes("ticket-completed-mark"));
});

test("completed ticket without a decision note still shows an accessible check", async () => {
  const { html } = await renderTicket({ status: "completed" });
  assert.match(html, /class="ticket-completed-mark" role="img" aria-label="Completed"/);
  assert.ok(!html.includes('class="ticket-next-step"'));
});

test("completed history keeps every reached milestone and marks only the latest step current", async () => {
  const timeline = ["submitted", "approved", "completed"].map((status) => ({ status, created_at: "2026-10-10T07:00:00Z" }));
  const { html } = await renderTicket({ status: "completed", timeline });
  assert.match(html, /class="ticket-timeline-step" data-status="submitted"/);
  assert.match(html, /class="ticket-timeline-step" data-status="approved"/);
  assert.match(html, /class="ticket-timeline-step current" data-status="completed" aria-current="step"/);
  assert.equal((html.match(/aria-current="step"/g) || []).length, 1);
  assert.equal((html.match(/class="ticket-timeline-step/g) || []).length, timeline.length);
});

test("resubmitted history preserves earlier requests for information instead of painting them approved", async () => {
  const statuses = ["submitted", "needs_info", "submitted", "in_review", "approved", "completed"];
  const { html } = await renderTicket({ status: "completed", timeline: statuses.map((status) => ({ status })) });
  const renderedStatuses = [...html.matchAll(/class="ticket-timeline-step(?: current)?" data-status="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(renderedStatuses, statuses);
  assert.equal((html.match(/aria-current="step"/g) || []).length, 1);
});

test("history escapes unexpected status values and reasons", async () => {
  const { html } = await renderTicket({ timeline: [{ status: 'unknown" onclick="bad', reason: "<script>bad()</script>", created_at: "not-a-date" }] });
  assert.match(html, /data-status="unknown&quot; onclick=&quot;bad"/);
  assert.match(html, /&lt;script>bad\(\)&lt;\/script>/);
  assert.ok(!html.includes("<time"));
  assert.ok(!html.includes('onclick="bad'));
});

test("all reached history checkmarks share the completion blue", () => {
  const css = fs.readFileSync("web/portal.css", "utf8");
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  for (const status of ["submitted", "in_review", "approved", "completed"]) {
    const selector = `.ticket-timeline li[data-status="${status}"]`;
    const declarations = rules.filter(([, selectors]) => selectors.split(",").some((value) => value.trim() === selector)).map(([, , body]) => body).join(" ");
    for (const part of ["ink", "solid", "halo"]) {
      assert.match(declarations, new RegExp(`--timeline-${part}: var\\(--status-completed-${part}\\)`), `${status} uses the completion ${part}`);
    }
    assert.match(declarations, /--timeline-glyph: #fff;/);
    assert.doesNotMatch(declarations, /var\(--status-(?:pending|approved|error)-/);
    const glyphRule = rules.find(([, selectors]) => selectors.split(",").some((value) => value.trim() === `${selector}::before`));
    assert.ok(glyphRule, `${status} has a history glyph`);
    assert.match(glyphRule[2], /content: "✓";/);
  }
});

test("ticket left stripes retain their semantic status colors independently of history", () => {
  const css = fs.readFileSync("web/styles.css", "utf8");
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const palettes = { submitted: "pending", in_review: "pending", needs_info: "error", rejected: "error", approved: "approved", completed: "completed" };
  for (const [status, palette] of Object.entries(palettes)) {
    const selector = `.request-card.status-border-${status}::before`;
    const rule = rules.find(([, selectors]) => selectors.split(",").some((value) => value.trim() === selector));
    assert.ok(rule, `${status} has a ticket stripe`);
    assert.match(rule[2], new RegExp(`background: var\\(--status-${palette}-solid\\)`));
  }
});

test("semantic status colors keep readable labels and marker glyphs", () => {
  const css = fs.readFileSync("web/styles.css", "utf8");
  const token = (name) => css.match(new RegExp(`--status-${name}: (#[a-f0-9]+);`, "i"))[1];
  const luminance = (hex) => {
    const channels = hex.match(/[a-f0-9]{2}/gi).map((channel) => {
      const value = parseInt(channel, 16) / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const contrast = (foreground, background) => {
    const values = [luminance(foreground), luminance(background)].sort((first, second) => second - first);
    return (values[0] + 0.05) / (values[1] + 0.05);
  };
  for (const status of ["pending", "approved", "completed", "error"]) {
    assert.ok(contrast(token(`${status}-ink`), token(`${status}-soft`)) >= 4.5, `${status} label contrast`);
    const glyph = status === "pending" ? token("pending-ink") : "#ffffff";
    assert.ok(contrast(glyph, token(`${status}-solid`)) >= 4.5, `${status} marker contrast`);
  }
  assert.notEqual(token("approved-solid"), token("completed-solid"));
});
