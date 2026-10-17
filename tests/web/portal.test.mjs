import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

const html = fs.readFileSync("web/index.html", "utf8");
const app = fs.readFileSync("web/app.js", "utf8");
const portal = fs.readFileSync("web/portal.js", "utf8");
const styles = fs.readFileSync("web/portal.css", "utf8");
const start = app.indexOf("function entryPayload(");
const end = app.indexOf("function setEntryError(", start);
const payload = vm.runInNewContext(`${app.slice(start, end)}\nentryPayload;`);

test("household removes duplicate entry shortcuts but keeps its overview and vehicle action", () => {
  const household = html.slice(html.indexOf('<section id="household"'), html.indexOf('<section id="resident-accounts"'));
  assert.doesNotMatch(household, /resident-entry-actions|Nhập thông tin cư dân|Hồ sơ cư trú|Thêm người cư trú|Đăng ký thẻ xe/);
  for (const section of ["household-summary", "household-occupants", "household-vehicles", "household-internet"]) {
    assert.ok(household.includes(`id="${section}"`));
  }
  assert.match(household, /data-ui-form="vehicle_registration">Thêm phương tiện/);
});

test("random credentials fill both fields only after server uniqueness checks and do not submit", async () => {
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, { value: "", textContent: "", disabled: false, addEventListener(event, handler) { this.handler = handler; } });
    return nodes.get(id);
  };
  const values = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333"];
  const checked = [];
  const state = {
    document: { getElementById: get }, accountDraft: { review: false, submitting: false },
    accountDirectory: { items: [{ username: "cd_111111111111" }] },
    crypto: { randomUUID: () => values.shift(), getRandomValues: bytes => bytes.fill(17) }, Uint8Array,
    authGeneration: 1, isSessionCurrent: () => true, accountSites: [{}], validateUsernameInput: async input => { checked.push(input.value); return input.value === "cd_333333333333"; },
  };
  const begin = portal.indexOf('document.getElementById("generate-account-password").addEventListener');
  const finish = portal.indexOf('document.getElementById("show-account-password").addEventListener', begin);
  vm.runInNewContext(portal.slice(begin, finish), state);
  await get("generate-account-password").handler({ currentTarget: get("generate-account-password") });
  assert.deepEqual(checked, ["cd_222222222222", "cd_333333333333"]);
  assert.equal(get("account-username").value, "cd_333333333333");
  assert.equal(get("account-password").value.length, 16);
  assert.match(get("account-generated-status").textContent, /xác nhận/);
  state.accountDraft.review = true;
  await get("generate-account-password").handler({ currentTarget: get("generate-account-password") });
  assert.equal(get("account-username").value, "cd_333333333333");
});

test("account password uses the same eye icon beside the input without submitting the form", () => {
  const field = html.slice(html.indexOf('<div class="account-password-field"'), html.indexOf('id="account-generated-status"'));
  assert.match(field, /<label for="account-password">Mật khẩu bàn giao<\/label>/);
  assert.match(field, /class="password-input-control"><input id="account-password"[\s\S]*<button id="show-account-password" type="button" class="password-visibility"/);
  assert.match(field, /aria-controls="account-password" aria-pressed="false" aria-label="Hiện mật khẩu bàn giao"/);
  assert.match(field, /class="ui-icon password-eye"[^>]*aria-hidden="true"/);
  assert.match(field, /class="password-eye-slash"/);
  assert.match(field, /<\/svg><\/button><\/div><\/div>/);
  assert.doesNotMatch(field, /<span>Hiện<\/span>/);
  assert.doesNotMatch(field.slice(field.indexOf('class="portal-inline-actions"')), /show-account-password/);
});

