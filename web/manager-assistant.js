let managerAgentRequest = null;
let managerAgentSelectedId = null;
let managerAgentReturnFocus = null;
let managerAgentPending = null;
let managerBatchState = null;
const managerAgentDialog = document.getElementById("manager-agent-dialog");
const managerAgentMessages = document.getElementById("manager-agent-messages");

function resetManagementAgent() {
  if (typeof resetManagerConversation === "function") resetManagerConversation();
  managerAgentRequest?.abort();
  managerAgentRequest = null;
  managerAgentSelectedId = null;
  if (managerAgentDialog.open) managerAgentDialog.close();
  if (document.getElementById("manager-batch-dialog").open) document.getElementById("manager-batch-dialog").close();
  managerBatchState = null;
  document.getElementById("manager-batch-tickets").replaceChildren();
  document.getElementById("manager-batch-form").reset();
  document.getElementById("manager-batch-error").textContent = "";
  document.getElementById("manager-batch-submit").disabled = false;
  document.getElementById("manager-agent-launcher").setAttribute("aria-expanded", "false");
  managerAgentDialog.classList.remove("expanded");
  document.getElementById("manager-agent-expand").setAttribute("aria-pressed", "false");
  document.getElementById("manager-agent-expand").setAttribute("aria-label", "Mở rộng trợ lý quản lý");
  document.getElementById("manager-agent-input").value = "";
  managerAgentMessages.replaceChildren();
  document.getElementById("manager-agent-launcher").hidden = true;
  updateManagerAgentSelection();
  setManagerAgentBusy(false);
}

function configureManagementAgent() {
  const available = authReady && hasRole("manager");
  document.getElementById("manager-agent-launcher").hidden = !available;
  if (!available) resetManagementAgent();
}

function openManagementAgent(trigger) {
  if (!authReady || !hasRole("manager")) return;
  managerAgentReturnFocus = trigger || document.activeElement;
  if (!managerAgentMessages.childElementCount) {
    managerAgentMessage("assistant", "Bạn muốn quản lý nhóm ticket nào? Tôi có thể gom theo loại hoặc trạng thái, tiếp nhận nhóm, chuẩn bị hoàn tất hồ sơ đã duyệt và đề xuất xóa ticket đã kết thúc. Tôi luôn liệt kê trước để bạn xác nhận; duyệt, bổ sung và từ chối do bạn xử lý trực tiếp.");
  }
  if (!managerAgentDialog.open) {
    if (managerAgentDialog.classList.contains("expanded")) managerAgentDialog.showModal();
    else managerAgentDialog.show();
  }
  document.getElementById("manager-agent-launcher").setAttribute("aria-expanded", "true");
  document.getElementById("manager-agent-input").focus();
}

function closeManagementAgent() {
  managerAgentRequest?.abort();
  managerAgentRequest = null;
  setManagerAgentBusy(false);
  managerAgentDialog.close();
  document.getElementById("manager-agent-launcher").setAttribute("aria-expanded", "false");
  if (managerAgentReturnFocus?.isConnected) managerAgentReturnFocus.focus();
}

function setManagerAgentBusy(busy) {
  document.getElementById("manager-agent-form").setAttribute("aria-busy", String(busy));
  document.getElementById("manager-agent-send").disabled = busy;
  document.getElementById("manager-agent-send").textContent = busy ? "Đang xử lý…" : "Gửi";
  managerAgentPending?.remove();
  managerAgentPending = null;
  if (busy) {
    const pending = document.createElement("article");
    pending.id = "manager-agent-pending";
    pending.className = "manager-agent-message manager-agent-assistant manager-agent-pending";
    pending.setAttribute("aria-busy", "true");
    const dots = document.createElement("span");
    dots.className = "manager-agent-dots";
    dots.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 3; index += 1) dots.append(document.createElement("i"));
    const status = document.createElement("span");
    status.textContent = "Đang phân tích yêu cầu…";
    pending.append(dots, status);
    managerAgentMessages.append(pending);
    managerAgentPending = pending;
    managerAgentMessages.scrollTop = managerAgentMessages.scrollHeight;
  }
  document.querySelectorAll("[data-manager-prompt]").forEach((button) => { button.disabled = busy; });
}

