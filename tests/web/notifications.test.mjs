import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/notifications.js", "utf8");

function feedContext(api, fields = {}) {
  const element = { hidden: false, textContent: "", value: "", addEventListener() {}, classList: { contains: () => false } };
  const context = vm.createContext({ api, authReady: true, authGeneration: 1,
    hasRole: () => true, isSessionCurrent: generation => generation === 1,
    residentPath: path => path, URLSearchParams, encodeURIComponent,
    escapeHtml: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
    requestStatusLabels: {}, document: { getElementById: id => fields[id] || element },
  });
  vm.runInContext(source.slice(0, source.indexOf('for (const suffix of')), context);
  context.renderNotificationFeed = () => {};
  context.updateNotificationSummary = () => {};
  return context;
}

test("a grouped page replaces old results and keeps all updates of each ticket together", async () => {
  const context = feedContext(async () => ({ items: [{ request_id: "REQ-A", events: [{ event_id: 100 }, { event_id: 20 }] }], latest_event_id: 100, unread_count: 80, total: 21, has_more: true }));
  vm.runInContext('notificationState.items.set("REQ-OLD",{request_id:"REQ-OLD"});', context);
  await context.loadNotificationFeed();
  assert.equal(vm.runInContext('notificationState.items.size', context), 1);
  assert.equal(vm.runInContext('notificationState.items.has("REQ-OLD")', context), false);
  assert.equal(vm.runInContext('notificationState.items.get("REQ-A").events.length', context), 2);
  assert.equal(vm.runInContext('notificationState.hasMore', context), true);
});

test("a stale feed cannot write into a replacement account session", async () => {
  let resolve;
  const context = feedContext(() => new Promise(done => { resolve = done; }));
  const loading = context.loadNotificationFeed();
  vm.runInContext('notificationState={items:new Map(),latest:0,unread:0,loading:false}; authGeneration=2;', context);
  resolve({ items: [{ request_id: "REQ-A" }], latest_event_id: 12, unread_count: 1, total: 1 });
  await loading;
  assert.equal(vm.runInContext('notificationState.items.size', context), 0);
});

test("read-all sends the last observed event boundary and leaves concurrent new events unread", async () => {
  const calls = [];
  const context = feedContext(async (path, options) => {
    calls.push({ path, body: options?.body });
    if (options) return {};
    return { items: [{ request_id: "REQ-A", events: [{ event_id: 13, read_at: null }, { event_id: 12, read_at: "2026-10-10" }] }], latest_event_id: 13, unread_count: 1, total: 1, has_more: false };
  });
  vm.runInContext('notificationState.latest=12; notificationState.unread=1;', context);
  await context.markNotificationsRead();
  assert.deepEqual(JSON.parse(calls[0].body), { through_event_id: 12 });
  assert.equal(vm.runInContext('notificationState.unread', context), 1);
  assert.equal(vm.runInContext('notificationState.items.get("REQ-A").events[0].read_at', context), null);
  assert.ok(vm.runInContext('notificationState.items.get("REQ-A").events[1].read_at', context));
});

test("notification query applies the same search, type, status and sent-date filters as tickets", () => {
  const fields = Object.fromEntries(Object.entries({ search: "  30K-123.45 ", "type-filter": "vehicle_registration", "status-filter": "approved", "date-from": "2026-10-01", "date-to": "2026-10-10" }).map(([key, value]) => [`notification-${key}`, { value }]));
  const context = feedContext(async () => {}, fields);
  vm.runInContext('notificationState.offset=20;', context);
  const query = new URLSearchParams(context.notificationQuery());
  assert.equal(query.get("q"), "30K-123.45");
  assert.equal(query.get("request_type"), "vehicle_registration");
  assert.equal(query.get("status"), "approved");
  assert.equal(query.get("date_from"), "2026-10-01");
  assert.equal(query.get("date_to"), "2026-10-10");
  assert.equal(query.get("offset"), "20");
});

