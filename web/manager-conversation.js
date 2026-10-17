const managerConversation = { id: null, revision: 0, proposal: null };

function resetManagerConversation() {
  managerConversation.id = null;
  managerConversation.revision = 0;
  managerConversation.proposal = null;
  managerAgentMessages.querySelectorAll("[data-conversation-control]").forEach(control => { control.disabled = true; });
}

function managerConversationPayload() {
  managerConversation.id ||= crypto.randomUUID().replaceAll("-", "");
  return { conversation_id: managerConversation.id, expected_revision: managerConversation.revision };
}

function acceptManagerConversation(data) {
  if (!data.conversation) return;
  if (managerConversation.id && (data.conversation.id !== managerConversation.id || data.conversation.revision < managerConversation.revision)) return false;
  managerConversation.id = data.conversation.id;
  managerConversation.revision = data.conversation.revision;
  managerConversation.proposal = data.proposal || null;
  managerAgentMessages.querySelectorAll("[data-conversation-control]").forEach(control => { control.disabled = true; });
}

async function managerConversationOperation(path, payload) {
  if (!authReady || !hasRole("manager") || managerAgentRequest) return;
  const generation = authGeneration;
  const controller = new AbortController();
  managerAgentRequest = controller;
  setManagerAgentBusy(true);
  try {
    const data = await api(path, { method: "POST", signal: controller.signal, cache: "no-store",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    if (controller.signal.aborted || !isSessionCurrent(generation)) return;
    if (acceptManagerConversation(data) === false) return;
    renderManagerConversation(data);
    if (data.execution) await loadRequests();
  } catch (error) {
    if (!controller.signal.aborted && isSessionCurrent(generation)) {
      await recoverManagerConversation(error);
      managerAgentMessage("assistant", apiErrorLabels[error.message] || "Chưa xác nhận được kết quả. Nếu kết nối bị ngắt, có thể gửi lại cùng xác nhận; hệ thống không thực hiện trùng.");
    }
  } finally {
    if (managerAgentRequest === controller) { managerAgentRequest = null; setManagerAgentBusy(false); }
  }
}

async function recoverManagerConversation(error) {
  if (error.message === "conversation_expired") resetManagerConversation();
  if (["conversation_changed", "review_changed", "review_expired"].includes(error.message)) {
    const generation = authGeneration;
    const id = managerConversation.id;
    managerConversation.proposal = null;
    managerAgentMessages.querySelectorAll("[data-conversation-control]").forEach(control => { control.disabled = true; });
    if (!id) return;
    try {
      const data = await api(`/management/assistant/conversations/${encodeURIComponent(id)}`, { cache: "no-store" });
      if (isSessionCurrent(generation) && managerConversation.id === id) acceptManagerConversation(data);
    } catch {
      if (isSessionCurrent(generation) && managerConversation.id === id) resetManagerConversation();
    }
  }
}

function conversationButton(parent, label, handler) {
  const button = managerAgentButton(parent, label, handler);
  button.dataset.conversationControl = "true";
  return button;
}

function renderManagerConversation(data) {
  const message = managerAgentMessage("assistant", data.answer, data);
  message.classList.add("manager-agent-conversation");
  if (data.execution) {
    const list = document.createElement("ul");
    for (const item of data.execution.items) {
      const row = document.createElement("li");
      row.textContent = `${item.request_id} — ${item.hidden ? "Đã ẩn ở danh sách BQL" : requestStatusLabels[item.status] || item.status}`;
      list.append(row);
    }
    message.append(list);
    if (data.duplicate) {
      const note = document.createElement("p");
      note.textContent = "Đây là kết quả đã thực hiện trước đó; không cập nhật lại hoặc gửi thêm thông báo.";
      message.append(note);
    }
    return;
  }
  const view = data.view;
  if (view?.awaiting_delete_statuses) {
    const choices = document.createElement("fieldset");
    choices.className = "manager-conversation-delete-options";
    const legend = document.createElement("legend");
    legend.textContent = "Chọn nhóm đề xuất xóa";
    choices.append(legend);
    const selected = [];
    for (const label of ["Hoàn tất", "Đã hủy", "Từ chối"]) {
      const row = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.conversationControl = "true";
      row.append(checkbox, document.createTextNode(label));
      choices.append(row);
      selected.push({ checkbox, label });
    }
    const submit = conversationButton(choices, "Xem đề xuất", () => {
      if (!managerAgentRequest) void sendManagerAgentMessage(`Đề xuất xóa ${selected.filter(item => item.checkbox.checked).map(item => item.label).join(" và ")}`);
    });
    submit.disabled = true;
    selected.forEach(item => item.checkbox.addEventListener("change", () => { submit.disabled = !selected.some(choice => choice.checkbox.checked); }));
    message.append(choices);
    return;
  }
  const selectedIds = new Set(data.proposal?.request_ids || []);
  for (const [index, group] of (view?.groups || []).entries()) {
    const section = document.createElement("section");
    section.className = "manager-agent-ticket manager-agent-group";
    const title = document.createElement("h3");
    title.textContent = `Nhóm ${index + 1} · ${group.title} · ${group.site_name}`;
    const total = document.createElement("p");
    total.textContent = `Trang này: ${group.items.length} / ${group.total} hồ sơ trong nhóm.`;
    section.append(title, total);
    for (const item of group.items) {
      const row = document.createElement("article");
      row.className = "manager-conversation-ticket";
      const heading = document.createElement("strong");
      heading.textContent = `${item.ordinal}. ${item.request_id} · ${item.full_name} · căn ${item.apartment}`;
      const status = document.createElement("p");
      status.textContent = `${item.title} — ${item.status_label}${item.excluded ? " · Đã tách khỏi lựa chọn" : ""}`;
      row.append(heading, status);
      const summary = document.createElement("p");
      summary.textContent = Object.values(item.summary || {}).join(" · ");
      if (summary.textContent) row.append(summary);
      const notes = document.createElement("p");
      notes.className = "manager-conversation-notes";
      notes.textContent = item.blocked || item.facts?.join("; ") || "Chưa phát hiện khác biệt theo quy tắc; BQL vẫn cần kiểm tra nội dung thực tế.";
      row.append(notes);
      if (data.proposal && !item.excluded && !item.blocked) {
        const label = document.createElement("label");
        label.className = "manager-conversation-select";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selectedIds.has(item.request_id);
        checkbox.dataset.conversationControl = "true";
        checkbox.addEventListener("change", () => {
          if (managerAgentRequest) return;
          if (checkbox.checked) selectedIds.add(item.request_id); else selectedIds.delete(item.request_id);
          if (!selectedIds.size) { void sendManagerAgentMessage("Hủy đề xuất"); return; }
          managerConversation.proposal = null;
          void managerConversationOperation("/management/assistant/proposal/preview", {
            ...managerConversationPayload(), action: data.proposal.action, request_ids: [...selectedIds],
          });
        });
        label.append(checkbox, document.createTextNode("Có trong danh sách xác nhận"));
        row.append(label);
      }
      conversationButton(row, "Mở ticket xử lý thủ công", () => openManagerAgentQueue({ query: item.request_id, status: item.status }));
      if (!item.excluded) conversationButton(row, "Tách khỏi nhóm đang xử lý", () => void sendManagerAgentMessage(`Bỏ hồ sơ số ${item.ordinal}`));
      section.append(row);
    }
    message.append(section);
  }
  if (view && !view.awaiting_delete_statuses) {
    const page = document.createElement("p");
    page.textContent = `Trang ${view.page + 1} · ${view.total} hồ sơ phù hợp. Mỗi lần xác nhận tối đa 20 ticket trên trang đang xem.`;
    message.append(page);
    const navigation = document.createElement("div");
    navigation.className = "manager-agent-actions";
    if (view.page > 0) conversationButton(navigation, "Trang trước", () => void sendManagerAgentMessage("Trang trước"));
    if (view.has_more) conversationButton(navigation, "Trang tiếp", () => void sendManagerAgentMessage("Trang tiếp"));
    message.append(navigation);
  }
  if (data.proposal) {
    const panel = document.createElement("section");
    panel.className = "manager-conversation-confirm";
    const heading = document.createElement("strong");
    heading.textContent = `${data.proposal.label}: ${data.proposal.count} ticket`;
    panel.append(heading);
    let actualCompletion = null;
    if (data.proposal.action === "complete") {
      const label = document.createElement("label");
      actualCompletion = document.createElement("input");
      actualCompletion.type = "checkbox";
      actualCompletion.dataset.conversationControl = "true";
      label.append(actualCompletion, document.createTextNode("Tôi xác nhận công việc của các hồ sơ đã xong trên thực tế."));
      panel.append(label);
    }
    const confirm = conversationButton(panel, `Xác nhận ${data.proposal.label.toLowerCase()}`, () => {
      if (managerConversation.proposal?.review_token !== data.proposal.review_token || managerAgentRequest) return;
      void managerConversationOperation("/management/assistant/proposal/confirm", {
        review_token: data.proposal.review_token, confirmed: true, actual_completion: actualCompletion?.checked || false,
      });
    });
    if (actualCompletion) {
      confirm.disabled = true;
      actualCompletion.addEventListener("change", () => { confirm.disabled = !actualCompletion.checked; });
    }
    const help = document.createElement("p");
    help.textContent = `Hoặc nhắn: “${data.proposal.confirmation_text}”. Chỉ áp dụng cho danh sách vừa xem.`;
    panel.append(help);
    conversationButton(panel, "Hủy đề xuất", () => void sendManagerAgentMessage("Hủy đề xuất"));
    message.append(panel);
  }
  managerAgentMessages.scrollTop = managerAgentMessages.scrollHeight;
}

Object.assign(apiErrorLabels, {
  conversation_changed: "Danh sách lựa chọn đã thay đổi. Hãy nhắn rà soát lại trước khi xác nhận.",
  conversation_expired: "Ngữ cảnh đã hết hạn sau 30 phút. Hãy bắt đầu lại yêu cầu, không có thao tác nào tự chạy.",
  actual_completion_confirmation_required: "Cần xác nhận công việc đã hoàn tất trên thực tế.",
  proposal_ticket_not_eligible: "Có ticket chưa đủ điều kiện thực hiện. Hãy rà soát lại.",
  invalid_proposal_selection: "Lựa chọn không thuộc danh sách đang xem hoặc đã được tách ra.",
});