test("all password eyes use a borderless icon-only control outside the input", () => {
  const glass = fs.readFileSync("web/liquid-glass.css", "utf8");
  assert.match(glass, /\.password-input-control \{[^}]*display:grid;[^}]*grid-template-columns:minmax\(0,1fr\) 44px;[^}]*gap:8px;/);
  const sharedRule = glass.match(/body \.password-input-control > button\.password-visibility\[aria-controls\] \{([^}]+)\}/)?.[1] || "";
  assert.match(sharedRule, /display:inline-flex/);
  assert.match(sharedRule, /border:0;/);
  assert.match(sharedRule, /background:transparent;/);
  assert.match(sharedRule, /min-width:44px; min-height:44px;/);
  assert.doesNotMatch(sharedRule, /position:absolute|translateY/);
  for (const match of html.matchAll(/<button[^>]*class="password-visibility"[^>]*>(.*?)<\/button>/g)) {
    assert.equal(match[1].replace(/<[^>]*>/g, "").trim(), "");
    assert.match(match[0], /aria-label="Hiện [^"]+"/);
    assert.match(match[0], /aria-pressed="false"/);
    assert.match(match[1], /aria-hidden="true"/);
  }
  for (const match of html.matchAll(/<input\b[^>]*type="password"[^>]*>/g)) {
    const identity = match[0].match(/id="([^"]+)"/)?.[1];
    assert.ok(identity, "Every masked field has an explicit identity");
    assert.ok(html.includes(`aria-controls="${identity}"`), `Eye control missing for ${identity}`);
  }
  assert.match(html, /id="manager-password-secret" type="password"/);
});

test("first-login dialog clips all four rounded corners and scrolls inside the content", () => {
  const glass = fs.readFileSync("web/liquid-glass.css", "utf8");
  assert.match(html, /id="first-password-dialog"[^>]*>\s*<div class="first-password-content">/);
  assert.match(styles, /dialog\.first-password-dialog \{[^}]*overflow: hidden;/);
  assert.match(glass, /\.first-password-dialog \{[^}]*padding:0;[^}]*border-radius:24px;/);
  assert.match(glass, /\.first-password-content \{[^}]*max-height:calc\(100dvh - 34px\);[^}]*overflow-y:auto;/);
});

test("account password toggles and resets accessibly without replacing the eye icon", () => {
  const input = { type: "password", value: "private-value" };
  const attributes = {};
  const button = {
    setAttribute: (name, value) => { attributes[name] = value; },
    addEventListener: (event, handler) => { button.handler = handler; },
    set textContent(value) { assert.fail("Visibility updates must preserve the eye SVG"); },
  };
  const state = { document: { getElementById: id => id === "account-password" ? input : button } };
  const begin = portal.indexOf("function setAccountPasswordVisible(");
  const finish = portal.indexOf("function resetAccountForm(", begin);
  vm.runInNewContext(portal.slice(begin, finish), state);
  const handlerBegin = portal.indexOf('document.getElementById("show-account-password").addEventListener');
  const handlerEnd = portal.indexOf('document.getElementById("account-site").addEventListener', handlerBegin);
  vm.runInNewContext(portal.slice(handlerBegin, handlerEnd), state);
  for (const visible of [true, false, true]) {
    button.handler();
    assert.equal(input.type, visible ? "text" : "password");
    assert.equal(attributes["aria-pressed"], String(visible));
    assert.equal(attributes["aria-label"], `${visible ? "Ẩn" : "Hiện"} mật khẩu bàn giao`);
    assert.equal(input.value, "private-value");
  }
  vm.runInNewContext("setAccountPasswordVisible(false)", state);
  assert.equal(input.type, "password");
  assert.equal(attributes["aria-pressed"], "false");
  assert.equal(attributes["aria-label"], "Hiện mật khẩu bàn giao");
  assert.match(portal.slice(finish, portal.indexOf('document.getElementById("account-create-form").addEventListener', finish)), /setAccountPasswordVisible\(false\)/);
});

test("both username forms put the accessible inline warning directly above the input", () => {
  for (const prefix of ["account", "first-password"]) {
    assert.match(html, new RegExp(`id="${prefix}-username-error"[^>]*role="alert"[^>]*hidden></span><input id="${prefix}-username"`));
    assert.match(html, new RegExp(`aria-describedby="[^"]*${prefix}-username-error`));
  }
});

test("residency choices send identifiers rather than trusting identity fields", () => {
  const result = payload("verify_residency", { full_name: "Someone else", site_name: "Other site", target_location: JSON.stringify({ apartment: "A1205", building: "A" }), residency_purpose: "current", details: "  " });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { residency_purpose: "current", target_apartment: "A1205", target_building: "A", details: "" });
});