function updateManagerAgentSelection() {
  const selected = document.getElementById("manager-agent-selection");
  selected.hidden = !managerAgentSelectedId;
  document.getElementById("manager-agent-selected-id").textContent = managerAgentSelectedId || "";
}

function managerAgentMessage(role, text, metadata = {}) {
  const article = document.createElement("article");
  article.className = `manager-agent-message manager-agent-${role}`;
  const label = document.createElement("strong");
  label.textContent = role === "user" ? "Bạn" : "MoveInMate";
  const body = document.createElement("p");
  body.textContent = text;
  if (role === "assistant" && typeof renderMarkdown === "function") {
    body.className = "markdown-body";
    body.innerHTML = renderMarkdown(text);
  }
  article.append(label, body);
  if (role === "assistant") {
    const avatar = document.createElement("img");
    avatar.src = "/static/agent-mascot.svg?v=20261013-v1";
    avatar.alt = "";
    avatar.width = 36;
    avatar.height = 36;
    avatar.className = "manager-agent-avatar";
    label.append(avatar);
    if (metadata.tools_used?.length) {
      const traces = document.createElement("div");
      traces.className = "manager-agent-traces";
      traces.setAttribute("aria-label", "Công cụ trợ lý đã dùng");
      const labels = { QueueLookup: "Đọc hàng đợi", TicketInspection: "Đối chiếu hồ sơ", OperationPreparation: "Kiểm tra thao tác đề xuất", ManagementWorkflow: "Tra cứu quy trình" };
      for (const tool of metadata.tools_used) {
        if (!Object.hasOwn(labels, tool.tool)) continue;
        const trace = document.createElement("span");
        trace.textContent = labels[tool.tool];
        traces.append(trace);
      }
      if (traces.childElementCount) article.append(traces);
    }
    if (metadata.agent_mode === "unavailable") article.dataset.agentError = "true";
  }
  managerAgentMessages.append(article);
  while (managerAgentMessages.childElementCount > 40) managerAgentMessages.firstElementChild.remove();
  managerAgentMessages.scrollTop = managerAgentMessages.scrollHeight;
  return article;
}

function managerAgentButton(parent, title, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = title;
  button.addEventListener("click", action);
  parent.append(button);
  return button;
}

async function openManagerAgentQueue(filters = {}) {
  closeManagementAgent();
  document.querySelector('[data-tab="management"]').click();
  document.getElementById("request-search").value = filters.query || "";
  document.getElementById("request-type-filter").value = filters.request_type || "all";
  selectTicketStageForStatus("manager", filters.status);
  document.getElementById("request-status-filter").value = filters.status || "all";
  document.getElementById("request-date-from").value = filters.date_from || "";
  document.getElementById("request-date-to").value = filters.date_to || "";
  managementPage.offset = 0;
  await loadRequests();
  const list = document.getElementById("request-list");
  list.setAttribute("tabindex", "-1");
  list.focus();
}

