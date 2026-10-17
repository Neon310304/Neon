let deliveryDialog = null;
let deliveryLoadId = 0;
let deliveryReturnFocus = null;
let deliveryBusy = false;
let transientNoticeTimer = null;
const ticketResidentTypeLabels = { owner: "Chủ sở hữu", tenant: "Người thuê", family_member: "Thành viên gia đình" };

function showTransientNotice(message) {
  let notice = document.getElementById("transient-notice");
  if (!notice) {
    notice = document.createElement("div");
    notice.id = "transient-notice";
    notice.className = "ownership-toast";
    notice.setAttribute("role", "alert");
    const text = document.createElement("span");
    const close = document.createElement("button");
    close.type = "button";
    close.textContent = "×";
    close.setAttribute("aria-label", "Đóng thông báo");
    close.addEventListener("click", () => { notice.hidden = true; clearTimeout(transientNoticeTimer); });
    notice.append(text, close);
    document.body.append(notice);
  }
  notice.querySelector("span").textContent = message;
  notice.hidden = false;
  clearTimeout(transientNoticeTimer);
  transientNoticeTimer = setTimeout(() => { notice.hidden = true; }, 1500);
}

function getDeliveryDialog(title, trigger) {
  if (!deliveryDialog) {
    deliveryDialog = document.createElement("dialog");
    deliveryDialog.id = "ticket-delivery-dialog";
    deliveryDialog.className = "resident-entry-dialog ticket-delivery-dialog";
    deliveryDialog.setAttribute("aria-labelledby", "delivery-title");
    deliveryDialog.addEventListener("cancel", event => { if (deliveryBusy) event.preventDefault(); });
    deliveryDialog.addEventListener("close", () => {
      if (deliveryDialog.open) return;
      deliveryLoadId += 1;
      deliveryBusy = false;
      deliveryDialog.replaceChildren();
      if (deliveryReturnFocus?.isConnected) deliveryReturnFocus.focus();
      deliveryReturnFocus = null;
    });
    document.body.append(deliveryDialog);
  }
  if (deliveryDialog.open) deliveryDialog.close();
  deliveryReturnFocus = trigger || document.activeElement;
  deliveryDialog.innerHTML = '<header class="recovery-heading"><h2 id="delivery-title"></h2><button type="button" data-delivery-close aria-label="Đóng cửa sổ">×</button></header><div id="delivery-content"></div><p id="delivery-error" class="field-error" role="alert" hidden></p>';
  deliveryDialog.querySelector("h2").textContent = title;
  deliveryDialog.querySelector("[data-delivery-close]").addEventListener("click", () => { if (!deliveryBusy) deliveryDialog.close(); });
  deliveryDialog.showModal();
  return deliveryDialog.querySelector("#delivery-content");
}

function deliveryError(message = "") {
  const target = document.getElementById("delivery-error");
  if (target) { target.textContent = message; target.hidden = !message; }
}

function deliveryPath(kind, identity) {
  if (kind === "account") return `/management/resident-accounts/${encodeURIComponent(identity)}/handover`;
  if (hasRole("manager")) return `/management/requests/${encodeURIComponent(identity)}/account-handover`;
  return `${residentPath("/requests")}/${encodeURIComponent(identity)}/account-handover`;
}