test("support and internet choices produce useful request details without mandatory long text", () => {
  assert.equal(payload("general_support", { support_category: "An ninh", details: "  Kiểm tra giúp tôi  " }).details, "An ninh: Kiểm tra giúp tôi");
  assert.equal(payload("internet_registration", { service_need: "Đăng ký lắp mới", details: "" }).details, "Đăng ký lắp mới:");
});

test("account creation keeps explicit consent and regulations have one confirmation after the content", () => {
  assert.match(html, /id="account-create-form"/);
  assert.match(html, /id="account-confirm" type="checkbox"\s*\//);
  assert.ok(html.indexOf('id="reading-confirm-selected"') > html.indexOf('id="regulations-list"'));
  assert.match(html, /Tôi xác nhận đã đọc nội quy/);
  assert.doesNotMatch(html, /id="ack-dialog"|id="ack-consent"/);
  assert.match(portal, /\/management\/resident-accounts/);
  assert.match(portal, /\/regulations\/acknowledge/);
  assert.ok(!/localStorage|sessionStorage/.test(portal));
});

test("personal information is actionable and support opens a form", () => {
  assert.match(html, /<button id="user-role"[^>]*aria-haspopup="dialog"/);
  assert.match(html, /data-ui-form="general_support"/);
  assert.match(app, /task-regulations/);
});

test("manager site choices do not require a fake resident in each site", () => {
  assert.match(portal, /accountSites = locations.sites/);
  assert.match(portal, /new Map\(accountSites.map/);
  assert.match(portal, /const site = accountSites.find/);
  assert.match(portal, /accountSites = \[\];\s+resetAccountForm/);
});

test("glass transitions have reduced-motion and reduced-transparency fallbacks", () => {
  assert.match(styles, /prefers-reduced-motion: reduce/);
  assert.match(styles, /prefers-reduced-transparency: reduce/);
  assert.ok(!/@import|https?:\/\//.test(styles));
  const optionalMotion = styles.match(/@media \(prefers-reduced-motion: no-preference\) \{[\s\S]*?\n\}/)?.[0];
  assert.ok(optionalMotion, "Decorative motion must respect the browser preference");
  assert.ok(!/infinite/.test(styles.replace(optionalMotion, "")), "Continuous animation must be opt-in through no-preference");
  assert.match(html, /\/static\/portal.js/);
});

function directoryHarness(items = []) {
  const elements = new Map();
  const node = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: "", children: [], attributes: {}, dataset: {},
      setAttribute(name, value) { this.attributes[name] = value; },
      getAttribute(name) { return this.attributes[name]; },
      replaceChildren() { this.children = []; },
      appendChild(child) { this.children.push(child); },
      focus() { this.focused = true; },
    });
    return elements.get(id);
  };
  const tabs = ["create", "manage"].map((view) => {
    const button = node(`account-${view}-tab`);
    button.dataset.accountTab = view;
    button.attributes["aria-controls"] = `account-${view}-panel`;
    return button;
  });
  const state = {
    accountDirectory: { items, loaded: true, page: 0, pageSize: 12 },
    document: { getElementById: node, createElement: () => node(`new-${elements.size}`), querySelectorAll: () => tabs },
    escapeHtml: value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
  };
  const start = portal.indexOf("function selectAccountWorkspace(");
  const end = portal.indexOf('\ndocument.querySelectorAll("[data-account-tab]")', start);
  vm.createContext(state);
  vm.runInContext(portal.slice(start, end), state);
  return { state, node, tabs };
}

const directoryAccount = (username, fullName, site = "site-a", building = "A") => ({
  username, confirmed_at: "2026-10-13T00:00:00Z",
  profile: { full_name: fullName, site_id: site, site_name: site === "site-a" ? "Vinhomes" : "Masteri", building, apartment: "1201" },
});

test("manager navigation leads with management and separates creation from the directory", () => {
  assert.ok(html.indexOf('id="tab-management"') < html.indexOf('id="tab-resident-accounts"'));
  assert.match(html, /id="tab-resident-accounts"[^>]*>Đăng ký cư dân<\/button>/);
  const create = html.slice(html.indexOf('id="account-create-panel"'), html.indexOf('id="account-manage-panel"'));
  assert.match(create, /id="account-create-form"/);
  assert.match(create, /id="account-receipt"/);
  assert.doesNotMatch(create, /id="account-list"|id="account-recovery-requests"/);
  assert.match(html.slice(html.indexOf('id="account-manage-panel"')), /id="account-list"/);
});

