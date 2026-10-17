let accountLocations = [];
let accountSites = [];
let accountDraft = { key: crypto.randomUUID(), review: false, submitting: false };
let accountHandover = "";
let readingDocuments = [];
let readingSubmitting = false;
let personalReturnFocus = null;
let accountWorkspaceLoad = 0;
let accountDirectory = { items: [], loaded: false, page: 0, pageSize: 12 };
let accountDirectoryPolling = false;

function selectAccountWorkspace(view, focus = false) {
  const selected = view === "manage" ? "manage" : "create";
  document.querySelectorAll("[data-account-tab]").forEach((button) => {
    const active = button.dataset.accountTab === selected;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    document.getElementById(button.getAttribute("aria-controls")).hidden = !active;
    if (active && focus) button.focus();
  });
}

function accountSearchText(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/gi, "d").toLowerCase();
}

function filterResidentAccounts(items, search, site, building) {
  const words = accountSearchText(search).trim().split(/\s+/).filter(Boolean);
  return items.filter((item) => {
    if (site && item.profile.site_id !== site) return false;
    if (building && item.profile.building !== building) return false;
    const text = accountSearchText([item.username, item.profile.full_name, item.profile.site_name, item.profile.building, item.profile.apartment].join(" "));
    return words.every((word) => text.includes(word));
  });
}

function normalizeApartmentCode(value) {
  return String(value || "").replace(/\s+/g, "").toUpperCase();
}

function matchingApartments(items, query) {
  const search = normalizeApartmentCode(query);
  const unique = new Map();
  for (const item of items) {
    if (search && !normalizeApartmentCode(`${item.apartment} ${item.building}`).includes(search)) continue;
    const key = JSON.stringify([item.site_id, normalizeApartmentCode(item.building), normalizeApartmentCode(item.apartment)]);
    const previous = unique.get(key);
    unique.set(key, previous ? { ...previous, owner_registered: previous.owner_registered || item.owner_registered, owner_conflict: previous.owner_conflict || item.owner_conflict } : item);
  }
  return [...unique.values()].sort((first, second) => first.apartment.localeCompare(second.apartment, "vi", { numeric: true }) || first.building.localeCompare(second.building, "vi", { numeric: true }));
}

function locationHasOwner(items, site, building, apartment) {
  const code = normalizeApartmentCode(apartment);
  const tower = normalizeApartmentCode(building);
  return Boolean(code) && items.some(item => item.site_id === site && normalizeApartmentCode(item.apartment) === code
    && (!tower || !normalizeApartmentCode(item.building) || normalizeApartmentCode(item.building) === tower)
    && (item.owner_conflict ?? item.owner_registered));
}

function attachApartmentPicker(input, panel, { items, onSelect, onInput = () => {}, allowNew = false }) {
  let active = -1;
  let visible = [];
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-haspopup", "listbox");
  input.setAttribute("aria-controls", panel.id + "-list");
  input.setAttribute("aria-expanded", "false");
  input.autocomplete = "off";
  const close = () => {
    panel.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };
  const choose = item => {
    input.value = item.apartment;
    onSelect(item);
    close();
    input.focus();
  };
  const refresh = (open = !panel.hidden) => {
    if (!open) { close(); return; }
    const matches = matchingApartments(items(), input.value);
    visible = matches.slice(0, 8);
    active = -1;
    input.removeAttribute("aria-activedescendant");
    panel.replaceChildren();
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.textContent = matches.length ? `Gợi ý căn hộ · ${visible.length} / ${matches.length}. Chọn một dòng hoặc dùng phím ↑ ↓ và Enter.` : allowNew ? "Chưa có căn hộ phù hợp. Có thể nhập căn hộ mới sau khi BQL đối chiếu." : "Không có căn hộ phù hợp. Chọn một căn hộ trong danh sách của khu.";
    panel.append(status);
    const list = document.createElement("div");
    list.id = panel.id + "-list";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "Gợi ý căn hộ");
    visible.forEach((item, index) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "apartment-option";
      option.id = `${list.id}-${index}`;
      option.tabIndex = -1;
      option.dataset.owner = String(Boolean(item.owner_registered));
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", "false");
      const name = document.createElement("strong");
      name.textContent = `Căn hộ ${item.apartment}`;
      const address = document.createElement("small");
      address.textContent = [item.building || "Chưa ghi tòa", item.site_name].filter(Boolean).join(" · ");
      const ownership = document.createElement("span");
      ownership.textContent = item.owner_registered ? "Đã có chủ sở hữu" : "Chưa ghi nhận chủ sở hữu";
      option.append(name, address, ownership);
      option.addEventListener("pointerdown", event => event.preventDefault());
      option.addEventListener("click", () => choose(item));
      list.append(option);
    });
    panel.append(list);
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
  };
  input.addEventListener("focus", () => refresh(true));
  input.addEventListener("click", () => refresh(true));
  input.addEventListener("input", () => { onInput(input.value); refresh(true); });
  input.addEventListener("blur", close);
  input.addEventListener("keydown", event => {
    if (event.key === "Escape" && !panel.hidden) { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key === "Enter" && !panel.hidden && active >= 0) { event.preventDefault(); choose(visible[active]); return; }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    if (panel.hidden) refresh(true);
    if (!visible.length) return;
    active = active < 0 ? (event.key === "ArrowDown" ? 0 : visible.length - 1) : (active + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length;
    const options = panel.querySelectorAll('[role="option"]');
    options.forEach((option, index) => option.setAttribute("aria-selected", String(index === active)));
    input.setAttribute("aria-activedescendant", options[active].id);
    options[active].scrollIntoView({ block: "nearest" });
  });
  return { refresh, close };
}