async function reviewManagerAgentDraft(draft, trigger) {
  if (!authReady || !hasRole("manager") || managerAgentRequest) return;
  const controller = new AbortController();
  managerAgentRequest = controller;
  setManagerAgentBusy(true);
  trigger.disabled = true;
  try {
    const generation = authGeneration;
    const data = await api(`/management/requests?q=${encodeURIComponent(draft.request_id)}`, { signal: controller.signal });
    if (controller.signal.aborted || !isSessionCurrent(generation) || !hasRole("manager")) return;
    const item = data.items.find((ticket) => ticket.request_id === draft.request_id);
    if (!item || item.updated_at !== draft.expected_updated_at || !["submitted", "in_review", "needs_info"].includes(item.status)) {
      managerAgentMessage("assistant", "Ticket đã thay đổi. Hãy xem lại thông tin mới và tạo bản nháp khác; chưa gửi nội dung cũ.");
      return;
    }
    closeManagementAgent();
    decide(draft.request_id, "needs_info", document.getElementById("manager-agent-launcher"));
    decisionReason.value = String(draft.reason).slice(0, 500);
  } catch {
    if (!controller.signal.aborted && authReady && hasRole("manager")) managerAgentMessage("assistant", "Không thể kiểm tra ticket. Hãy mở hàng đợi để xử lý trực tiếp.");
  } finally {
    if (managerAgentRequest === controller) {
      managerAgentRequest = null;
      setManagerAgentBusy(false);
    }
    trigger.disabled = false;
  }
}