test("account subtabs expose just their panel with roving keyboard focus", () => {
  const { state, node, tabs } = directoryHarness();
  state.selectAccountWorkspace("manage", true);
  assert.equal(node("account-create-panel").hidden, true);
  assert.equal(node("account-manage-panel").hidden, false);
  assert.equal(tabs[0].tabIndex, -1);
  assert.equal(tabs[1].tabIndex, 0);
  assert.equal(tabs[1].attributes["aria-selected"], "true");
  assert.equal(tabs[1].focused, true);
  state.selectAccountWorkspace("invalid");
  assert.equal(node("account-manage-panel").hidden, true);
  assert.equal(node("account-create-panel").hidden, false);
});

test("account search supports Vietnamese without accents and combines name, site and building filters", () => {
  const { state } = directoryHarness();
  const items = [directoryAccount("demo001", "Nguyễn Đăng An"), directoryAccount("demo002", "Nguyễn Đăng An", "site-b", "B"), directoryAccount("demo003", "Trần Bình")];
  assert.equal(state.filterResidentAccounts(items, "NGUYEN dang", "", "").length, 2);
  assert.equal(state.filterResidentAccounts(items, "an", "site-a", "A")[0].username, "demo001");
  assert.equal(state.filterResidentAccounts(items, "demo002 1201", "site-b", "B").length, 1);
  assert.equal(state.filterResidentAccounts(items, "nguyen", "site-a", "B").length, 0);
  assert.equal(state.filterResidentAccounts(items, "<script>", "", "").length, 0);
  assert.equal(state.filterResidentAccounts(items, "", "", "").length, 3);
});

test("building filter follows the selected site and removes an invalid previous selection", () => {
  const items = [directoryAccount("first", "An"), directoryAccount("second", "Bình", "site-b", "B")];
  const { state, node } = directoryHarness(items);
  node("account-filter-site").value = "site-b";
  node("account-filter-building").value = "A";
  state.updateAccountBuildingFilter();
  assert.deepEqual(node("account-filter-building").children.map(item => item.value), ["", "B"]);
  assert.equal(node("account-filter-building").value, "");
  node("account-filter-building").value = "B";
  state.updateAccountBuildingFilter();
  assert.equal(node("account-filter-building").value, "B");
});

test("account directory paginates, clamps after filtering, and explains empty results", () => {
  const items = Array.from({ length: 14 }, (_, index) => directoryAccount(`demo${index}`, index === 13 ? "<img src=x>" : `Cư dân ${index}`));
  const { state, node } = directoryHarness(items);
  state.renderAccountDirectory();
  assert.equal((node("account-list").innerHTML.match(/role="listitem"/g) || []).length, 12);
  assert.equal(node("account-list-prev").disabled, true);
  assert.equal(node("account-list-next").disabled, false);
  state.accountDirectory.page = 1;
  state.renderAccountDirectory();
  assert.equal(node("account-list-page").textContent, "Trang 2 / 2");
  assert.match(node("account-list").innerHTML, /&lt;img src=x&gt;/);
  assert.doesNotMatch(node("account-list").innerHTML, /<img/);
  assert.equal(node("account-list-next").disabled, true);
  node("account-search").value = "demo13";
  state.renderAccountDirectory();
  assert.equal(state.accountDirectory.page, 0);
  assert.match(node("account-list-count").textContent, /1 \/ 14/);
  node("account-search").value = "no match";
  state.renderAccountDirectory();
  assert.equal(node("account-list-empty").hidden, false);
  assert.match(node("account-list-empty").textContent, /xóa bộ lọc/);
  state.accountDirectory.items = [];
  state.renderAccountDirectory();
  assert.match(node("account-list-empty").textContent, /Tạo tài khoản cư dân/);
});

