let notificationState = { items: new Map(), latest: 0, unread: 0, loading: false, silent: false, submitting: false, offset: 0, limit: 20, total: 0, hasMore: false, loadId: 0 };
let notificationFilterTimer = null;

function clearNotificationSession() {
  clearTimeout(notificationFilterTimer);
  notificationState = { items: new Map(), latest: 0, unread: 0, loading: false, silent: false, submitting: false, offset: 0, limit: 20, total: 0, hasMore: false, loadId: 0 };
  document.getElementById("notification-list").replaceChildren();
  document.getElementById("notification-error").hidden = true;
  resetNotificationFilters();
  updateNotificationSummary();
}

function notificationQuery() {
  const params = new URLSearchParams({ offset: String(notificationState.offset), limit: String(notificationState.limit) });
  for (const [suffix, key] of [["search", "q"], ["type-filter", "request_type"], ["status-filter", "status"], ["date-from", "date_from"], ["date-to", "date_to"]]) {
    const value = document.getElementById(`notification-${suffix}`).value.trim();
    if (value && value !== "all") params.set(key, value);
  }
  return params.toString();
}

function resetNotificationFilters() {
  document.querySelectorAll("#notification-filters input").forEach(input => { input.value = ""; });
  document.querySelectorAll("#notification-filters select").forEach(select => { select.value = "all"; });
}

function updateNotificationSummary() {
  const { unread, submitting, loading, total, offset, limit, hasMore } = notificationState;
  const busy = loading && !notificationState.silent;
  const count = document.getElementById("notification-tab-count");
  count.hidden = unread === 0;
  count.textContent = String(unread);
  const notice = document.getElementById("management-notice");
  notice.hidden = unread === 0;
  const noticeText = `${unread} cập nhật BQL chưa đọc`;
  if (notice.textContent !== noticeText) notice.textContent = noticeText;
  const announcement = document.getElementById("notification-announcement");
  if (announcement.textContent !== noticeText) announcement.textContent = noticeText;
  document.getElementById("notification-summary").textContent = busy ? "Đang tải thông báo…" : `${total} ticket phù hợp · ${unread} cập nhật chưa đọc trong tất cả ticket`;
  document.getElementById("notification-read-all").disabled = !unread || submitting || busy;
  document.querySelectorAll("[data-notification-read-ticket]").forEach(button => {
    button.disabled = submitting || busy || !notificationState.items.get(button.dataset.notificationReadTicket)?.unread_count;
  });
  document.getElementById("notification-prev").disabled = busy || submitting || offset === 0;
  document.getElementById("notification-next").disabled = busy || submitting || !hasMore;
  document.getElementById("notification-page").textContent = `Trang ${Math.floor(offset / limit) + 1} / ${Math.max(1, Math.ceil(total / limit))}`;
  document.getElementById("notification-list").setAttribute("aria-busy", String(busy));
}

function notificationTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", dateStyle: "short", timeStyle: "short" });
}

function notificationHistoryMarkup(events) {
  return [...events].reverse().map((event, index, history) => {
    const current = index === history.length - 1;
    return `<li class="ticket-timeline-step${current ? " current" : ""}" data-status="${escapeHtml(event.status)}"${current ? ' aria-current="step"' : ""}><div class="ticket-timeline-heading"><strong>${escapeHtml(requestStatusLabels[event.status] || event.status)}</strong><time datetime="${escapeHtml(event.created_at)}">${escapeHtml(notificationTime(event.created_at))}</time></div>${event.reason ? `<p>${escapeHtml(event.reason)}</p>` : ""}<small class="notification-event-read">${event.read_at ? "Đã đọc" : "Chưa đọc"}</small></li>`;
  }).join("");
}