function renderManagerAgentResult(data) {
  if (typeof acceptManagerConversation === "function" && acceptManagerConversation(data) === false) return;
  if ((data.view || data.proposal || data.execution) && typeof renderManagerConversation === "function") {
    renderManagerConversation(data);
    return;
  }
  if (data.accounts || data.recovery_requests) {
    const recovery = managerAgentMessage("assistant", data.answer);
    managerAgentButton(recovery, "Xem yêu cầu khôi phục", () => {
      closeManagementAgent();
      document.querySelector('[data-tab="resident-accounts"]').click();
      selectAccountWorkspace("manage", true);
    });
    for (const account of data.accounts || []) {
      managerAgentButton(recovery, `${account.username} · căn ${account.apartment} · Đặt lại mật khẩu`, () => {
        closeManagementAgent();
        openManagerPasswordReset(account.username);
      });
    }
    return;
  }
  if (data.selected_request_id) {
    managerAgentSelectedId = data.selected_request_id;
    updateManagerAgentSelection();
  } else if (data.filters) {
    managerAgentSelectedId = null;
    updateManagerAgentSelection();
  }
  const message = managerAgentMessage("assistant", data.answer, data);
  if (data.policy_code) return;
  if (data.filters) {
    managerAgentButton(message, `Mở danh sách (${data.total})`, () => openManagerAgentQueue(data.filters));
  }
  for (const group of (data.groups || []).slice(0, 6)) {
    const card = document.createElement("section");
    card.className = "manager-agent-ticket manager-agent-group";
    const title = document.createElement("strong");
    title.textContent = `${group.title} · ${group.total} hồ sơ`;
    const detail = document.createElement("p");
    const variants = Object.entries(group.variant).map(([key, value]) => ({ vehicle_type: { car: "Ô tô", motorbike: "Xe máy" }[value], visit_date: `Ngày đến ${value}`, move_date: `Ngày chuyển ${value}`, move_time: `Giờ ${value}`, stay_start: `Bắt đầu ở ${value}` })[key] || value);
    detail.textContent = `${group.site_name}${group.building ? ` · tòa ${group.building}` : ""} · ${group.status_label}${variants.length ? ` · ${variants.join(" · ")}` : ""}`;
    const note = document.createElement("small");
    note.textContent = group.action === "needs_info" ? `Cần bổ sung: ${group.missing.join(", ")}.` : "Đã kiểm tra dữ liệu biểu mẫu. Ngoại lệ được tách riêng; chưa cập nhật ticket nào.";
    card.append(title, detail, note);
    managerAgentButton(card, ({ in_review: "Chuẩn bị tiếp nhận", approved: "Chuẩn bị duyệt", completed: "Xác nhận kết quả hoàn tất", needs_info: "Chuẩn bị yêu cầu bổ sung" })[group.action], () => reviewManagerAgentGroup(group));
    if (group.total > group.tickets.length) {
      const limit = document.createElement("small");
      limit.textContent = `Lần này xử lý tối đa ${group.tickets.length} / ${group.total} hồ sơ. Rà soát lại sau khi xác nhận để xem phần còn lại.`;
      card.append(limit);
    }
    message.append(card);
  }
  if (data.groups?.length > 6) {
    const note = document.createElement("p");
    note.textContent = `Đang hiển thị 6 / ${data.groups.length} danh sách xử lý. Hỏi thêm loại thủ tục, căn hộ hoặc khoảng ngày để thu hẹp kết quả.`;
    message.append(note);
  }
  if (data.exceptions?.length) {
    const exceptions = document.createElement("details");
    exceptions.className = "manager-agent-exceptions";
    const heading = document.createElement("summary");
    heading.textContent = `Cần xem riêng · ${data.exception_total} hồ sơ`;
    exceptions.append(heading);
    message.append(exceptions);
    for (const item of data.exceptions) {
      const card = document.createElement("section");
      card.className = "manager-agent-ticket";
      card.dataset.status = item.status;
      const title = document.createElement("strong");
      title.textContent = `${item.request_id} · ${item.title}`;
      const note = document.createElement("p");
      note.textContent = item.notes.join(". ");
      card.append(title, note);
      if (item.decision_draft) {
        const button = managerAgentButton(card, "Xem yêu cầu bổ sung", () => reviewManagerAgentDraft(item.decision_draft, button));
      } else {
        managerAgentButton(card, "Xem và xử lý ticket", () => openManagerAgentQueue({ query: item.request_id, status: item.status }));
      }
      exceptions.append(card);
    }
  }
  if (data.truncated) {
    const note = document.createElement("p");
    note.textContent = "Đang phân tích 200 hồ sơ mới nhất, chưa phải toàn bộ hàng đợi. Hãy lọc theo loại hoặc khoảng ngày để xem đủ nhóm cần xử lý.";
    message.append(note);
  }
  for (const item of data.tickets || []) {
    const card = document.createElement("section");
    card.className = "manager-agent-ticket";
    card.dataset.status = item.status;
    const title = document.createElement("strong");
    title.textContent = `${item.request_id} · ${item.title}`;
    const detail = document.createElement("small");
    detail.textContent = `${item.status_label} · ${item.site_name} · căn ${item.apartment}`;
    const actions = document.createElement("div");
    actions.className = "manager-agent-actions";
    if (item.request_id !== data.selected_request_id) managerAgentButton(actions, "Tóm tắt", () => {
      managerAgentSelectedId = item.request_id;
      updateManagerAgentSelection();
      sendManagerAgentMessage(`Tóm tắt lịch sử ticket ${item.request_id}`);
    });
    managerAgentButton(actions, "Mở ticket", () => openManagerAgentQueue({ query: item.request_id, status: item.status }));
    if (!data.decision_draft && ["submitted", "in_review", "needs_info"].includes(item.status)) {
      managerAgentButton(actions, "Gợi ý bổ sung", () => {
        managerAgentSelectedId = item.request_id;
        updateManagerAgentSelection();
        sendManagerAgentMessage(`Soạn yêu cầu bổ sung cho ticket ${item.request_id}`);
      });
    }
    card.append(title, detail, actions);
    message.append(card);
  }
  if (data.decision_draft) {
    const draft = document.createElement("section");
    draft.className = "manager-agent-draft";
    const label = document.createElement("strong");
    label.textContent = "Bản nháp · chưa gửi cư dân";
    const text = document.createElement("p");
    text.textContent = data.decision_draft.reason;
    draft.append(label, text);
    const button = managerAgentButton(draft, "Xem lại và chỉnh nội dung", () => reviewManagerAgentDraft(data.decision_draft, button));
    message.append(draft);
  }
  managerAgentMessages.scrollTop = managerAgentMessages.scrollHeight;
}