function updateAccountBuildingFilter() {
  const site = document.getElementById("account-filter-site").value;
  const select = document.getElementById("account-filter-building");
  const previous = select.value;
  const buildings = [...new Set(accountDirectory.items.filter((item) => !site || item.profile.site_id === site).map((item) => item.profile.building).filter(Boolean))].sort((first, second) => first.localeCompare(second, "vi", { numeric: true }));
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "Tất cả tòa / khu vực";
  select.appendChild(all);
  buildings.forEach((building) => {
    const option = document.createElement("option");
    option.value = building;
    option.textContent = building;
    select.appendChild(option);
  });
  select.value = buildings.includes(previous) ? previous : "";
}

function renderAccountDirectory() {
  const filtered = filterResidentAccounts(accountDirectory.items, document.getElementById("account-search").value, document.getElementById("account-filter-site").value, document.getElementById("account-filter-building").value);
  const pages = Math.max(1, Math.ceil(filtered.length / accountDirectory.pageSize));
  accountDirectory.page = Math.min(accountDirectory.page, pages - 1);
  const start = accountDirectory.page * accountDirectory.pageSize;
  const visible = filtered.slice(start, start + accountDirectory.pageSize);
  const accessLabels = { suspended: "Tạm ngừng sử dụng", disabled: "Đã vô hiệu hóa", expired: "Quyền truy cập đã hết hạn", inactive: "Chưa có quyền truy cập" };
  document.getElementById("account-list").innerHTML = visible.map((item) => `<article class="account-list-item" role="listitem"><strong>${escapeHtml(item.profile.full_name)}</strong><span>${escapeHtml(item.username)}</span><small>${escapeHtml([item.profile.site_name, item.profile.building, item.profile.apartment].filter(Boolean).join(" · "))}</small>${accessLabels[item.access_status] ? `<small class="error">${escapeHtml(accessLabels[item.access_status])} · Đặt lại mật khẩu không mở lại quyền truy cập.</small>` : ""}<small>Cấp lúc ${escapeHtml(new Date(item.confirmed_at).toLocaleString("vi-VN"))}</small>${item.temporary_password_available ? `<button type="button" data-handover-account="${escapeHtml(item.username)}">Xem tài khoản, mật khẩu</button>` : `<small>${item.password_change_required ? "Không còn mật khẩu bàn giao" : "Cư dân đã đổi mật khẩu"}</small>`}<button type="button" data-reset-account="${escapeHtml(item.username)}" aria-label="Đặt lại mật khẩu cho ${escapeHtml(item.profile.full_name)} (${escapeHtml(item.username)})">Đặt lại mật khẩu</button></article>`).join("");
  document.getElementById("account-list-count").textContent = accountDirectory.loaded ? `${filtered.length} / ${accountDirectory.items.length} tài khoản${visible.length ? ` · Hiển thị ${start + 1}–${start + visible.length}` : ""}` : "Đang tải tài khoản…";
  const empty = document.getElementById("account-list-empty");
  empty.hidden = !accountDirectory.loaded || filtered.length > 0;
  empty.textContent = accountDirectory.items.length ? "Không tìm thấy tài khoản phù hợp. Thử từ khóa khác hoặc xóa bộ lọc." : "Chưa có tài khoản cư dân được cấp. Chọn Tạo tài khoản cư dân để bắt đầu.";
  document.getElementById("account-list-page").textContent = `Trang ${accountDirectory.page + 1} / ${pages}`;
  document.getElementById("account-list-prev").disabled = !accountDirectory.loaded || accountDirectory.page === 0;
  document.getElementById("account-list-next").disabled = !accountDirectory.loaded || accountDirectory.page + 1 >= pages;
}