async function showAccountHandover(kind, identity, trigger) {
  if (!authReady) return;
  const generation = authGeneration;
  const content = getDeliveryDialog("Tài khoản bàn giao", trigger);
  const loadId = ++deliveryLoadId;
  content.textContent = "Đang tải thông tin bàn giao…";
  const current = () => isSessionCurrent(generation) && loadId === deliveryLoadId && deliveryDialog.open;
  try {
    const data = await api(deliveryPath(kind, identity), { cache: "no-store" });
    if (!current()) return;
    content.innerHTML = '<label>Tên đăng nhập<input id="delivery-username" readonly autocomplete="off"></label><p id="delivery-secret-state" class="portal-help"></p>';
    document.getElementById("delivery-username").value = data.username;
    const status = document.getElementById("delivery-secret-state");
    if (!data.temporary_password) {
      status.textContent = data.password_change_required ? "Không còn mật khẩu bàn giao" : "Cư dân đã đổi mật khẩu";
      return;
    }
    status.textContent = "Mật khẩu tạm chỉ dùng cho lần đăng nhập đầu. Bàn giao riêng cho đúng cư dân.";
    const label = document.createElement("label");
    label.textContent = "Mật khẩu tạm";
    const input = document.createElement("input");
    input.id = "delivery-password";
    input.type = "password";
    input.readOnly = true;
    input.autocomplete = "off";
    input.value = data.temporary_password;
    label.setAttribute("for", input.id);
    const control = document.createElement("div");
    control.className = "password-input-control";
    const actions = document.createElement("div");
    actions.className = "delivery-actions";
    const reveal = document.createElement("button");
    reveal.type = "button";
    reveal.className = "password-visibility";
    reveal.innerHTML = '<svg class="ui-icon password-eye" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/><path class="password-eye-slash" d="m3 3 18 18"/></svg>';
    reveal.setAttribute("aria-controls", input.id);
    reveal.setAttribute("aria-label", "Hiện mật khẩu tạm");
    reveal.setAttribute("aria-pressed", "false");
    reveal.addEventListener("click", async () => {
      try {
        const latest = await api(deliveryPath(kind, identity), { cache: "no-store" });
        if (!current()) return;
        if (!latest.temporary_password) { await showAccountHandover(kind, identity, trigger); return; }
        input.value = latest.temporary_password;
        input.type = input.type === "password" ? "text" : "password";
        reveal.setAttribute("aria-label", `${input.type === "password" ? "Hiện" : "Ẩn"} mật khẩu tạm`);
        reveal.setAttribute("aria-pressed", String(input.type === "text"));
      } catch (error) { if (current()) deliveryError(error.message); }
    });
    const copy = document.createElement("button");
    copy.type = "button";
    copy.textContent = "Sao chép bàn giao";
    copy.addEventListener("click", async () => {
      copy.disabled = true;
      try {
        const latest = await api(deliveryPath(kind, identity), { cache: "no-store" });
        if (!current()) return;
        if (!latest.temporary_password) { await showAccountHandover(kind, identity, trigger); return; }
        await navigator.clipboard.writeText(`MoveInMate\nTài khoản: ${latest.username}\nMật khẩu tạm: ${latest.temporary_password}\nHãy đổi mật khẩu ngay lần đăng nhập đầu.`);
        if (current()) status.textContent = "Đã sao chép bàn giao. Chỉ gửi riêng cho đúng cư dân.";
      } catch (error) { if (current()) deliveryError(error.message || "Không sao chép được. Bạn có thể chọn thông tin để sao chép thủ công."); }
      finally { if (current()) copy.disabled = false; }
    });
    control.append(input, reveal);
    actions.append(copy);
    content.append(label, control, actions);
  } catch (error) { if (current()) { content.textContent = ""; deliveryError(error.message); } }
}