async function sendManagerAgentMessage(value) {
  const message = value.trim();
  if (!message || managerAgentRequest || !authReady || !hasRole("manager")) return;
  if (message.length > 2000) return;
  const generation = authGeneration;
  const controller = new AbortController();
  managerAgentRequest = controller;
  managerAgentMessage("user", message);
  setManagerAgentBusy(true);
  try {
    const data = await api("/management/assistant/chat", {
      method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, request_id: managerAgentSelectedId, ...(typeof managerConversationPayload === "function" ? managerConversationPayload() : {}) }),
    });
    if (controller.signal.aborted || !isSessionCurrent(generation) || !hasRole("manager")) return;
    managerAgentPending?.remove();
    managerAgentPending = null;
    renderManagerAgentResult(data);
    if (data.execution) await loadRequests();
  } catch (error) {
    if (!controller.signal.aborted && isSessionCurrent(generation) && hasRole("manager")) {
      if (typeof recoverManagerConversation === "function") await recoverManagerConversation(error);
      managerAgentMessage("assistant", apiErrorLabels[error.message] || (error.message === "request_not_found" ? "Không tìm thấy ticket trong khu bạn được quản lý." : "Chưa thể đọc yêu cầu. Kiểm tra kết nối hoặc mở hàng đợi để xử lý trực tiếp."));
    }
  } finally {
    if (managerAgentRequest === controller) {
      managerAgentRequest = null;
      setManagerAgentBusy(false);
    }
  }
}

document.getElementById("manager-agent-launcher").addEventListener("click", (event) => openManagementAgent(event.currentTarget));
document.getElementById("manager-agent-close").addEventListener("click", closeManagementAgent);
managerAgentDialog.addEventListener("cancel", (event) => { event.preventDefault(); closeManagementAgent(); });
document.getElementById("manager-agent-expand").addEventListener("click", (event) => {
  const expanded = managerAgentDialog.classList.toggle("expanded");
  managerAgentDialog.close();
  if (expanded) managerAgentDialog.showModal();
  else managerAgentDialog.show();
  event.currentTarget.setAttribute("aria-pressed", String(expanded));
  event.currentTarget.setAttribute("aria-label", expanded ? "Thu nhỏ trợ lý quản lý" : "Mở rộng trợ lý quản lý");
});