document.querySelectorAll("[data-account-tab]").forEach((button) => {
  button.addEventListener("click", () => selectAccountWorkspace(button.dataset.accountTab));
});
document.querySelector(".account-workspace-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...document.querySelectorAll("[data-account-tab]")];
  const current = tabs.indexOf(document.activeElement);
  if (current < 0) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowLeft" ? -1 : 1) + tabs.length) % tabs.length;
  selectAccountWorkspace(tabs[next].dataset.accountTab, true);
});
for (const [id, event] of [["account-search", "input"], ["account-filter-site", "change"], ["account-filter-building", "change"]]) {
  document.getElementById(id).addEventListener(event, () => {
    accountDirectory.page = 0;
    if (id === "account-filter-site") updateAccountBuildingFilter();
    renderAccountDirectory();
  });
}
document.getElementById("account-clear-filters").addEventListener("click", () => {
  document.getElementById("account-search").value = "";
  document.getElementById("account-filter-site").value = "";
  document.getElementById("account-filter-building").value = "";
  accountDirectory.page = 0;
  updateAccountBuildingFilter();
  renderAccountDirectory();
});
for (const [id, direction] of [["account-list-prev", -1], ["account-list-next", 1]]) {
  document.getElementById(id).addEventListener("click", () => {
    accountDirectory.page = Math.max(0, accountDirectory.page + direction);
    renderAccountDirectory();
  });
}

function portalSummary(target, entries) {
  target.replaceChildren();
  entries.forEach(([label, content]) => {
    const title = document.createElement("dt");
    const value = document.createElement("dd");
    title.textContent = label;
    value.textContent = content || "Chưa cập nhật";
    target.append(title, value);
  });
}

function portalError(id, message = "") {
  const target = document.getElementById(id);
  const labels = {
    username_unavailable: "Tên đăng nhập này đã được sử dụng. Hãy chọn tên khác.",
    account_creation_conflict: "Thông tin đã thay đổi so với lần gửi trước. Hãy quay lại để kiểm tra.",
    document_revision_changed: "Tài liệu đã có phiên bản mới. Đóng hộp thoại và tải lại tài liệu trước khi xác nhận.",
    site_scope_required: "Khu dân cư không thuộc quyền quản lý của tài khoản này.",
    apartment_not_available: "Căn hộ không còn trong danh sách hiện tại. Hãy mở lại form.",
    apartment_owner_exists: "Căn hộ này đã có chủ sở hữu. Không thể thêm chủ sở hữu thứ hai. Bấm Sửa thông tin để chọn đúng tư cách cư trú hoặc căn hộ; liên hệ BQL nếu cần chuyển chủ.",
  };
  target.textContent = labels[message] || message;
  target.hidden = !message;
  if (message) target.focus();
}

function updateLocationSuggestions() {
  const site = document.getElementById("account-site").value;
  const building = document.getElementById("account-building").value.trim();
  const scoped = accountLocations.filter((item) => item.site_id === site);
  const fill = (id, values) => {
    const target = document.getElementById(id);
    target.replaceChildren();
    [...new Set(values)].filter(Boolean).forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      target.appendChild(option);
    });
  };
  fill("account-building-options", scoped.map((item) => item.building));
  const apartments = scoped.filter((item) => !building || normalizeApartmentCode(item.building) === normalizeApartmentCode(building));
  accountApartmentPicker.refresh();
  document.getElementById("account-location-help").textContent = `${apartments.length} căn hộ trong phạm vi đang chọn · Tòa A–D · Tầng 01–20 · Phòng 01–10. Nhập mã để tìm hoặc chọn trong bảng gợi ý.`;
  validateAccountOwner();
}