async function previewDependentAccount(requestId, trigger) {
  const generation = authGeneration;
  const content = getDeliveryDialog("Cấp tài khoản cư dân phụ thuộc", trigger);
  const loadId = ++deliveryLoadId;
  content.textContent = "Đang đối chiếu hồ sơ đã được duyệt…";
  try {
    const preview = await api(`/management/requests/${encodeURIComponent(requestId)}/dependent-account-preview`, { cache: "no-store" });
    if (!isSessionCurrent(generation) || loadId !== deliveryLoadId || !deliveryDialog.open) return;
    content.replaceChildren();
    const summary = document.createElement("dl");
    const member = preview.member;
    portalSummary(summary, [["Họ tên", member.person_name], ["Tư cách", ticketResidentTypeLabels[member.resident_type]], ["Khu", member.site_name], ["Tòa", member.building], ["Căn hộ", member.apartment]]);
    const note = document.createElement("p");
    note.className = "portal-help";
    note.textContent = "Thông tin lấy từ hồ sơ cư trú đã duyệt, không sửa tại đây. Hệ thống sinh tên đăng nhập duy nhất và mật khẩu tạm sau khi bạn xác nhận.";
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "auth-submit";
    confirm.textContent = "Xác nhận duyệt và cấp tài khoản";
    confirm.addEventListener("click", async () => {
      if (deliveryBusy) return;
      deliveryBusy = true;
      confirm.disabled = true;
      deliveryDialog.querySelector("[data-delivery-close]").disabled = true;
      deliveryError();
      try {
        await api(`/management/requests/${encodeURIComponent(requestId)}/issue-dependent-account`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ review_token: preview.review_token, confirmed: true }) });
        if (!isSessionCurrent(generation) || loadId !== deliveryLoadId) return;
        deliveryBusy = false;
        deliveryDialog.close();
        await showAccountHandover("ticket", requestId, trigger);
        void loadRequests();
        if (typeof loadAccountWorkspace === "function") void loadAccountWorkspace();
      } catch (error) {
        if (isSessionCurrent(generation) && loadId === deliveryLoadId) {
          deliveryError(error.message);
          confirm.disabled = false;
          const refresh = document.createElement("button");
          refresh.type = "button";
          refresh.textContent = "Đối chiếu lại hồ sơ";
          refresh.addEventListener("click", () => void previewDependentAccount(requestId, trigger));
          if (!content.querySelector("[data-review-again]")) { refresh.dataset.reviewAgain = "true"; content.append(refresh); }
        }
      } finally {
        deliveryBusy = false;
        const close = deliveryDialog.querySelector("[data-delivery-close]");
        if (close) close.disabled = false;
      }
    });
    content.append(summary, note, confirm);
  } catch (error) {
    if (isSessionCurrent(generation) && loadId === deliveryLoadId) { content.textContent = ""; deliveryError(error.message); }
  }
}

function addTicketDeliveryActions(host, items, listName) {
  const cards = [...host.querySelectorAll(listName === "notifications" ? "[data-notification-group]" : ".request-card")];
  cards.forEach((card, index) => {
    const item = items[index];
    if (!item) return;
    card.querySelector(".delivery-ticket-actions")?.remove();
    const actions = document.createElement("div");
    actions.className = "delivery-ticket-actions delivery-actions";
    const add = (label, field, value) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.dataset[field] = value;
      actions.append(button);
    };
    if (listName === "management" && item.request_type === "account_registration" && !item.payload?.app_account_resident_id && ["submitted", "in_review", "approved"].includes(item.status)) add("Cấp tài khoản ngẫu nhiên", "issueDependent", item.request_id);
    if (item.account_handover_available) add("Xem tài khoản, mật khẩu", "handoverTicket", item.request_id);
    if (item.account_handover_available) actions.lastElementChild.className = "delivery-handover";
    if (["completed", "rejected", "cancelled_by_resident"].includes(item.status)) {
      add(listName === "notifications" ? "Xóa thông báo" : "Xóa ticket", "hideRequest", item.request_id);
      actions.lastElementChild.dataset.hideList = listName;
      actions.lastElementChild.className = "delivery-delete";
    }
    if (listName === "management") card.querySelector(".delivery-history")?.remove();
    if (actions.childElementCount) (card.querySelector(".ticket-footer, .request-card-footer") || card).append(actions);
  });
}