async function reviewManagerAgentGroup(group) {
  if (!authReady || !hasRole("manager") || managerAgentRequest || managerBatchState) return;
  const controller = new AbortController();
  managerAgentRequest = controller;
  setManagerAgentBusy(true);
  const generation = authGeneration;
  try {
    const data = await api("/management/assistant/batch-preview", { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: group.action, tickets: group.tickets.map(ticket => ({ request_id: ticket.request_id, expected_updated_at: ticket.updated_at })) }) });
    if (controller.signal.aborted || !isSessionCurrent(generation) || !hasRole("manager")) return;
    const items = data.items;
    if (items.length !== group.tickets.length || items.some(item => !group.tickets.some(snapshot => snapshot.request_id === item.request_id && snapshot.updated_at === item.updated_at))) throw new Error("batch_ticket_changed");
    closeManagementAgent();
    const dialog = document.getElementById("manager-batch-dialog");
    const form = document.getElementById("manager-batch-form");
    form.reset();
    document.getElementById("manager-batch-error").textContent = "";
    const actions = { in_review: "Tiếp nhận", approved: "Duyệt", completed: "Hoàn tất", needs_info: "Yêu cầu bổ sung" };
    document.getElementById("manager-batch-description").textContent = `${actions[group.action]} · ${group.title} · ${items.length} hồ sơ cùng điều kiện phân nhóm. Mỗi ticket vẫn giữ lịch sử riêng.`;
    document.getElementById("manager-batch-consent-label").textContent = group.action === "completed" ? "Tôi xác nhận các hồ sơ được chọn đã hoàn tất trên thực tế, không chỉ có dữ liệu đầy đủ." : group.action === "approved" ? "Tôi đã xem bảng rà soát, đối chiếu nội dung cần kiểm chứng và chịu trách nhiệm duyệt các hồ sơ được chọn." : "Tôi đã xem kết quả rà soát và đồng ý gửi nội dung này đến các cư dân được chọn.";
    document.getElementById("manager-batch-submit").textContent = `Xác nhận ${actions[group.action].toLowerCase()} nhóm`;
    document.getElementById("manager-batch-reason").value = { in_review: "BQL đã tiếp nhận và đang xem xét hồ sơ.", approved: "BQL đã xem kết quả rà soát, đối chiếu thông tin và đồng ý duyệt hồ sơ.", completed: "BQL xác nhận đã kiểm tra kết quả và hoàn tất thực tế.", needs_info: `Vui lòng bổ sung ${(group.missing || []).join(", ")} để BQL tiếp tục đối chiếu hồ sơ.` }[group.action];
    const list = document.getElementById("manager-batch-tickets");
    list.replaceChildren();
    const reviewed = new Map();
    for (const item of items) {
      const row = document.createElement("section");
      row.className = "manager-batch-ticket";
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `${item.request_id} · ${item.resident_profile?.full_name || "Cư dân"} · căn ${item.resident_profile?.apartment || ""}`;
      const body = document.createElement("div");
      body.innerHTML = residentProfileDetails(item) + requestDetails(item);
      details.append(summary, body);
      if (item.request_type === "vehicle_registration") {
        const button = managerAgentButton(body, "Xem giấy đăng ký xe", () => openVehicleDocument(item.request_id, button));
      }
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = false;
      checkbox.addEventListener("change", updateManagerBatchSelection);
      const copy = document.createElement("span");
      copy.textContent = `${item.request_id} · ${item.resident_profile?.full_name || "Cư dân"} · căn ${item.resident_profile?.apartment || ""}`;
      summary.textContent = "Xem hồ sơ chi tiết";
      const checks = document.createElement("dl");
      checks.className = "manager-batch-checks";
      for (const check of item.review?.checks || []) {
        const term = document.createElement("dt");
        term.textContent = check.label;
        const value = document.createElement("dd");
        value.textContent = check.value;
        checks.append(term, value);
      }
      label.append(checkbox, copy);
      row.append(label, checks, details);
      list.append(row);
      reviewed.set(item.request_id, checkbox);
    }
    managerBatchState = { items, reviewed, action: group.action, generation, submitting: false };
    updateManagerBatchSelection();
    dialog.showModal();
  } catch (error) {
    if (!controller.signal.aborted && isSessionCurrent(generation)) managerAgentMessage("assistant", error.message === "batch_ticket_changed" ? "Nhóm hồ sơ đã thay đổi. Hãy tạo lại gợi ý và kiểm tra thông tin mới; chưa cập nhật ticket nào." : "Chưa đọc được nhóm hồ sơ. Thử lại hoặc mở từng ticket từ hàng đợi.");
  } finally {
    if (managerAgentRequest === controller) { managerAgentRequest = null; setManagerAgentBusy(false); }
  }
}

function closeManagerBatch() {
  if (managerBatchState?.submitting) return;
  document.getElementById("manager-batch-dialog").close();
  document.getElementById("manager-batch-tickets").replaceChildren();
  document.getElementById("manager-batch-form").reset();
  managerBatchState = null;
  if (authReady && hasRole("manager")) openManagementAgent(document.getElementById("manager-agent-launcher"));
}