function validateAccountOwner() {
  const input = document.getElementById("account-apartment");
  const site = document.getElementById("account-site").value;
  const building = document.getElementById("account-building").value;
  const scoped = accountLocations.filter(item => item.site_id === site);
  const available = scoped.some(item => normalizeApartmentCode(item.apartment) === normalizeApartmentCode(input.value) && (!building || normalizeApartmentCode(item.building) === normalizeApartmentCode(building)));
  const invalid = Boolean(input.value.trim()) && !available;
  const conflict = document.getElementById("account-resident-type").value === "owner" && locationHasOwner(accountLocations, site, building, input.value);
  const message = conflict ? `Căn hộ ${input.value.trim()} đã có chủ sở hữu. Không thể đăng ký thêm chủ sở hữu; chọn đúng căn hộ hoặc tư cách Người thuê / Thành viên gia đình.` : invalid ? "Chọn căn hộ có sẵn trong danh sách của đúng khu và tòa (A–D, tầng 01–20, phòng 01–10)." : "";
  input.setCustomValidity(message);
  input.setAttribute("aria-invalid", String(Boolean(message)));
  const error = document.getElementById("account-owner-error");
  error.textContent = message;
  error.hidden = !message;
  document.getElementById("account-submit").disabled = Boolean(message) || !accountSites.length || accountDraft.submitting;
  return !message;
}

const accountApartmentPicker = attachApartmentPicker(document.getElementById("account-apartment"), document.getElementById("account-apartment-suggestions"), {
  items: () => accountLocations.filter(item => item.site_id === document.getElementById("account-site").value && (!document.getElementById("account-building").value.trim() || normalizeApartmentCode(item.building) === normalizeApartmentCode(document.getElementById("account-building").value))),
  onSelect: item => { document.getElementById("account-building").value = item.building; updateLocationSuggestions(); },
  onInput: validateAccountOwner,
  allowNew: false,
});

async function loadAccountWorkspace() {
  if (!authReady || !hasRole("manager")) return;
  const generation = authGeneration;
  const load = ++accountWorkspaceLoad;
  const refresh = document.getElementById("refresh-accounts");
  refresh.disabled = true;
  document.getElementById("account-list").setAttribute("aria-busy", "true");
  if (!accountDirectory.loaded) renderAccountDirectory();
  portalError("account-load-error");
  try {
    const [locations, accounts] = await Promise.all([api("/locations"), api("/management/resident-accounts")]);
    if (!isSessionCurrent(generation) || load !== accountWorkspaceLoad) return;
    accountLocations = locations.items;
    accountSites = locations.sites;
    const select = document.getElementById("account-site");
    const previous = select.value;
    select.replaceChildren();
    const sites = new Map(accountSites.map((item) => [item.site_id, item.site_name]));
    sites.forEach((name, id) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = name;
      select.appendChild(option);
    });
    if (sites.has(previous)) select.value = previous;
    updateLocationSuggestions();
    validateAccountOwner();
    accountDirectory.items = accounts.items;
    accountDirectory.loaded = true;
    const filter = document.getElementById("account-filter-site");
    const previousSite = filter.value;
    filter.replaceChildren();
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "Tất cả khu";
    filter.appendChild(all);
    sites.forEach((name, id) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = name;
      filter.appendChild(option);
    });
    filter.value = sites.has(previousSite) ? previousSite : "";
    updateAccountBuildingFilter();
    renderAccountDirectory();
    if (typeof loadPasswordRecoveryRequests === "function") void loadPasswordRecoveryRequests();
    if (!sites.size) portalError("account-error", "Chưa có thông tin căn hộ thuộc khu được quản lý. Cần bổ sung hồ sơ khu trước khi cấp tài khoản.");
  } catch (error) {
    if (isSessionCurrent(generation) && load === accountWorkspaceLoad) {
      portalError("account-load-error", error.message);
      document.getElementById("account-list-count").textContent = "Không tải được tài khoản. Bấm Làm mới để thử lại.";
    }
  } finally {
    if (load === accountWorkspaceLoad) {
      refresh.disabled = false;
      document.getElementById("account-list").setAttribute("aria-busy", "false");
    }
  }
}