async function showHideDialog(listName, requestId, trigger) {
  const generation = authGeneration;
  const noun = listName === "notifications" ? "thông báo" : "ticket";
  const label = listName === "notifications" ? "Xóa thông báo" : "Xóa ticket";
  const content = getDeliveryDialog(label, trigger);
  const loadId = ++deliveryLoadId;
  const query = new URLSearchParams(listName === "notifications" ? notificationQuery() : ticketQuery(listName === "management" ? "manager" : "resident"));
  const selection = { list_name: listName, query: query.get("q") || "" };
  for (const key of ["request_type", "status", "date_from", "date_to"]) if (query.get(key)) selection[key] = query.get(key);
  const previewPath = listName === "management" ? "/management/requests/hide/preview" : residentPath("/requests/hide/preview");
  const confirmPath = listName === "management" ? "/management/requests/hide" : residentPath(listName === "notifications" ? "/notifications/hide" : "/requests/hide");
  content.innerHTML = `<p class="portal-help">Chỉ ẩn ${requestId ? `${noun} này` : `các ${noun} phù hợp bộ lọc hiện tại, trên tất cả trang`} trong danh sách của bạn. Tài khoản, giấy tờ và lịch sử vẫn được giữ; danh sách bên còn lại không bị ảnh hưởng.</p><fieldset class="delivery-options"><legend>Trạng thái được xóa</legend><label><input type="checkbox" name="hide-status" value="completed" checked>Hoàn tất</label><label><input type="checkbox" name="hide-status" value="cancelled_by_resident" checked>Đã hủy</label><label><input type="checkbox" name="hide-status" value="rejected" checked>Từ chối</label><p class="portal-help">Tích cả ba để chọn tất cả trạng thái đã kết thúc.</p></fieldset><p id="delivery-hide-count" role="status"></p><div class="delivery-actions"><button type="button" data-hide-cancel>Hủy</button><button type="button" class="delivery-delete" data-hide-confirm disabled>${label} <span class="delivery-count" aria-hidden="true">0</span></button></div>`;
  const confirm = content.querySelector("[data-hide-confirm]");
  let review = null;
  let previewId = 0;
  const current = () => isSessionCurrent(generation) && loadId === deliveryLoadId && deliveryDialog.open;
  const refresh = async () => {
    const latest = ++previewId;
    confirm.disabled = true;
    review = null;
    deliveryError();
    document.getElementById("delivery-hide-count").textContent = "Đang đếm mục đã kết thúc…";
    const statuses = [...content.querySelectorAll('[name="hide-status"]:checked')].map(input => input.value);
    confirm.querySelector(".delivery-count").textContent = "0";
    if (!statuses.length) {
      document.getElementById("delivery-hide-count").textContent = "Chọn ít nhất một trạng thái để xóa.";
      return;
    }
    try {
      const data = await api(previewPath, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...selection, terminal_statuses: statuses, request_id: requestId || null }) });
      if (!current() || latest !== previewId) return;
      review = data;
      document.getElementById("delivery-hide-count").textContent = `${data.count} ${noun} sẽ được ẩn theo lựa chọn của bạn.`;
      confirm.querySelector(".delivery-count").textContent = String(data.count);
      confirm.setAttribute("aria-label", `Xóa ${data.count} ${noun}`);
      confirm.disabled = data.count === 0;
    } catch (error) { if (current() && latest === previewId) deliveryError(error.message); }
  };
  content.querySelectorAll('[name="hide-status"]').forEach(input => input.addEventListener("change", () => void refresh()));
  content.querySelector("[data-hide-cancel]").addEventListener("click", () => { if (!deliveryBusy) deliveryDialog.close(); });
  confirm.addEventListener("click", async () => {
    if (!review || deliveryBusy || !current()) return;
    deliveryBusy = true;
    content.querySelectorAll("button,input").forEach(input => { input.disabled = true; });
    deliveryDialog.querySelector("[data-delivery-close]").disabled = true;
    try {
      await api(confirmPath, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ review_token: review.review_token, confirmed: true }) });
      if (!current()) return;
      deliveryBusy = false;
      deliveryDialog.close();
      if (listName === "management") await loadRequests();
      else if (listName === "notifications") await loadNotificationFeed();
      else await loadResidentTickets();
      showTransientNotice("Đã xóa khỏi danh sách của bạn.");
    } catch (error) {
      if (current()) { await refresh(); deliveryError(`${error.message} Vui lòng xem lại số lượng rồi xác nhận lại.`); }
    } finally {
      deliveryBusy = false;
      if (current()) {
        content.querySelectorAll("button,input").forEach(input => { input.disabled = false; });
        confirm.disabled = !review?.count;
        deliveryDialog.querySelector("[data-delivery-close]").disabled = false;
      }
    }
  });
  await refresh();
}