function renderNotificationFeed() {
  const list = document.getElementById("notification-list");
  const focused = document.activeElement;
  const existing = new Map([...list.querySelectorAll("[data-notification-group]")].map(card => [card.dataset.notificationGroup, card]));
  const anchor = [...existing.values()].find(card => card.getBoundingClientRect().bottom > 0);
  const anchorTop = anchor?.getBoundingClientRect().top;
  for (const [index, item] of [...notificationState.items.values()].entries()) {
    let card = existing.get(item.request_id);
    if (!card) {
      card = document.createElement("article");
      card.dataset.notificationGroup = item.request_id;
      card.innerHTML = '<header class="request-card-header"></header><p class="notification-meta"></p><p class="notification-latest"></p><details class="ticket-timeline"><summary><span>Lịch sử cập nhật</span><span class="ticket-timeline-count"></span></summary><ol></ol></details><div class="notification-actions"><button type="button" data-notification-read-ticket>Đánh dấu đã đọc</button><button type="button" data-notification-ticket>Xem ticket</button><span class="notification-read-status"></span></div>';
      card.querySelector("[data-notification-read-ticket]").dataset.notificationReadTicket = item.request_id;
      card.querySelector("[data-notification-ticket]").dataset.notificationTicket = item.request_id;
    }
    card.className = `request-card notification-item status-border-${item.status}`;
    let correction = card.querySelector("[data-notification-correction]");
    if (!correction) {
      correction = document.createElement("button");
      correction.type = "button";
      correction.dataset.notificationCorrection = item.request_id;
      correction.textContent = "Bổ sung thông tin";
      correction.setAttribute("aria-controls", "ticket-correction-dialog");
      card.querySelector(".notification-actions").prepend(correction);
    }
    correction.hidden = item.status !== "needs_info";
    card.classList.toggle("is-read", !item.unread_count);
    const signature = JSON.stringify([item.request_type, item.status, item.created_at, item.events, item.unread_count]);
    if (card.dataset.signature !== signature) {
      card.querySelector("header").innerHTML = `<div class="request-identity"><div><span class="request-kind">${escapeHtml(requestTypeLabels[item.request_type] || item.request_type)}</span><strong>${escapeHtml(item.request_id)}</strong></div></div><span class="request-status status-${escapeHtml(item.status)}">${escapeHtml(requestStatusLabels[item.status] || item.status)}</span>`;
      const latest = item.events[0];
      card.querySelector(".notification-meta").textContent = `Gửi ${notificationTime(item.created_at)} · BQL cập nhật ${notificationTime(latest?.created_at)}`;
      card.querySelector(".notification-latest").textContent = latest?.reason || "Ban quản lý đã cập nhật tiến độ. Mở lịch sử để xem chi tiết.";
      card.querySelector(".ticket-timeline-count").textContent = `${item.events.length} cập nhật`;
      card.querySelector(".ticket-timeline ol").innerHTML = notificationHistoryMarkup(item.events);
      card.querySelector(".notification-read-status").textContent = item.unread_count ? `${item.unread_count} chưa đọc` : "✓ Đã đọc tất cả";
      card.dataset.signature = signature;
    }
    if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null);
    existing.delete(item.request_id);
  }
  existing.forEach(card => card.remove());
  list.querySelector(".empty-state")?.remove();
  if (!notificationState.items.size) list.innerHTML = '<div class="empty-state"><strong>Không có thông báo phù hợp</strong><p>Thử đổi từ khóa, loại yêu cầu, trạng thái hoặc khoảng ngày. Xóa bộ lọc để xem tất cả ticket có cập nhật từ BQL.</p></div>';
  if (typeof addTicketDeliveryActions === "function" && notificationState.items.size) addTicketDeliveryActions(list, [...notificationState.items.values()], "notifications");
  if (focused?.isConnected && document.activeElement !== focused) focused.focus({ preventScroll: true });
  if (anchor?.isConnected && document.getElementById("notifications").classList.contains("active") && window.scrollY > 0) window.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
  updateNotificationSummary();
}

async function loadNotificationFeed({ silent = false } = {}) {
  if (!authReady || !hasRole("resident") || notificationState.submitting || (silent && notificationState.loading)) return;
  const state = notificationState;
  const generation = authGeneration;
  const loadId = ++state.loadId;
  state.loading = true;
  state.silent = silent;
  updateNotificationSummary();
  try {
    const data = await api(residentPath(`/notifications/groups?${notificationQuery()}`));
    if (!isSessionCurrent(generation) || state !== notificationState || loadId !== state.loadId) return;
    const lastOffset = Math.max(0, Math.ceil(data.total / state.limit) - 1) * state.limit;
    if (state.offset > lastOffset) { state.offset = lastOffset; return await loadNotificationFeed(); }
    const changed = state.latest && data.latest_event_id > state.latest;
    state.items = new Map(data.items.map(item => [item.request_id, item]));
    state.latest = data.latest_event_id;
    state.unread = data.unread_count;
    state.total = data.total;
    if (typeof updateTicketDeleteCount === "function") updateTicketDeleteCount("notifications", data.counts_by_status);
    state.hasMore = data.has_more;
    document.getElementById("notification-error").hidden = true;
    renderNotificationFeed();
    if (changed) {
      if (document.getElementById("tickets").classList.contains("active")) void loadResidentTickets();
      if (document.getElementById("household").classList.contains("active")) void loadHousehold();
      if (document.getElementById("progress").classList.contains("active")) void loadProgress();
    }
  } catch (error) {
    if (!silent && isSessionCurrent(generation) && state === notificationState && loadId === state.loadId) {
      document.getElementById("notification-error").textContent = error.message;
      document.getElementById("notification-error").hidden = false;
    }
  } finally {
    if (isSessionCurrent(generation) && state === notificationState && loadId === state.loadId) { state.loading = false; updateNotificationSummary(); }
  }
}