function setAccountPasswordVisible(visible) {
  const input = document.getElementById("account-password");
  const button = document.getElementById("show-account-password");
  input.type = visible ? "text" : "password";
  button.setAttribute("aria-pressed", String(visible));
  button.setAttribute("aria-label", `${visible ? "Ẩn" : "Hiện"} mật khẩu bàn giao`);
}

function resetAccountForm() {
  accountDraft = { key: crypto.randomUUID(), review: false, submitting: false };
  accountHandover = "";
  const form = document.getElementById("account-create-form");
  form.reset();
  resetUsernameCheck(document.getElementById("account-username"), "account-username-error");
  form.hidden = false;
  document.getElementById("account-fields").hidden = false;
  document.getElementById("account-fields").disabled = false;
  document.getElementById("account-review").hidden = true;
  document.getElementById("account-confirm").required = false;
  document.getElementById("account-back").hidden = true;
  document.getElementById("account-back").disabled = false;
  document.getElementById("account-submit").disabled = !accountSites.length;
  document.getElementById("account-submit").textContent = "Xem lại thông tin";
  document.getElementById("account-receipt").hidden = true;
  document.getElementById("account-receipt-details").replaceChildren();
  document.getElementById("account-copy-status").textContent = "";
  document.getElementById("account-generated-status").textContent = "";
  setAccountPasswordVisible(false);
  portalError("account-error");
  accountApartmentPicker.close();
  validateAccountOwner();
}

document.getElementById("account-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setAccountPasswordVisible(false);
  const form = event.currentTarget;
  if (!authReady || !hasRole("manager") || accountDraft.submitting || accountDraft.validating || !validateAccountOwner()) return;
  showUsernameError(document.getElementById("account-username"), "account-username-error");
  if (!form.reportValidity()) return;
  portalError("account-error");
  if (!accountDraft.review) {
    const draft = accountDraft;
    draft.validating = true;
    document.getElementById("account-submit").disabled = true;
    try {
      if (!await validateUsernameInput(document.getElementById("account-username"), "account-username-error") || draft !== accountDraft) return;
    } finally {
      draft.validating = false;
      if (draft === accountDraft) document.getElementById("account-submit").disabled = false;
    }
    accountDraft.payload = Object.fromEntries(new FormData(form));
    const values = accountDraft.payload;
    values.username = values.username.trim().toLowerCase();
    values.full_name = values.full_name.trim();
    values.building = values.building.trim();
    values.apartment = values.apartment.trim();
    if (values.full_name.length < 2 || !values.apartment) { portalError("account-error", "Vui lòng nhập họ tên và mã căn hộ hợp lệ."); return; }
    const site = accountSites.find((item) => item.site_id === values.site_id);
    portalSummary(document.getElementById("account-review-summary"), [["Họ và tên", values.full_name], ["Khu dân cư", site?.site_name], ["Tòa / khu vực", values.building], ["Căn hộ", values.apartment], ["Tư cách cư trú", document.getElementById("account-resident-type").selectedOptions[0].textContent], ["Tên đăng nhập", values.username], ["Quyền được cấp", "Cư dân — chỉ truy cập hồ sơ được gắn với tài khoản"]]);
    document.getElementById("account-fields").hidden = true;
    document.getElementById("account-fields").disabled = true;
    document.getElementById("account-review").hidden = false;
    document.getElementById("account-confirm").checked = false;
    document.getElementById("account-confirm").required = true;
    document.getElementById("account-back").hidden = false;
    document.getElementById("account-submit").textContent = "Xác nhận và tạo tài khoản";
    accountDraft.review = true;
    document.getElementById("account-confirm").focus();
    return;
  }
  if (!document.getElementById("account-confirm").checked) return;
  const state = accountDraft;
  const generation = authGeneration;
  state.submitting = true;
  document.getElementById("account-submit").disabled = true;
  document.getElementById("account-back").disabled = true;
  document.getElementById("account-submit").textContent = "Đang tạo tài khoản…";
  try {
    const result = await api("/management/resident-accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...state.payload, confirmed: true, idempotency_key: state.key }) });
    if (!isSessionCurrent(generation) || state !== accountDraft) return;
    const address = [result.profile.site_name, result.profile.building, result.profile.apartment].filter(Boolean).join(" · ");
    accountHandover = result.username;
    portalSummary(document.getElementById("account-receipt-details"), [["Cư dân", result.profile.full_name], ["Khu / căn hộ", address], ["Tài khoản", result.username]]);
    document.getElementById("account-receipt-copy").textContent = "Tài khoản đã được lưu và có thể đăng nhập. Bàn giao riêng thông tin bên dưới cho cư dân.";
    form.reset();
    form.hidden = true;
    state.payload = null;
    document.getElementById("account-receipt").hidden = false;
    document.getElementById("copy-account-handover").focus();
    void loadAccountWorkspace();
  } catch (error) {
    if (isSessionCurrent(generation) && state === accountDraft) {
      portalError("account-error", error.message);
      if (error.code === "username_unavailable") {
        state.review = false;
        document.getElementById("account-fields").disabled = false;
        document.getElementById("account-fields").hidden = false;
        document.getElementById("account-review").hidden = true;
        document.getElementById("account-confirm").required = false;
        document.getElementById("account-back").hidden = true;
        showUsernameError(document.getElementById("account-username"), "account-username-error", "Tên đăng nhập này đã được sử dụng. Hãy chọn tên khác.");
        document.getElementById("account-username").focus();
      }
    }
  } finally {
    if (state === accountDraft) {
      state.submitting = false;
      document.getElementById("account-submit").disabled = false;
      document.getElementById("account-back").disabled = false;
      document.getElementById("account-submit").textContent = state.review ? "Xác nhận và tạo tài khoản" : "Xem lại thông tin";
    }
  }
});