function clearTicketDeliverySession() {
  deliveryLoadId += 1;
  deliveryBusy = false;
  if (deliveryDialog?.open) deliveryDialog.close();
  deliveryDialog?.replaceChildren();
  clearTimeout(transientNoticeTimer);
  const notice = document.getElementById("transient-notice");
  if (notice) notice.hidden = true;
  document.querySelectorAll(".delivery-bulk-delete").forEach(button => {
    button.querySelector(".delivery-count").textContent = "0";
    button.disabled = true;
  });
}

document.addEventListener("click", event => {
  const issue = event.target.closest("[data-issue-dependent]");
  if (issue) void previewDependentAccount(issue.dataset.issueDependent, issue);
  const handover = event.target.closest("[data-handover-ticket], [data-handover-account]");
  if (handover) void showAccountHandover(handover.dataset.handoverAccount ? "account" : "ticket", handover.dataset.handoverAccount || handover.dataset.handoverTicket, handover);
  const hide = event.target.closest("[data-hide-list]");
  if (hide) void showHideDialog(hide.dataset.hideList, hide.dataset.hideRequest || null, hide);
});

function updateTicketDeleteCount(listName, counts = {}) {
  const button = document.querySelector(`.delivery-bulk-delete[data-hide-list="${listName}"]`);
  if (!button) return;
  const query = new URLSearchParams(listName === "notifications" ? notificationQuery() : ticketQuery(listName === "management" ? "manager" : "resident"));
  const filter = query.get("status");
  const statuses = ["completed", "rejected", "cancelled_by_resident"].filter(status => !filter || ["all", "finished", "closed"].includes(filter) || filter === status);
  const count = statuses.reduce((total, status) => total + Number(counts[status] || 0), 0);
  button.querySelector(".delivery-count").textContent = String(count);
  button.setAttribute("aria-label", `${listName === "notifications" ? "Xóa thông báo" : "Xóa ticket"}: ${count} mục đã kết thúc phù hợp bộ lọc`);
  button.disabled = count === 0;
}

for (const [target, listName] of [["refresh-tickets", "tickets"], ["notification-refresh", "notifications"], ["refresh-requests", "management"]]) {
  const anchor = document.getElementById(target);
  if (!anchor) continue;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "refresh-button delivery-bulk-delete";
  button.dataset.hideList = listName;
  button.innerHTML = `<svg class="ui-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7M14 10v7"/></svg><span>${listName === "notifications" ? "Xóa thông báo" : "Xóa ticket"}</span><span class="delivery-count" aria-hidden="true">0</span>`;
  button.disabled = true;
  const role = listName === "management" ? "manager" : "resident";
  if (listName !== "notifications") {
    button.hidden = ticketStages[role] !== "finished";
  }
  const actions = document.createElement("div");
  actions.className = "section-heading-actions";
  anchor.before(actions);
  actions.append(button, anchor);
}

Object.assign(apiErrorLabels, {
  settings_encryption_key_required: "Chưa cấu hình khóa mã hóa bàn giao. Liên hệ người quản trị.",
  review_changed: "Hồ sơ hoặc danh sách đã thay đổi. Vui lòng xem lại trước khi xác nhận.",
  review_expired: "Lượt xem lại đã hết hạn. Vui lòng mở lại để đối chiếu.",
  account_request_not_eligible: "Ticket chưa đủ điều kiện cấp tài khoản hoặc đã được cấp.",
  request_not_terminal: "Chỉ được xóa mục đã Hoàn tất, Từ chối hoặc Đã hủy.",
  handover_unavailable: "Không đọc được mật khẩu bàn giao. Liên hệ BQL để cấp lại nếu cần.",
});