async function markNotificationsRead(requestId = null) {
  if (!authReady || !hasRole("resident") || notificationState.submitting || (notificationState.loading && !notificationState.silent)) return;
  const state = notificationState;
  const generation = authGeneration;
  const through = requestId === null ? state.latest : state.items.get(requestId)?.events[0]?.event_id;
  if (!through) return;
  state.loadId += 1;
  state.loading = false;
  state.submitting = true;
  updateNotificationSummary();
  try {
    const path = requestId === null ? "/notifications/read-all" : `/notifications/tickets/${encodeURIComponent(requestId)}/read`;
    await api(residentPath(path), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ through_event_id: through }) });
    if (!isSessionCurrent(generation) || state !== notificationState) return;
    state.submitting = false;
    await loadNotificationFeed();
  } catch (error) {
    if (isSessionCurrent(generation) && state === notificationState) {
      document.getElementById("notification-error").textContent = error.message;
      document.getElementById("notification-error").hidden = false;
    }
  } finally {
    if (isSessionCurrent(generation) && state === notificationState) { state.submitting = false; updateNotificationSummary(); }
  }
}

function reloadNotificationFilters(debounce = false) {
  notificationState.offset = 0;
  notificationState.loadId += 1;
  clearTimeout(notificationFilterTimer);
  if (debounce) notificationFilterTimer = setTimeout(() => void loadNotificationFeed(), 300);
  else void loadNotificationFeed();
}

for (const suffix of ["type-filter", "status-filter"]) {
  document.getElementById(`notification-${suffix}`).innerHTML = document.getElementById(`request-${suffix}`).innerHTML;
}
document.getElementById("notification-read-all").addEventListener("click", () => void markNotificationsRead());
document.getElementById("notification-refresh").addEventListener("click", () => void loadNotificationFeed());
document.getElementById("notification-clear-filters").addEventListener("click", () => { resetNotificationFilters(); reloadNotificationFilters(); });
document.getElementById("notification-search").addEventListener("input", () => reloadNotificationFilters(true));
document.querySelectorAll("#notification-filters select, #notification-filters input[type=date]").forEach(input => input.addEventListener("change", () => reloadNotificationFilters()));
for (const [direction, step] of [["prev", -1], ["next", 1]]) {
  document.getElementById(`notification-${direction}`).addEventListener("click", () => {
    notificationState.offset = Math.max(0, notificationState.offset + step * notificationState.limit);
    void loadNotificationFeed();
  });
}
document.getElementById("notification-list").addEventListener("click", event => {
  const correction = event.target.closest("[data-notification-correction]");
  if (correction) void openTicketCorrection(correction.dataset.notificationCorrection, correction);
  const read = event.target.closest("[data-notification-read-ticket]");
  if (read) void markNotificationsRead(read.dataset.notificationReadTicket);
  const ticket = event.target.closest("[data-notification-ticket]");
  if (ticket) {
    clearTimeout(ticketFilterTimers.get("resident"));
    document.querySelectorAll("#resident-ticket-toolbar input").forEach(input => { input.value = ""; });
    document.querySelectorAll("#resident-ticket-toolbar select").forEach(select => { select.value = "all"; });
    document.getElementById("resident-ticket-search").value = ticket.dataset.notificationTicket;
    const item = notificationState.items.get(ticket.dataset.notificationTicket);
    selectTicketStageForStatus("resident", item?.status || "submitted");
    residentTicketPage.offset = 0;
    openResidentTab("tickets");
    document.getElementById("tab-tickets").focus();
  }
});