document.getElementById("account-back").addEventListener("click", () => {
  if (accountDraft.submitting) return;
  accountDraft = { key: crypto.randomUUID(), review: false, submitting: false };
  document.getElementById("account-fields").disabled = false;
  document.getElementById("account-fields").hidden = false;
  document.getElementById("account-review").hidden = true;
  document.getElementById("account-confirm").required = false;
  document.getElementById("account-back").hidden = true;
  document.getElementById("account-submit").textContent = "Xem lại thông tin";
  portalError("account-error");
  document.getElementById("account-full-name").focus();
});

bindUsernameValidation(document.getElementById("account-username"), "account-username-error");

document.getElementById("generate-account-password").addEventListener("click", async event => {
  if (accountDraft.review || accountDraft.submitting || accountDraft.validating || document.getElementById("account-fields").disabled) return;
  const draft = accountDraft;
  const generation = authGeneration;
  const button = event.currentTarget;
  const input = document.getElementById("account-username");
  draft.validating = true;
  button.disabled = true;
  document.getElementById("account-submit").disabled = true;
  document.getElementById("account-generated-status").textContent = "Đang kiểm tra tên đăng nhập trên hệ thống…";
  try {
    const occupied = new Set(accountDirectory.items.map(item => item.username.toLowerCase()));
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const username = `cd_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
      if (occupied.has(username)) continue;
      input.value = username;
      if (!await validateUsernameInput(input, "account-username-error")) {
        if (!isSessionCurrent(generation) || draft !== accountDraft || input.value.trim().toLowerCase() !== username) return;
        continue;
      }
      if (!isSessionCurrent(generation) || draft !== accountDraft) return;
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#";
      document.getElementById("account-password").value = Array.from(bytes, (value) => alphabet[value % alphabet.length]).join("");
      document.getElementById("account-generated-status").textContent = "Đã kiểm tra tên đăng nhập chưa trùng. Bạn có thể chỉnh lại trước khi xem lại và xác nhận cấp tài khoản.";
      return;
    }
    document.getElementById("account-generated-status").textContent = "Chưa tạo được tên phù hợp. Vui lòng thử lại hoặc nhập tên khác.";
  } finally {
    draft.validating = false;
    if (draft === accountDraft && isSessionCurrent(generation)) {
      button.disabled = false;
      document.getElementById("account-submit").disabled = !accountSites.length;
    }
  }
});
document.getElementById("show-account-password").addEventListener("click", () => {
  setAccountPasswordVisible(document.getElementById("account-password").type === "password");
});
document.getElementById("account-site").addEventListener("change", () => {
  document.getElementById("account-building").value = "";
  document.getElementById("account-apartment").value = "";
  updateLocationSuggestions();
});
document.getElementById("account-building").addEventListener("input", updateLocationSuggestions);
document.getElementById("account-resident-type").addEventListener("change", validateAccountOwner);
document.getElementById("refresh-accounts").addEventListener("click", loadAccountWorkspace);
setInterval(() => {
  if (!authReady || !hasRole("manager") || document.hidden || accountDirectoryPolling || document.getElementById("resident-accounts").hidden || document.getElementById("account-manage-panel").hidden || document.getElementById("refresh-accounts").disabled) return;
  const generation = authGeneration;
  accountDirectoryPolling = true;
  void api("/management/resident-accounts").then(data => {
    if (!isSessionCurrent(generation)) return;
    accountDirectory.items = data.items;
    renderAccountDirectory();
  }).catch(() => {}).finally(() => { accountDirectoryPolling = false; });
}, 15000);
document.getElementById("new-account").addEventListener("click", () => { resetAccountForm(); void loadAccountWorkspace(); document.getElementById("account-full-name").focus(); });
document.getElementById("copy-account-handover").addEventListener("click", async () => {
  if (!accountHandover || !authReady || !hasRole("manager")) return;
  await showAccountHandover("account", accountHandover, document.getElementById("copy-account-handover"));
});

document.getElementById("user-role").addEventListener("click", async () => {
  if (!authReady) return;
  const generation = authGeneration;
  personalReturnFocus = document.activeElement;
  const dialog = document.getElementById("personal-dialog");
  document.getElementById("personal-details").replaceChildren();
  document.getElementById("personal-status").textContent = "Đang tải thông tin tài khoản…";
  document.getElementById("personal-residency").hidden = true;
  if (chatWidget.classList.contains("open")) setChatWidgetOpen(false);
  dialog.showModal();
  try {
    const profile = hasRole("resident") ? await api(residentPath("")) : null;
    if (!isSessionCurrent(generation) || !dialog.open) return;
    residentOwnershipState = { ready: true, profile };
    synchronizeOwnershipUi();
    portalSummary(document.getElementById("personal-details"), profile
      ? [["Họ và tên", profile.full_name], ["Khu dân cư", profile.site_name], ["Tòa / khu vực", profile.building], ["Căn hộ", profile.apartment], ["Tư cách cư trú", ({ owner: "Chủ sở hữu", tenant: "Người thuê", family_member: "Thành viên gia đình" })[profile.resident_type]], ["Mã hồ sơ", profile.resident_id]]
      : [["Vai trò", "Ban quản lý"], ["Khu được quản lý", (authContext.manager_site_ids || []).join(", ")]]);
    document.getElementById("personal-status").textContent = "Thông tin khu và căn hộ được lấy từ hồ sơ tài khoản đã cấp.";
    document.getElementById("personal-identity-note").textContent = profile ? "Họ tên, tư cách, khu, tòa và căn hộ do BQL cấp, chỉ được xem. Nếu có sai sót, cần BQL đối chiếu và xác nhận điều chỉnh; cư dân không thể tự sửa." : "Thông tin phạm vi quản lý được cấp theo tài khoản.";
  } catch (error) { if (isSessionCurrent(generation)) document.getElementById("personal-status").textContent = error.message; }
});
document.getElementById("personal-close").addEventListener("click", () => document.getElementById("personal-dialog").close());
document.getElementById("personal-dialog").addEventListener("close", () => personalReturnFocus?.focus());
document.getElementById("personal-residency").addEventListener("click", () => { document.getElementById("personal-dialog").close(); void openResidentEntry("verify_residency"); });

function renderReadingActions(documents, target) {
  readingDocuments = documents;
  target.querySelectorAll(".regulation-item").forEach((item, index) => {
    const documentInfo = documents[index];
    if (!documentInfo) return;
    const footer = document.createElement("div");
    footer.className = "reading-actions";
    const status = document.createElement("p");
    status.textContent = documentInfo.acknowledged_at ? `Đã xác nhận đã đọc lúc ${new Date(documentInfo.acknowledged_at).toLocaleString("vi-VN")}` : "Chưa xác nhận đã đọc phiên bản này.";
    item.dataset.readingTitle = `${documentInfo.title} ${documentInfo.version || ""}`.toLocaleLowerCase("vi");
    item.dataset.readingAcknowledged = String(Boolean(documentInfo.acknowledged_at));
    footer.append(status);
    item.querySelector(".regulation-body").appendChild(footer);
  });
  updateReadingSelection();
  filterReadingDocuments();
}

function updateReadingSelection() {
  const completed = readingDocuments.filter(item => item.acknowledged_at).length;
  const button = document.getElementById("reading-confirm-selected");
  button.disabled = readingSubmitting || !readingDocuments.length;
  button.textContent = completed === readingDocuments.length && completed ? "Xác nhận lại đã đọc nội quy" : "Tôi xác nhận đã đọc nội quy";
}

function filterReadingDocuments() {
  const search = document.getElementById("reading-search").value.trim().toLocaleLowerCase("vi");
  const filter = document.getElementById("reading-filter").value;
  let visible = 0;
  document.querySelectorAll("#regulations-list .regulation-item").forEach(item => {
    item.hidden = Boolean(search && !item.dataset.readingTitle.includes(search)) || (filter === "read" && item.dataset.readingAcknowledged !== "true") || (filter === "unread" && item.dataset.readingAcknowledged === "true");
    if (!item.hidden) visible += 1;
  });
  document.getElementById("reading-empty").hidden = visible !== 0;
}

document.getElementById("reading-search").addEventListener("input", filterReadingDocuments);
document.getElementById("reading-filter").addEventListener("change", filterReadingDocuments);
document.getElementById("reading-confirm-selected").addEventListener("click", async () => {
  const generation = authGeneration;
  const documents = [...readingDocuments];
  if (!authReady || !hasRole("resident") || readingSubmitting || !documents.length) return;
  readingSubmitting = true;
  const button = document.getElementById("reading-confirm-selected");
  button.disabled = true;
  button.textContent = "Đang lưu xác nhận…";
  portalError("ack-error");
  document.getElementById("reading-confirm-status").textContent = "";
  try {
    await api(residentPath("/regulations/acknowledge-batch"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ documents: documents.map(item => ({ source_id: item.source_id, revision: item.revision })), confirmed: true }) });
    if (!isSessionCurrent(generation)) return;
    await loadRegulations();
    if (isSessionCurrent(generation)) await loadProgress();
    if (isSessionCurrent(generation)) document.getElementById("reading-confirm-status").textContent = "Đã ghi nhận bạn xác nhận đã đọc nội quy.";
  } catch (error) { if (isSessionCurrent(generation)) portalError("ack-error", error.message); }
  finally {
    if (isSessionCurrent(generation)) { readingSubmitting = false; updateReadingSelection(); }
  }
});

function clearPortalSession() {
  accountWorkspaceLoad += 1;
  if (typeof clearTicketDeliverySession === "function") clearTicketDeliverySession();
  accountDirectory = { items: [], loaded: false, page: 0, pageSize: 12 };
  document.getElementById("account-search").value = "";
  document.getElementById("account-filter-site").replaceChildren();
  document.getElementById("account-filter-building").replaceChildren();
  document.getElementById("account-list-count").textContent = "";
  document.getElementById("account-list-page").textContent = "Trang 1";
  document.getElementById("account-list-empty").hidden = true;
  document.getElementById("account-list-prev").disabled = true;
  document.getElementById("account-list-next").disabled = true;
  document.getElementById("refresh-accounts").disabled = false;
  portalError("account-load-error");
  selectAccountWorkspace("create");
  accountLocations = [];
  accountSites = [];
  resetAccountForm();
  readingDocuments = [];
  readingSubmitting = false;
  regulationsLoadId += 1;
  updateReadingSelection();
  document.getElementById("account-list").replaceChildren();
  document.getElementById("account-list").setAttribute("aria-busy", "false");
  if (typeof resetAccountRecovery === "function") resetAccountRecovery();
  document.getElementById("personal-details").replaceChildren();
  ["personal-dialog", "vehicle-document-dialog", "password-change-dialog", "password-result-dialog"].forEach((id) => { const dialog = document.getElementById(id); if (dialog.open) dialog.close(); });
  if (typeof clearPasswordResult === "function") clearPasswordResult();
  document.getElementById("password-change-form").reset();
  document.getElementById("reading-search").value = "";
  document.getElementById("reading-filter").value = "all";
  document.getElementById("reading-confirm-status").textContent = "";
  managementPage.offset = 0;
  residentTicketPage.offset = 0;
  for (const role of ["resident", "manager"]) selectTicketStage(role, "active", false);
}