async function submitManagerBatch(event) {
  event.preventDefault();
  const state = managerBatchState;
  if (!state || state.submitting || !isSessionCurrent(state.generation) || !hasRole("manager")) return;
  const error = document.getElementById("manager-batch-error");
  const chosen = state.items.filter(item => state.reviewed.get(item.request_id)?.checked);
  if (!chosen.length || !document.getElementById("manager-batch-confirmed").checked) {
    error.textContent = "Chọn hồ sơ cần xử lý (có thể Chọn tất cả) và xác nhận nội dung trước khi gửi.";
    return;
  }
  const reason = document.getElementById("manager-batch-reason").value.trim();
  if (reason.length < 3) { error.textContent = "Nhập ghi chú ít nhất 3 ký tự."; return; }
  state.submitting = true;
  document.getElementById("manager-batch-tickets").inert = true;
  document.getElementById("manager-batch-reason").readOnly = true;
  error.textContent = "";
  document.getElementById("manager-batch-submit").disabled = true;
  try {
    const data = await api("/management/assistant/batch-decision", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: state.action, reason, confirmed: true, tickets: chosen.map(item => ({ request_id: item.request_id, expected_updated_at: item.updated_at, expected_profile_version: item.resident_profile?.profile_version, reviewed: true })) }) });
    if (!isSessionCurrent(state.generation) || managerBatchState !== state) return;
    state.submitting = false;
    closeManagerBatch();
    managerAgentMessage("assistant", `Đã cập nhật ${data.updated} ticket. Hồ sơ chưa chọn được giữ nguyên; mỗi ticket có lịch sử xử lý riêng.`);
    await loadRequests();
  } catch (failure) {
    if (isSessionCurrent(state.generation) && managerBatchState === state) error.textContent = ({ batch_ticket_changed: "Có ticket vừa được cập nhật. Chưa cập nhật hồ sơ nào; hủy và xem lại nhóm mới.", batch_requires_individual_review: "Có trường hợp cần kiểm tra riêng. Chưa cập nhật hồ sơ nào.", batch_not_comparable: "Các hồ sơ không còn cùng điều kiện. Chưa cập nhật hồ sơ nào." })[failure.message] || "Chưa xác nhận được kết quả. Kiểm tra lại hàng đợi trước khi thử gửi lại.";
  } finally {
    state.submitting = false;
    if (!managerBatchState || managerBatchState === state) {
      document.getElementById("manager-batch-tickets").inert = false;
      document.getElementById("manager-batch-reason").readOnly = false;
      updateManagerBatchSelection();
    }
  }
}

function updateManagerBatchSelection() {
  const state = managerBatchState;
  const chosen = state ? [...state.reviewed.values()].filter(checkbox => checkbox.checked).length : 0;
  document.getElementById("manager-batch-count").textContent = `Đã chọn ${chosen} / ${state?.items.length || 0} hồ sơ`;
  document.getElementById("manager-batch-submit").disabled = !chosen || Boolean(state?.submitting);
  document.getElementById("manager-batch-confirmed").checked = false;
}

for (const [id, checked] of [["manager-batch-select-all", true], ["manager-batch-clear", false]]) {
  document.getElementById(id).addEventListener("click", () => {
    if (!managerBatchState || managerBatchState.submitting) return;
    managerBatchState.reviewed.forEach(checkbox => { checkbox.checked = checked; });
    updateManagerBatchSelection();
  });
}

document.getElementById("manager-batch-form").addEventListener("submit", submitManagerBatch);
document.getElementById("manager-batch-cancel").addEventListener("click", closeManagerBatch);
document.getElementById("manager-batch-close").addEventListener("click", closeManagerBatch);
document.getElementById("manager-batch-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeManagerBatch(); });
document.getElementById("manager-agent-unselect").addEventListener("click", () => {
  managerAgentSelectedId = null;
  updateManagerAgentSelection();
});
document.querySelectorAll("[data-manager-prompt]").forEach((button) => button.addEventListener("click", () => {
  managerAgentSelectedId = null;
  updateManagerAgentSelection();
  sendManagerAgentMessage(button.dataset.managerPrompt);
}));
document.getElementById("manager-agent-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.getElementById("manager-agent-input");
  if (managerAgentRequest) return;
  const value = input.value;
  if (value.trim()) { input.value = ""; sendManagerAgentMessage(value); }
});
document.getElementById("manager-agent-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    document.getElementById("manager-agent-form").requestSubmit();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && managerAgentDialog.open && !document.getElementById("manager-batch-dialog").open && !document.getElementById("decision-dialog").open) {
    event.preventDefault();
    closeManagementAgent();
  }
});
configureManagementAgent();