test("a slow previous filter response cannot overwrite a newer filter", async () => {
  const pending = [];
  const context = feedContext(() => new Promise(resolve => pending.push(resolve)));
  const first = context.loadNotificationFeed();
  const second = context.loadNotificationFeed();
  pending[1]({ items: [{ request_id: "REQ-NEW" }], latest_event_id: 20, total: 1, unread_count: 1, has_more: false });
  await second;
  pending[0]({ items: [{ request_id: "REQ-OLD" }], latest_event_id: 10, total: 1, unread_count: 1, has_more: false });
  await first;
  assert.equal(vm.runInContext('notificationState.items.has("REQ-NEW")', context), true);
  assert.equal(vm.runInContext('notificationState.items.has("REQ-OLD")', context), false);
});

test("group read acknowledges only that ticket through its displayed update", async () => {
  const calls = [];
  const context = feedContext(async (path, options) => {
    calls.push({ path, options });
    return options ? {} : { items: [], latest_event_id: 30, unread_count: 1, total: 0, has_more: false };
  });
  vm.runInContext('notificationState.latest=30; notificationState.items.set("REQ-A",{events:[{event_id:12}]});', context);
  await context.markNotificationsRead("REQ-A");
  assert.equal(calls[0].path, "/notifications/tickets/REQ-A/read");
  assert.deepEqual(JSON.parse(calls[0].options.body), { through_event_id: 12 });
});

test("group history preserves all events, escapes notes and marks one latest step", () => {
  const context = feedContext(async () => {});
  const markup = context.notificationHistoryMarkup([
    { event_id: 3, status: "completed", reason: "<img onerror=bad>", created_at: "2026-10-10T08:00:00Z", read_at: null },
    { event_id: 2, status: "approved", reason: "", created_at: "2026-10-10T07:00:00Z", read_at: "2026-10-10" },
  ]);
  assert.equal((markup.match(/aria-current="step"/g) || []).length, 1);
  assert.ok(markup.indexOf('data-status="approved"') < markup.indexOf('data-status="completed"'));
  assert.match(markup, /&lt;img onerror=bad>/);
  assert.doesNotMatch(markup, /<img/);
  assert.match(markup, /Chưa đọc/);
  assert.match(markup, /Đã đọc/);
});

test("opening a notification ticket clears unrelated filters before navigation", () => {
  const handler = source.slice(source.indexOf('const ticket = event.target.closest'));
  assert.ok(handler.indexOf('select.value = "all"') < handler.indexOf('openResidentTab("tickets")'));
  assert.match(handler, /ticketFilterTimers.get\("resident"\)/);
  assert.match(handler, /residentTicketPage.offset = 0/);
});

test("a group can be marked read during background polling without using unseen updates", async () => {
  let resolvePoll;
  const calls = [];
  let reads = 0;
  const context = feedContext((path, options) => {
    calls.push({ path, options });
    if (options) return {};
    if (reads++ === 0) return new Promise(resolve => { resolvePoll = resolve; });
    return { items: [{ request_id: "REQ-A", events: [{ event_id: 14, read_at: null }] }], total: 1, latest_event_id: 14, unread_count: 1, has_more: false };
  });
  vm.runInContext('notificationState.latest=12; notificationState.items.set("REQ-A",{events:[{event_id:12}]});', context);
  const poll = context.loadNotificationFeed({ silent: true });
  await context.markNotificationsRead("REQ-A");
  resolvePoll({ items: [{ request_id: "REQ-STALE" }], total: 1, latest_event_id: 13, unread_count: 2 });
  await poll;
  assert.deepEqual(JSON.parse(calls[1].options.body), { through_event_id: 12 });
  assert.equal(vm.runInContext('notificationState.items.has("REQ-STALE")', context), false);
  assert.equal(vm.runInContext('notificationState.latest', context), 14);
});