test("directory hooks retain auth generation guards and recovery opens the management subtab", () => {
  assert.match(portal, /load !== accountWorkspaceLoad/);
  assert.match(portal, /function clearPortalSession\(\) \{\s+accountWorkspaceLoad \+= 1;/);
  assert.match(fs.readFileSync("web/account-recovery.js", "utf8"), /getElementById\("account-recovery-requests"\)\.appendChild\(section\)/);
  assert.match(fs.readFileSync("web/manager-assistant.js", "utf8"), /selectAccountWorkspace\("manage", true\)/);
});

test("manager directory keeps suspended accounts visible and distinguishes reset from restoring access", () => {
  const { state, node } = directoryHarness([
    { ...directoryAccount("suspended", "Nguyễn An"), access_status: "suspended" },
    { ...directoryAccount("active", "Trần Bình"), access_status: "active" },
  ]);
  state.renderAccountDirectory();
  const cards = node("account-list").innerHTML.split("</article>");
  assert.match(cards[0], /class="error">Tạm ngừng sử dụng/);
  assert.match(cards[0], /Đặt lại mật khẩu không mở lại quyền truy cập/);
  assert.match(cards[0], /data-reset-account="suspended"/);
  assert.doesNotMatch(cards[1], /Tạm ngừng sử dụng/);
  assert.equal(node("account-list-empty").hidden, true);
  assert.match(styles, /#account-list \.account-list-item \.error \{ color: var\(--status-error-ink\)/);
});

test("only resident procedure and checklist catalogs receive full-height edges and blue completed surfaces", () => {
  assert.match(styles, /#procedure-picker \.a2ui-procedure::before, #progress \.task\[data-status\]::before \{ top: 0; bottom: 0; border-radius: 0; \}/);
  assert.match(styles, /#progress \.task\[data-status="completed"\] \{ background: var\(--status-completed-soft\)/);
  assert.match(styles, /#procedure-picker \.a2ui-procedure:is\(\[data-status="done"\], \[data-status="completed"\]\)/);
  assert.match(styles, /#progress \.task\[data-status="completed"\] \.task-marker.*--status-completed-solid/);
});

test("other-request entry retains the mascot and chat actions use an even grid", () => {
  assert.match(html, /<title>MoveinmateAI-P047<\/title>/);
  assert.match(html, /class="brand-name">MoveInMate<\/span><span class="brand-tag">AI<\/span>/);
  assert.match(html, /aria-label="MoveInMate - về trang chính"/);
  assert.doesNotMatch(html.slice(html.indexOf("<body>")), /MoveinmateAI-P047/);
  assert.match(html, /id="open-chat-main"/);
  assert.match(html, /data-agent-mascot="start"/);
  assert.match(app, /getElementById\("open-chat-main"\)\.addEventListener\("click", \(\) => focusChatDraft\(""\)\)/);
  assert.match(styles, /\.a2ui-actions \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(app, /namedItem\("details"\)\.value = questionDraft\.slice\(0, 700\)/);
});

const apartmentHelpers = vm.runInNewContext(`${portal.slice(portal.indexOf("function normalizeApartmentCode("), portal.indexOf("function attachApartmentPicker("))}\n({normalizeApartmentCode, matchingApartments, locationHasOwner});`);
const apartmentRecord = (apartment, building = "C", changes = {}) => ({ site_id: "site-a", site_name: "Khu A", building, apartment, owner_registered: false, owner_conflict: false, ...changes });

test("apartment suggestions filter and deduplicate real addresses with normalized codes", () => {
  assert.equal(apartmentHelpers.normalizeApartmentCode(" c 1208 "), "C1208");
  const items = [apartmentRecord("C1208"), apartmentRecord("c 1208", " c ", { owner_registered: true, owner_conflict: true }), apartmentRecord("C1209"), apartmentRecord("D0901", "D")];
  const matches = apartmentHelpers.matchingApartments(items, "c12");
  assert.equal(matches.length, 2);
  assert.equal(matches[0].apartment, "C1208");
  assert.equal(matches[0].owner_registered, true);
  assert.equal(matches[0].owner_conflict, true);
  assert.equal(apartmentHelpers.matchingApartments(items, "unknown").length, 0);
});

test("owner conflict distinguishes sites and explicit towers but never bypasses a missing tower", () => {
  const items = [apartmentRecord("C1208", "C", { owner_registered: true, owner_conflict: true })];
  assert.equal(apartmentHelpers.locationHasOwner(items, "site-a", " c ", "c 1208"), true);
  assert.equal(apartmentHelpers.locationHasOwner(items, "site-a", "", "C1208"), true);
  assert.equal(apartmentHelpers.locationHasOwner(items, "site-a", "D", "C1208"), false);
  assert.equal(apartmentHelpers.locationHasOwner(items, "site-b", "C", "C1208"), false);
  assert.equal(apartmentHelpers.locationHasOwner([apartmentRecord("C1208", "", { owner_conflict: true })], "site-a", "C", "C1208"), true);
  assert.equal(apartmentHelpers.locationHasOwner([apartmentRecord("C1208", "C", { owner_registered: true, owner_conflict: false })], "site-a", "C", "C1208"), false);
});

test("apartment entry supports a keyboard-accessible suggestion table and inline owner errors", () => {
  assert.match(html, /id="account-apartment-suggestions"/);
  assert.match(html, /id="account-owner-error"[^>]*role="alert"/);
  assert.match(portal, /setAttribute\("role", "combobox"\)/);
  assert.match(portal, /setAttribute\("role", "listbox"\)/);
  assert.match(portal, /setAttribute\("aria-activedescendant"/);
  assert.match(portal, /setCustomValidity\(message\)/);
  assert.match(portal, /account-resident-type"\)\.addEventListener\("change", validateAccountOwner\)/);
  assert.match(app, /entry-apartment-query/);
  assert.match(app, /attachApartmentPicker\(search, panel/);
  assert.match(app, /entry-owner-error/);
});

test("apartment keyboard navigation starts at the correct edge and wraps before selection", () => {
  const makeElement = () => ({
    id: "picker", value: "", hidden: true, dataset: {}, children: [], attributes: new Map(), handlers: {},
    setAttribute(name, value) { this.attributes.set(name, value); },
    getAttribute(name) { return this.attributes.get(name); },
    removeAttribute(name) { this.attributes.delete(name); },
    addEventListener(name, handler) { this.handlers[name] = handler; },
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; },
    querySelectorAll() { return this.children[1].children; },
    scrollIntoView() {}, focus() {},
  });
  const attach = vm.runInNewContext(`${portal.slice(portal.indexOf("function normalizeApartmentCode("), portal.indexOf("function updateAccountBuildingFilter("))}\nattachApartmentPicker;`, { document: { createElement: makeElement } });
  const input = makeElement();
  const panel = makeElement();
  panel.id = "suggestions";
  let selected;
  attach(input, panel, { items: () => [apartmentRecord("C1208"), apartmentRecord("C1209"), apartmentRecord("C1210")], onSelect: item => { selected = item; } });
  const press = key => input.handlers.keydown({ key, preventDefault() {}, stopPropagation() {} });
  press("ArrowUp");
  assert.equal(input.getAttribute("aria-activedescendant"), "suggestions-list-2");
  press("ArrowDown");
  assert.equal(input.getAttribute("aria-activedescendant"), "suggestions-list-0");
  press("Enter");
  assert.equal(selected.apartment, "C1208");
  assert.equal(input.value, "C1208");
  assert.equal(panel.hidden, true);
  input.value = "";
  press("ArrowDown");
  assert.equal(input.getAttribute("aria-activedescendant"), "suggestions-list-0");
  press("Escape");
  assert.equal(input.getAttribute("aria-expanded"), "false");
});

test("other-request card keeps readable blue copy and a smaller mascot", () => {
  assert.match(html, /class="start-chat-arrow"/);
  assert.match(styles, /\.start-agent-avatar \{ width: 48px; height: 52px;/);
  const glass = fs.readFileSync("web/liquid-glass.css", "utf8");
  assert.match(glass, /\.start-card \.start-chat-copy strong \{ color:#2e66a3/);
  assert.match(glass, /\.start-card \.start-steps small \{ display:block/);
});

test("ownership gate blocks every unconfirmed resident and warning closes after 1.5 seconds", () => {
  const toast = { hidden: true };
  let timer;
  const state = { authReady: true, hasRole: () => true, document: { getElementById: () => toast }, clearTimeout() {}, setTimeout(callback, duration) { timer = { callback, duration }; return 1; } };
  vm.createContext(state);
  vm.runInContext(app.slice(app.indexOf("let residentOwnershipState"), app.indexOf('document.addEventListener("click", event =>')), state);
  vm.runInContext('residentOwnershipState = { ready: true, profile: { resident_type: "owner", ownership_confirmed_at: null } };', state);
  assert.equal(state.ownershipRequired(), true);
  state.showOwnershipWarning();
  assert.equal(toast.hidden, false);
  assert.equal(timer.duration, 1500);
  assert.match(styles, /\.ownership-toast \{ position: fixed; top: 50%; left: 50%; transform: translate\(-50%, -50%\)/);
  timer.callback();
  assert.equal(toast.hidden, true);
  for (const profile of [{ resident_type: "owner", ownership_confirmed_at: "2026-10-13" }, { resident_type: "tenant", ownership_confirmed_at: "2026-10-13" }, { resident_type: "family_member", ownership_confirmed_at: "2026-10-13" }]) {
    vm.runInContext(`residentOwnershipState.profile = ${JSON.stringify(profile)};`, state);
    assert.equal(state.ownershipRequired(), false);
  }
  assert.match(html, /id="ownership-onboarding"/);
  assert.match(app, /Xác nhận và khóa thông tin/);
  assert.match(styles, /suggestion-chips button:not\(\[aria-pressed="true"\]\) \{ background: #fff/);
  assert.match(html, />TK\/MK ngẫu nhiên<\/button>/);
  const personal = html.slice(html.indexOf('<dialog id="personal-dialog"'), html.indexOf('<dialog id="password-change-dialog"'));
  assert.doesNotMatch(personal, /id="password-change-form"/);
});

test("returning from ownership review keeps document extraction hidden", () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { hidden: false, required: true, textContent: "" });
    return elements.get(id);
  };
  let back;
  element("resident-entry-back").addEventListener = (_, handler) => { back = handler; };
  const context = {
    residentEntryState: { type: "ownership", review: true, submitting: false, payload: {} },
    document: { getElementById: element }, crypto: { randomUUID: () => "new-key" }, setEntryError() {},
  };
  const start = app.indexOf('document.getElementById("resident-entry-back").addEventListener');
  const end = app.indexOf('residentEntryForm.addEventListener("submit"', start);
  vm.runInNewContext(app.slice(start, end), context);
  back();
  assert.equal(context.residentEntryState.review, false);
  assert.equal(element("resident-entry-fields").hidden, false);
  assert.equal(element("form-autofill").hidden, true);
  assert.equal(element("resident-entry-review").hidden, true);
  assert.equal(element("resident-entry-consent").required, false);
  context.residentEntryState.type = "verify_residency";
  back();
  assert.equal(element("form-autofill").hidden, false);
  context.residentEntryState.type = "vehicle_registration";
  back();
  assert.equal(element("form-autofill").hidden, true);
});

test("ownership button remains available until every resident confirms", () => {
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); };
  const state = { authReady: true, hasRole: () => true, synchronizeProcedureCards() {}, document: { getElementById: get } };
  vm.createContext(state);
  vm.runInContext(app.slice(app.indexOf("let residentOwnershipState"), app.indexOf('document.addEventListener("click", event =>')), state);
  for (const profile of [null, { resident_type: "owner" }, { resident_type: "owner", ownership_confirmed_at: "2026-10-14" }, { resident_type: "tenant" }]) {
    vm.runInContext(`residentOwnershipState = { ready: true, profile: ${JSON.stringify(profile)} };`, state);
    state.synchronizeOwnershipUi();
    assert.equal(get("personal-residency").hidden, !profile || Boolean(profile.ownership_confirmed_at));
  }
  const start = app.indexOf("const residentEntryCatalog =");
  const end = app.indexOf("let residentEntryState", start);
  const catalog = vm.runInNewContext(`${app.slice(start, end)}\nresidentEntryCatalog;`);
  assert.ok(catalog.ownership.fields.every(field => field.readonly));
  for (const field of catalog.profile.fields.filter(field => ["full_name", "resident_type", "site_name", "building", "apartment"].includes(field.key))) assert.equal(field.readonly, true);
  assert.ok(!catalog.ownership.fields.some(field => field.key === "target_location"));
});
